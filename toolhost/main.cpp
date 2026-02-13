#include "machina/plugin_loader.h"
#include "machina/plugin_api.h"
#include "machina/state.h"
#include "machina/tools.h"
#include "machina/serialization.h"
#include "machina/lease.h"

#include <json-c/json.h>

#include <chrono>
#include <filesystem>
#include <iostream>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

using namespace machina;

struct ToolEntry {
    ToolDesc desc;
    ToolFnPtr fn{nullptr};
};

struct CollectorRegistrar : public IToolRegistrar {
    std::unordered_map<std::string, ToolEntry> tools;
    void register_tool(const ToolDesc& desc, ToolFnPtr fn) override {
        tools[desc.aid] = ToolEntry{desc, fn};
    }
};

// --- Idempotency Guard ---
// Deduplicates tool executions by idempotency_key within a configurable window.
// Cache entry: {output_json, status_str, expiry_ms}.
struct IdempotencyCacheEntry {
    std::string status;
    std::string output_json;
    std::string error;
    int64_t expiry_ms;
};

static std::unordered_map<std::string, IdempotencyCacheEntry> g_idemp_cache;
static constexpr int64_t IDEMP_DEFAULT_TTL_MS = 60000; // 60 seconds
static constexpr size_t IDEMP_MAX_ENTRIES = 1024;

static int64_t now_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

static void idemp_gc() {
    // Evict expired entries (lazy, on each check)
    if (g_idemp_cache.size() < IDEMP_MAX_ENTRIES / 2) return;
    int64_t now = now_ms();
    for (auto it = g_idemp_cache.begin(); it != g_idemp_cache.end(); ) {
        if (it->second.expiry_ms < now) {
            it = g_idemp_cache.erase(it);
        } else {
            ++it;
        }
    }
}

// Returns true if this key was already processed (cache hit), filling result.
// Returns false if this is a new request (cache miss).
static bool idemp_check(const std::string& key, IdempotencyCacheEntry* out) {
    if (key.empty()) return false;
    int64_t now = now_ms();
    auto it = g_idemp_cache.find(key);
    if (it != g_idemp_cache.end()) {
        if (it->second.expiry_ms > now) {
            if (out) *out = it->second;
            return true;
        }
        // Expired
        g_idemp_cache.erase(it);
    }
    return false;
}

static void idemp_store(const std::string& key, const ToolResult& r, int64_t ttl_ms = 0) {
    if (key.empty()) return;
    if (ttl_ms <= 0) ttl_ms = IDEMP_DEFAULT_TTL_MS;
    idemp_gc();
    g_idemp_cache[key] = {
        stepstatus_to_str(r.status),
        r.output_json,
        r.error,
        now_ms() + ttl_ms,
    };
}

static std::string slurp_stdin() {
    std::string result;
    result.reserve(4096);
    constexpr size_t MAX_STDIN_BYTES = 10ULL * 1024 * 1024;
    char buf[8192];
    while (std::cin.read(buf, sizeof(buf)) || std::cin.gcount()) {
        result.append(buf, (size_t)std::cin.gcount());
        if (result.size() > MAX_STDIN_BYTES) {
            return "{\"error\":\"stdin exceeds 10MB limit\"}";
        }
    }
    return result;
}

static void print_error_json(const std::string& msg, int exit_code=2) {
    std::cout << "{\"ok\":false,\"error\":" << json_quote(msg) << "}";
    std::exit(exit_code);
}

int main(int argc, char** argv) {
    if (argc < 3) {
        std::cerr << "usage:\n  machina_toolhost --list <plugin_path>\n  machina_toolhost --run <plugin_path> <aid>  (reads JSON request from stdin)\n  machina_toolhost --serve <plugin_path>       (persistent NDJSON mode on stdin/stdout)\n";
        return 2;
    }

    const std::string mode = argv[1];
    const std::filesystem::path plugin_path = std::filesystem::path(argv[2]);

    CollectorRegistrar registrar;
    PluginManager pm;
    std::string err;
    if (!pm.load_plugin(plugin_path, &registrar, &err)) {
        print_error_json(err, 3);
    }

    if (mode == "--list") {
        // Emit tool descriptors only.
        json_object* root = json_object_new_object();
        json_object_object_add(root, "ok", json_object_new_boolean(1));
        json_object* arr = json_object_new_array();

        for (const auto& kv : registrar.tools) {
            const ToolDesc& d = kv.second.desc;
            json_object* o = json_object_new_object();
            json_object_object_add(o, "aid", json_object_new_string(d.aid.c_str()));
            json_object_object_add(o, "name", json_object_new_string(d.name.c_str()));
            json_object_object_add(o, "deterministic", json_object_new_boolean(d.deterministic ? 1 : 0));

            json_object* tags = json_object_new_array();
            for (const auto& t : d.tags) json_object_array_add(tags, json_object_new_string(t.c_str()));
            json_object_object_add(o, "tags", tags);

            json_object* se = json_object_new_array();
            for (const auto& t : d.side_effects) json_object_array_add(se, json_object_new_string(t.c_str()));
            json_object_object_add(o, "side_effects", se);

            json_object* ri = json_object_new_array();
            for (const auto& t : d.replay_inputs) json_object_array_add(ri, json_object_new_string(t.c_str()));
            json_object_object_add(o, "replay_inputs", ri);

            json_object_array_add(arr, o);
        }

        json_object_object_add(root, "tools", arr);
        std::cout << json_object_to_json_string_ext(root, JSON_C_TO_STRING_PLAIN);
        json_object_put(root);
        return 0;
    }

    if (mode == "--run") {
        if (argc < 4) {
            print_error_json("missing aid", 2);
        }
        const std::string aid = argv[3];
        auto it = registrar.tools.find(aid);
        if (it == registrar.tools.end() || !it->second.fn) {
            print_error_json("tool not found in plugin: " + aid, 4);
        }

        const std::string req = slurp_stdin();
        json_object* reqj = json_tokener_parse(req.c_str());
        if (!reqj) {
            print_error_json("invalid JSON request on stdin", 5);
        }

        std::string input_json = "{}";
        DSState ds;

        json_object* v = nullptr;
        if (json_object_object_get_ex(reqj, "input_json", &v) && json_object_is_type(v, json_type_string)) {
            input_json = json_object_get_string(v);
        }
        if (json_object_object_get_ex(reqj, "ds_state", &v)) {
            (void)dsstate_from_json(v, &ds);
        }

        ToolResult r = it->second.fn(input_json, ds);

        json_object* out = json_object_new_object();
        json_object_object_add(out, "ok", json_object_new_boolean(1));
        json_object_object_add(out, "status", json_object_new_string(stepstatus_to_str(r.status)));
        json_object_object_add(out, "output_json", json_object_new_string_len(r.output_json.c_str(), (int)r.output_json.size()));
        json_object_object_add(out, "error", json_object_new_string_len(r.error.c_str(), (int)r.error.size()));

        json_object* dsj = dsstate_to_json(ds);
        json_object_object_add(out, "ds_state", dsj);

        std::cout << json_object_to_json_string_ext(out, JSON_C_TO_STRING_PLAIN);

        json_object_put(out);
        json_object_put(reqj);
        return 0;
    }

    if (mode == "--serve") {
        // Persistent serve mode: reads newline-delimited JSON requests from
        // stdin and writes one JSON response line per request to stdout.
        // Each request must include "aid" and optionally "input_json" / "ds_state".
        // Supports delta serialization: incoming ds_state with "delta":true is applied
        // incrementally to the session state; responses also use delta encoding.
        //
        // Idempotency: if "idempotency_key" is present in the request, the result
        // is cached. Duplicate keys within the TTL window return the cached result
        // without re-executing the tool.
        //
        // Lease verification: if MACHINA_LEASE_ENFORCE=1, tools with tier>0
        // require a "_lease_token" in the request. Tokens are single-use and TTL-bound.
        //
        // An empty line or EOF terminates the server.
        DSState session_ds; // maintained across requests for delta tracking
        LeaseManager lease_mgr;
        bool enforce_leases = false;
        {
            const char* el = std::getenv("MACHINA_LEASE_ENFORCE");
            if (el && (std::string(el) == "1" || std::string(el) == "true")) enforce_leases = true;
        }

        std::string line;
        while (std::getline(std::cin, line)) {
            if (line.empty()) break; // graceful shutdown

            json_object* reqj = json_tokener_parse(line.c_str());
            if (!reqj) {
                std::cout << "{\"ok\":false,\"error\":\"invalid JSON\"}\n";
                std::cout.flush();
                continue;
            }

            std::string aid;
            json_object* av = nullptr;
            if (json_object_object_get_ex(reqj, "aid", &av) && json_object_is_type(av, json_type_string)) {
                aid = json_object_get_string(av);
            }
            if (aid.empty()) {
                std::cout << "{\"ok\":false,\"error\":\"missing aid\"}\n";
                std::cout.flush();
                json_object_put(reqj);
                continue;
            }

            // Internal lease management commands (aid="_lease.issue" / "_lease.gc")
            if (aid == "_lease.issue") {
                std::string tool_aid;
                int tier_val = 0;
                int64_t ttl_ms_val = 10000;
                json_object* tv = nullptr;
                if (json_object_object_get_ex(reqj, "tool_aid", &tv) && json_object_is_type(tv, json_type_string))
                    tool_aid = json_object_get_string(tv);
                if (json_object_object_get_ex(reqj, "tier", &tv) && json_object_is_type(tv, json_type_int))
                    tier_val = json_object_get_int(tv);
                if (json_object_object_get_ex(reqj, "ttl_ms", &tv) && json_object_is_type(tv, json_type_int))
                    ttl_ms_val = json_object_get_int64(tv);
                auto tok = lease_mgr.issue_lease(tool_aid, tier_val, ttl_ms_val, "serve");
                std::cout << "{\"ok\":true,\"token_id\":" << json_quote(tok.token_id)
                          << ",\"tool_aid\":" << json_quote(tok.tool_aid)
                          << ",\"tier\":" << tok.tier << "}\n";
                std::cout.flush();
                json_object_put(reqj);
                continue;
            }
            if (aid == "_lease.gc") {
                lease_mgr.gc();
                std::cout << "{\"ok\":true,\"active\":" << lease_mgr.active_count()
                          << ",\"issued\":" << lease_mgr.total_issued()
                          << ",\"consumed\":" << lease_mgr.total_consumed()
                          << ",\"rejected\":" << lease_mgr.total_rejected() << "}\n";
                std::cout.flush();
                json_object_put(reqj);
                continue;
            }

            auto it = registrar.tools.find(aid);
            if (it == registrar.tools.end() || !it->second.fn) {
                std::cout << "{\"ok\":false,\"error\":" << json_quote("tool not found: " + aid) << "}\n";
                std::cout.flush();
                json_object_put(reqj);
                continue;
            }

            // Check idempotency key
            std::string idemp_key;
            json_object* ik = nullptr;
            if (json_object_object_get_ex(reqj, "idempotency_key", &ik) && json_object_is_type(ik, json_type_string)) {
                idemp_key = json_object_get_string(ik);
            }

            IdempotencyCacheEntry cached;
            if (idemp_check(idemp_key, &cached)) {
                // Cache hit — return previous result without re-executing
                json_object* out = json_object_new_object();
                json_object_object_add(out, "ok", json_object_new_boolean(1));
                json_object_object_add(out, "status", json_object_new_string(cached.status.c_str()));
                json_object_object_add(out, "output_json", json_object_new_string_len(cached.output_json.c_str(), (int)cached.output_json.size()));
                json_object_object_add(out, "error", json_object_new_string_len(cached.error.c_str(), (int)cached.error.size()));
                json_object_object_add(out, "idempotent_hit", json_object_new_boolean(1));
                json_object_object_add(out, "ds_state", json_object_new_object()); // empty delta

                std::cout << json_object_to_json_string_ext(out, JSON_C_TO_STRING_PLAIN) << "\n";
                std::cout.flush();

                json_object_put(out);
                json_object_put(reqj);
                continue;
            }

            // Lease verification gate (opt-in via MACHINA_LEASE_ENFORCE=1)
            if (enforce_leases) {
                const auto& td = it->second.desc;
                auto tier = classify_tool_tier(aid, td.side_effects);
                if (lease_mgr.requires_lease(aid, td.side_effects)) {
                    std::string lease_token;
                    json_object* lt = nullptr;
                    if (json_object_object_get_ex(reqj, "_lease_token", &lt) && json_object_is_type(lt, json_type_string)) {
                        lease_token = json_object_get_string(lt);
                    }
                    if (lease_token.empty()) {
                        std::cout << "{\"ok\":false,\"error\":\"lease_required\",\"tier\":" << (int)tier << ",\"aid\":" << json_quote(aid) << "}\n";
                        std::cout.flush();
                        json_object_put(reqj);
                        continue;
                    }
                    std::string reason;
                    if (!lease_mgr.verify_and_consume(lease_token, aid, &reason)) {
                        std::cout << "{\"ok\":false,\"error\":\"lease_rejected\",\"reason\":" << json_quote(reason) << "}\n";
                        std::cout.flush();
                        json_object_put(reqj);
                        continue;
                    }
                }
            }

            std::string input_json = "{}";

            json_object* v = nullptr;
            if (json_object_object_get_ex(reqj, "input_json", &v) && json_object_is_type(v, json_type_string)) {
                input_json = json_object_get_string(v);
            }
            // Apply incoming state (handles both delta and full replacement transparently)
            if (json_object_object_get_ex(reqj, "ds_state", &v)) {
                (void)dsstate_apply_delta(v, &session_ds);
            }

            DSState pre_call = session_ds; // snapshot for delta computation
            ToolResult r = it->second.fn(input_json, session_ds);

            // Store in idempotency cache
            idemp_store(idemp_key, r);

            json_object* out = json_object_new_object();
            json_object_object_add(out, "ok", json_object_new_boolean(1));
            json_object_object_add(out, "status", json_object_new_string(stepstatus_to_str(r.status)));
            json_object_object_add(out, "output_json", json_object_new_string_len(r.output_json.c_str(), (int)r.output_json.size()));
            json_object_object_add(out, "error", json_object_new_string_len(r.error.c_str(), (int)r.error.size()));

            // Emit only changed slots (delta serialization)
            json_object* dsj = dsstate_to_json_delta(session_ds, &pre_call);
            json_object_object_add(out, "ds_state", dsj);

            std::cout << json_object_to_json_string_ext(out, JSON_C_TO_STRING_PLAIN) << "\n";
            std::cout.flush();

            json_object_put(out);
            json_object_put(reqj);
        }
        return 0;
    }

    print_error_json("unknown mode: " + mode, 2);
    return 2;
}
