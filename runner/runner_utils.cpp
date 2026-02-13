#include "runner_utils.h"
#include "cmd_run.h"
#include "machina/selector_gpu.h"
#include "machina/json_mini.h"
#include "machina/hash.h"
#include "machina/gpu_context.h"

#include <algorithm>
#include <array>
#include <cctype>
#include <random>
#include <iostream>

namespace machina {

static void warn_sensitive_root(const std::filesystem::path& root) {
    static const std::vector<std::string> sensitive = {"/", "/etc", "/usr", "/var", "/home", "/root", "/tmp"};
    try {
        auto canon = std::filesystem::weakly_canonical(root);
        for (const auto& s : sensitive) {
            if (canon == std::filesystem::path(s)) {
                std::cerr << "[WARN] MACHINA_ROOT points to sensitive directory: " << canon << "\n";
                std::cerr << "[WARN] This is strongly discouraged for production use.\n";
                break;
            }
        }
    } catch (...) {
        // best-effort: skip warning if canonicalization fails
    }
}

std::filesystem::path resolve_root(const char* argv0) {
    if (const char* e = std::getenv("MACHINA_ROOT")) {
        std::filesystem::path p = e;
        if (std::filesystem::exists(p)) {
            auto result = std::filesystem::canonical(p);
            warn_sensitive_root(result);
            return result;
        }
    }
    std::filesystem::path exe = argv0 ? std::filesystem::path(argv0) : std::filesystem::path();
    if (!exe.empty() && !exe.is_absolute()) exe = std::filesystem::absolute(exe);
    try {
        if (!exe.empty() && std::filesystem::exists(exe)) exe = std::filesystem::canonical(exe);
    } catch (...) {
        // keep best-effort path
    }
    std::filesystem::path dir = exe.empty() ? std::filesystem::current_path() : exe.parent_path();
    // walk up looking for a repo root (toolpacks directory)
    for (int i = 0; i < 8; i++) {
        if (std::filesystem::exists(dir / "toolpacks")) {
            warn_sensitive_root(dir);
            return dir;
        }
        if (!dir.has_parent_path()) break;
        dir = dir.parent_path();
    }
    auto result = std::filesystem::current_path();
    warn_sensitive_root(result);
    return result;
}

void set_env_if_missing(const char* key, const std::string& value) {
    if (std::getenv(key) != nullptr) return;
#ifdef _WIN32
    _putenv_s(key, value.c_str());
#else
    setenv(key, value.c_str(), 0);
#endif
}

std::string slurp(const std::string& path) {
    std::ifstream f(path);
    if (!f) throw std::runtime_error("cannot open: " + path);
    std::stringstream ss; ss << f.rdbuf();
    return ss.str();
}

std::string json_escape(const std::string& s) {
    return json_mini::json_escape(s);
}

std::string shallow_merge_json_objects(const std::string& base_json, const std::string& patch_json) {
    auto bdoc = machina::json_mini::parse(base_json);
    json_object* base_obj = nullptr;
    bool base_owned = false;
    if (bdoc && json_object_is_type(bdoc.root, json_type_object)) {
        base_obj = bdoc.root;
    } else {
        base_obj = json_object_new_object();
        base_owned = true;
    }

    auto pdoc = machina::json_mini::parse(patch_json);
    if (!pdoc || !json_object_is_type(pdoc.root, json_type_object)) {
        std::string out = json_object_to_json_string_ext(base_obj, JSON_C_TO_STRING_PLAIN);
        if (base_owned) json_object_put(base_obj);
        return out;
    }

    json_object_object_foreach(pdoc.root, key, val) {
        json_object_object_add(base_obj, key, json_object_get(val));
    }

    std::string out = json_object_to_json_string_ext(base_obj, JSON_C_TO_STRING_PLAIN);
    if (base_owned) json_object_put(base_obj);
    return out;
}

std::string safe_merge_patch(const std::string& base_json,
                             const std::string& patch_json,
                             const std::vector<std::string>& blocked_prefixes,
                             const std::vector<std::string>& allowed_keys) {
    auto bdoc = machina::json_mini::parse(base_json);
    json_object* base_obj = nullptr;
    bool base_owned = false;
    if (bdoc && json_object_is_type(bdoc.root, json_type_object)) {
        base_obj = bdoc.root;
    } else {
        base_obj = json_object_new_object();
        base_owned = true;
    }

    auto pdoc = machina::json_mini::parse(patch_json);
    if (!pdoc || !json_object_is_type(pdoc.root, json_type_object)) {
        std::string out = json_object_to_json_string_ext(base_obj, JSON_C_TO_STRING_PLAIN);
        if (base_owned) json_object_put(base_obj);
        return out;
    }

    int blocked_count = 0;
    json_object_object_foreach(pdoc.root, key, val) {
        std::string k = key;
        // Check blocked prefixes
        bool is_blocked = false;
        for (const auto& prefix : blocked_prefixes) {
            if (k.rfind(prefix, 0) == 0) {
                is_blocked = true;
                break;
            }
        }
        if (is_blocked) {
            blocked_count++;
            continue;
        }
        // Check allowed_keys whitelist (if non-empty)
        if (!allowed_keys.empty()) {
            bool in_whitelist = false;
            for (const auto& ak : allowed_keys) {
                if (k == ak) { in_whitelist = true; break; }
            }
            if (!in_whitelist) {
                blocked_count++;
                continue;
            }
        }
        json_object_object_add(base_obj, key, json_object_get(val));
    }
    if (blocked_count > 0) {
        std::cerr << "[safe_merge_patch] blocked " << blocked_count << " key(s) from input patch\n";
    }

    std::string out = json_object_to_json_string_ext(base_obj, JSON_C_TO_STRING_PLAIN);
    if (base_owned) json_object_put(base_obj);
    return out;
}

static uint64_t fnv1a64_update(uint64_t h, const uint8_t* data, size_t n) {
    for (size_t i = 0; i < n; i++) {
        h ^= data[i];
        h *= 1099511628211ULL;
    }
    return h;
}

std::optional<std::string> fingerprint_file_fnv1a64(const std::filesystem::path& p) {
    std::ifstream f(p, std::ios::binary);
    if (!f) return std::nullopt;

    uint64_t h = 1469598103934665603ULL;
    std::array<uint8_t, 1 << 15> buf{};
    while (true) {
        f.read(reinterpret_cast<char*>(buf.data()), (std::streamsize)buf.size());
        std::streamsize got = f.gcount();
        if (got > 0) h = fnv1a64_update(h, buf.data(), (size_t)got);
        if (!f) break;
    }
    return std::string("fnv1a64:") + machina::hash::hex64(h);
}

std::filesystem::path resolve_path_for_replay(const std::string& input_path,
                                               const std::filesystem::path& request_dir,
                                               const std::filesystem::path& root) {
    std::filesystem::path p = input_path;
    if (p.is_absolute()) return p;
    std::filesystem::path cand = request_dir / p;
    if (std::filesystem::exists(cand)) return cand;
    cand = root / p;
    if (std::filesystem::exists(cand)) return cand;
    return std::filesystem::absolute(p);
}

std::string gpu_signature() {
    auto ctx = machina::GpuContext::create();
    std::ostringstream oss;
    oss << ctx.backend() << "|avail=" << (ctx.available() ? 1 : 0)
        << "|count=" << ctx.device_count()
        << "|idx=" << ctx.device_index();
    return oss.str();
}

std::map<std::string, std::string> compute_replay_inputs(const machina::ToolDesc& td,
                                                          const std::string& inputs_json,
                                                          const std::filesystem::path& request_dir,
                                                          const std::filesystem::path& root) {
    std::map<std::string, std::string> out;
    for (const auto& key : td.replay_inputs) {
        if (key == "input_path_fingerprint") {
            auto p = machina::json_mini::get_string(inputs_json, "input_path");
            if (!p || p->empty()) {
                out[key] = "missing:input_path";
            } else {
                auto resolved = resolve_path_for_replay(*p, request_dir, root);
                auto fp = fingerprint_file_fnv1a64(resolved);
                out[key] = fp.value_or("unavailable:fingerprint");
            }
        } else if (key == "gpu_signature") {
            out[key] = gpu_signature();
        } else if (key == "path_fingerprint") {
            auto pth = machina::json_mini::get_string(inputs_json, "path");
            if (!pth || pth->empty()) {
                out[key] = "missing:path";
            } else {
                auto resolved = resolve_path_for_replay(*pth, request_dir, root);
                auto fp = fingerprint_file_fnv1a64(resolved);
                out[key] = fp.value_or("unavailable:fingerprint");
            }
        } else {
            out[key] = "unhandled:" + key;
        }
    }
    return out;
}

std::string replay_inputs_to_json(const machina::ToolDesc& td,
                                   const std::string& inputs_json,
                                   const std::filesystem::path& request_dir,
                                   const std::filesystem::path& root) {
    auto m = compute_replay_inputs(td, inputs_json, request_dir, root);
    std::ostringstream oss;
    oss << "{";
    bool first = true;
    for (const auto& kv : m) {
        if (!first) oss << ",";
        first = false;
        oss << "\"" << json_escape(kv.first) << "\":\"" << json_escape(kv.second) << "\"";
    }
    oss << "}";
    return oss.str();
}

std::string json_array_compact(const std::vector<std::string>& items) {
    std::ostringstream oss;
    oss << "[";
    for (size_t i=0;i<items.size();i++){
        if (i) oss << ",";
        oss << "\"" << json_escape(items[i]) << "\"";
    }
    oss << "]";
    return oss.str();
}

std::string gen_run_id() {
    const char* det = std::getenv("MACHINA_DETERMINISTIC_RUN_ID");

    uint64_t seed = 0;
    if (det && std::string(det) == "1") {
        seed = 1234567ULL;
    } else {
        uint64_t t = (uint64_t)std::chrono::high_resolution_clock::now().time_since_epoch().count();
        uint64_t r = 0;
        try {
            std::random_device rd;
            r = ((uint64_t)rd() << 32) ^ (uint64_t)rd();
        } catch (...) {
            r = 0x9e3779b97f4a7c15ULL;
        }
        seed = t ^ r;
    }

    std::mt19937_64 rng{seed};
    uint64_t a = rng();
    uint64_t b = rng();
    std::ostringstream oss;
    oss << std::hex << a << b;
    return oss.str();
}

Menu build_menu_from_registry(const Registry& reg, const std::vector<std::string>& tags) {
    auto tools = reg.queryByTags(tags);
    Menu menu;
    uint16_t sid = 1;
    for (const auto& t : tools) {
        MenuItem mi;
        mi.sid.value = sid++;
        mi.aid = t.aid;
        mi.name = t.name;
        mi.tags = t.tags;
        menu.items.push_back(mi);
    }
    auto noop = reg.getTool("AID.NOOP.v1");
    if (noop) {
        MenuItem mi;
        mi.sid.value = sid++;
        mi.aid = noop->aid;
        mi.name = noop->name;
        mi.tags = noop->tags;
        menu.items.push_back(mi);
    }
    menu.buildIndex();
    return menu;
}

static bool aid_matches_pattern(const std::string& aid, const std::string& pattern) {
    if (pattern.empty()) return false;
    if (pattern.back() == '*') {
        std::string prefix = pattern.substr(0, pattern.size() - 1);
        return aid.rfind(prefix, 0) == 0;
    }
    return aid == pattern;
}

static bool aid_matches_any(const std::string& aid, const std::vector<std::string>& patterns) {
    for (const auto& p : patterns) {
        if (aid_matches_pattern(aid, p)) return true;
    }
    return false;
}

Menu filter_menu_by_capabilities(const Menu& menu,
                                 const std::vector<std::string>& allowed,
                                 const std::vector<std::string>& blocked) {
    Menu out;
    int filtered = 0;
    for (const auto& mi : menu.items) {
        // If blocked list is present, exclude matching AIDs
        if (!blocked.empty() && aid_matches_any(mi.aid, blocked)) {
            filtered++;
            continue;
        }
        // If allowed list is present, only include matching AIDs
        if (!allowed.empty() && !aid_matches_any(mi.aid, allowed)) {
            filtered++;
            continue;
        }
        out.items.push_back(mi);
    }
    if (filtered > 0) {
        std::cerr << "[capabilities] filtered " << filtered << " tool(s) from menu\n";
    }
    out.buildIndex();
    return out;
}

ControlMode parse_mode(const std::string& s) {
    if (s == "SHADOW_POLICY") return ControlMode::SHADOW_POLICY;
    if (s == "BLENDED") return ControlMode::BLENDED;
    if (s == "POLICY_ONLY") return ControlMode::POLICY_ONLY;
    return ControlMode::FALLBACK_ONLY;
}

std::unique_ptr<ISelector> make_selector(const std::string& backend, const std::filesystem::path& repo_root) {
    std::unique_ptr<ISelector> base;
    if (backend == "GPU_CENTROID") base = std::make_unique<GpuCentroidSelector>();
    else base = std::make_unique<HeuristicSelector>();

    if (const char* cmd = std::getenv("MACHINA_POLICY_CMD")) {
        std::string scmd = cmd;
        if (!scmd.empty()) {
            return std::make_unique<ExternalProcessSelector>(std::move(base), repo_root, scmd);
        }
    }
    return base;
}

int64_t now_ms_i64() {
    using namespace std::chrono;
    return (int64_t)duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

void sleep_ms(int ms) {
    std::this_thread::sleep_for(std::chrono::milliseconds(ms));
}

bool ends_with(const std::string& s, const std::string& suf) {
    return s.size() >= suf.size() && s.compare(s.size()-suf.size(), suf.size(), suf) == 0;
}

std::unordered_set<std::string> list_run_logs(const std::filesystem::path& log_dir) {
    std::unordered_set<std::string> out;
    std::error_code ec;
    if (!std::filesystem::exists(log_dir, ec)) return out;
    for (auto& e : std::filesystem::directory_iterator(log_dir, ec)) {
        if (ec) break;
        if (!e.is_regular_file(ec)) continue;
        auto name = e.path().filename().string();
        if (name.rfind("run_", 0) == 0 && name.find(".jsonl") != std::string::npos) out.insert(name);
    }
    return out;
}

std::optional<std::filesystem::path> newest_new_log(const std::filesystem::path& log_dir,
                                                    const std::unordered_set<std::string>& before) {
    std::optional<std::filesystem::path> best;
    std::filesystem::file_time_type best_t{};
    std::error_code ec;
    if (!std::filesystem::exists(log_dir, ec)) return std::nullopt;
    for (auto& e : std::filesystem::directory_iterator(log_dir, ec)) {
        if (ec) break;
        if (!e.is_regular_file(ec)) continue;
        auto name = e.path().filename().string();
        if (before.find(name) != before.end()) continue;
        auto t = e.last_write_time(ec);
        if (ec) continue;
        if (!best || t > best_t) { best = e.path(); best_t = t; }
    }
    return best;
}

std::filesystem::path default_queue_dir(const std::filesystem::path& root) {
    return root / "work" / "queue";
}

void ensure_queue_dirs(const std::filesystem::path& q) {
    std::error_code ec;
    std::filesystem::create_directories(q / "inbox", ec);
    std::filesystem::create_directories(q / "processing", ec);
    std::filesystem::create_directories(q / "retry", ec);
    std::filesystem::create_directories(q / "done", ec);
    std::filesystem::create_directories(q / "failed", ec);
    std::filesystem::create_directories(q / "dlq", ec);
    std::filesystem::create_directories(q / "out", ec);
    std::filesystem::create_directories(q / "tmp", ec);
}

std::string slurp_file(const std::filesystem::path& p) {
    std::ifstream f(p.string(), std::ios::binary);
    if (!f) return "";
    std::ostringstream oss;
    oss << f.rdbuf();
    return oss.str();
}

int64_t getenv_i64(const char* k, int64_t defv) {
    if (const char* e = std::getenv(k)) {
        try { return std::stoll(e); } catch (...) { return defv; }
    }
    return defv;
}

namespace runner_detail {
int getenv_int(const char* k, int defv) {
    if (const char* e = std::getenv(k)) {
        try { return std::stoi(e); } catch (...) { return defv; }
    }
    return defv;
}
} // namespace runner_detail

bool parse_retry_name(const std::string& fname, int64_t& due_ms, std::string& rest_name) {
    due_ms = 0;
    rest_name.clear();
    if (fname.rfind("retry_", 0) != 0) return false;
    size_t p1 = 6;
    size_t p2 = fname.find('_', p1);
    if (p2 == std::string::npos) return false;
    try {
        due_ms = std::stoll(fname.substr(p1, p2 - p1));
    } catch (...) {
        return false;
    }
    if (due_ms < 0) return false;
    rest_name = fname.substr(p2 + 1);
    if (rest_name.empty()) return false;
    return true;
}

int64_t parse_due_from_filename(const std::filesystem::path& p) {
    auto s = p.filename().string();
    if (s.rfind("retry_", 0) != 0) return -1;
    auto u = s.find('_', 6);
    if (u == std::string::npos) return -1;
    auto num = s.substr(6, u - 6);
    try { return std::stoll(num); } catch (...) { return -1; }
}

int64_t extract_next_run_at(const std::string& json) {
    auto q = machina::json_mini::get_object_raw(json, "_queue").value_or("");
    if (q.empty()) return -1;
    auto v = machina::json_mini::get_int(q, "next_run_at_ms");
    return v.value_or(-1);
}

void move_due_retries(const std::filesystem::path& retry_dir, const std::filesystem::path& inbox_dir) {
    std::error_code ec;
    if (!std::filesystem::exists(retry_dir, ec)) return;
    int64_t now = now_ms_i64();
    for (auto& e : std::filesystem::directory_iterator(retry_dir, ec)) {
        if (ec) break;
        if (!e.is_regular_file(ec)) continue;
        auto p = e.path();
        if (p.extension() != ".json") continue;
        int64_t due = parse_due_from_filename(p);
        if (due < 0) {
            auto js = slurp_file(p);
            due = extract_next_run_at(js);
        }
        if (due < 0 || due <= now) {
            std::string fname = p.filename().string();
            std::string rest;
            int64_t due2 = 0;
            if (parse_retry_name(fname, due2, rest)) {
                fname = rest;
            }
            auto dst = inbox_dir / fname;
            if (std::filesystem::exists(dst, ec)) {
                auto stem = dst.stem().string();
                auto ext = dst.extension().string();
                dst = inbox_dir / (stem + "_" + std::to_string(now_ms_i64()) + ext);
            }
            std::filesystem::rename(p, dst, ec);
            if (ec) {
                ec.clear();
            }
        }
    }
}

std::string patch_queue_meta_for_retry(const std::string& req_json,
                                       int attempt,
                                       int max_attempts,
                                       int64_t next_run_at_ms,
                                       const std::string& last_error) {
    std::string base = req_json;
    if (base.find("\"_queue\"") != std::string::npos) {
        size_t k = base.find("\"_queue\"");
        size_t colon = base.find(':', k);
        if (colon != std::string::npos) {
            size_t p = colon + 1;
            while (p < base.size() && (base[p] == ' ' || base[p] == '\t' || base[p] == '\n' || base[p] == '\r')) p++;
            if (p < base.size() && base[p] == '{') {
                int depth = 0;
                size_t end = p;
                for (; end < base.size(); end++) {
                    if (base[end] == '{') depth++;
                    else if (base[end] == '}') {
                        depth--;
                        if (depth == 0) { end++; break; }
                    }
                }
                if (end > p && end <= base.size()) {
                    size_t rm_start = k;
                    size_t rm_end = end;
                    while (rm_end < base.size() && (base[rm_end] == ' ' || base[rm_end] == '\t' || base[rm_end] == '\n' || base[rm_end] == '\r')) rm_end++;
                    if (rm_end < base.size() && base[rm_end] == ',') rm_end++;
                    if (rm_start > 0) {
                        size_t b = rm_start;
                        while (b > 0 && (base[b-1] == ' ' || base[b-1] == '\t' || base[b-1] == '\n' || base[b-1] == '\r')) b--;
                        if (b > 0 && base[b-1] == ',') rm_start = b-1;
                    }
                    base.erase(rm_start, rm_end - rm_start);
                }
            }
        }
    }

    auto pos = base.rfind('}');
    if (pos == std::string::npos) return req_json;
    std::ostringstream q;
    q << "\"_queue\":{";
    q << "\"attempt\":" << attempt << ",";
    q << "\"max_attempts\":" << max_attempts << ",";
    q << "\"next_run_at_ms\":" << next_run_at_ms << ",";
    q << "\"last_error\":\"" << json_escape(last_error) << "\"";
    q << "}";

    bool has_other = base.find(':') != std::string::npos;
    std::string insert = (has_other ? "," : "") + q.str();
    base.insert(pos, insert);
    return base;
}

int parse_priority_prefix(const std::string& fname, int defv) {
    if (fname.size() >= 6 && fname[0] == 'p' &&
        std::isdigit((unsigned char)fname[1]) && std::isdigit((unsigned char)fname[2]) &&
        std::isdigit((unsigned char)fname[3]) && std::isdigit((unsigned char)fname[4]) &&
        fname[5] == '_') {
        try {
            int v = std::stoi(fname.substr(1, 4));
            if (v < 0) v = 0;
            if (v > 9999) v = 9999;
            return v;
        } catch (...) {}
    }
    return defv;
}

std::vector<std::filesystem::path> list_inbox_json(const std::filesystem::path& inbox) {
    std::vector<std::filesystem::path> v;
    std::error_code ec;
    if (!std::filesystem::exists(inbox, ec)) return v;
    for (auto& e : std::filesystem::directory_iterator(inbox, ec)) {
        if (ec) break;
        if (!e.is_regular_file(ec)) continue;
        auto p = e.path();
        if (p.extension() == ".json") v.push_back(p);
    }
    std::sort(v.begin(), v.end(), [](const std::filesystem::path& a, const std::filesystem::path& b) {
        int pa = parse_priority_prefix(a.filename().string());
        int pb = parse_priority_prefix(b.filename().string());
        if (pa != pb) return pa < pb;
        return a.filename().string() < b.filename().string();
    });
    return v;
}

std::vector<std::filesystem::path> list_dir_json(const std::filesystem::path& dir) {
    std::vector<std::filesystem::path> v;
    std::error_code ec;
    if (!std::filesystem::exists(dir, ec)) return v;
    for (auto& e : std::filesystem::directory_iterator(dir, ec)) {
        if (ec) break;
        if (!e.is_regular_file(ec)) continue;
        auto p = e.path();
        if (p.extension() == ".json") v.push_back(p);
    }
    std::sort(v.begin(), v.end(), [](const std::filesystem::path& a, const std::filesystem::path& b) {
        int pa = parse_priority_prefix(a.filename().string());
        int pb = parse_priority_prefix(b.filename().string());
        if (pa != pb) return pa < pb;
        return a.filename().string() < b.filename().string();
    });
    return v;
}

bool parse_attempt_from_name(const std::string& name, int& attempt_out) {
    attempt_out = 1;
    auto pos = name.rfind(".a");
    if (pos == std::string::npos) return true;
    auto dot = name.find('.', pos + 2);
    if (dot == std::string::npos) return true;
    std::string num = name.substr(pos + 2, dot - (pos + 2));
    if (num.empty()) return true;
    try {
        int n = std::stoi(num);
        if (n >= 1 && n <= 1000) attempt_out = n;
    } catch (...) {}
    return true;
}

int64_t backoff_delay_ms(int next_attempt,
                         int64_t base_ms,
                         int64_t mult,
                         int64_t max_ms,
                         int64_t jitter_ms) {
    if (base_ms < 0) base_ms = 0;
    if (mult < 1) mult = 1;
    if (max_ms < 0) max_ms = 0;
    if (jitter_ms < 0) jitter_ms = 0;
    int exp = next_attempt - 2;
    if (exp < 0) exp = 0;
    long double d = (long double)base_ms;
    for (int i = 0; i < exp; i++) d *= (long double)mult;
    int64_t delay = (int64_t)d;
    if (delay > max_ms && max_ms > 0) delay = max_ms;
    if (jitter_ms > 0) {
        uint64_t seed = (uint64_t)now_ms_i64();
        seed ^= (seed << 13);
        seed ^= (seed >> 7);
        seed ^= (seed << 17);
        int64_t j = (int64_t)(seed % (uint64_t)(jitter_ms + 1));
        delay += j;
    }
    return delay;
}

std::string write_atomic_json(const std::filesystem::path& dst, const std::string& body) {
    std::error_code ec;
    std::filesystem::create_directories(dst.parent_path(), ec);
    auto tmp = dst;
    tmp += ".tmp";
    {
        std::ofstream f(tmp.string(), std::ios::binary);
        if (!f) return "cannot write";
        f << body;
    }
    std::filesystem::rename(tmp, dst, ec);
    if (ec) {
        std::filesystem::remove(tmp, ec);
        return "rename failed";
    }
    return "";
}

JobResult process_queue_job(const std::filesystem::path& proc_file,
                            const std::string& base_name,
                            char* argv0,
                            const std::filesystem::path& root,
                            const std::filesystem::path& queue_dir) {
    JobResult jr;
    auto retry_dir  = queue_dir / "retry";
    auto done_dir   = queue_dir / "done";
    auto dlq_dir    = queue_dir / "dlq";
    auto out_dir    = queue_dir / "out";
    auto logs_dir   = root / "logs";

    jr.attempt = 1;
    (void)parse_attempt_from_name(base_name, jr.attempt);

    auto before = list_run_logs(logs_dir);

    std::string proc_path = proc_file.string();
    char* av[3];
    av[0] = argv0;
    av[1] = (char*)"run";
    av[2] = (char*)proc_path.c_str();
    jr.exit_code = cmd_run(3, av);

    auto newlog = newest_new_log(logs_dir, before);
    // Use relative path in response to avoid leaking absolute filesystem paths
    if (newlog) {
        auto rel = std::filesystem::relative(*newlog, root);
        jr.log_path = rel.empty() ? newlog->filename().string() : rel.string();

        // Extract per-tool metrics from run log (tool_ok / tool_error events)
        std::ifstream logf(newlog->string());
        if (logf.good()) {
            std::string line;
            while (std::getline(logf, line)) {
                if (line.empty()) continue;
                auto ev = machina::json_mini::get_string(line, "event").value_or("");
                if (ev != "tool_ok" && ev != "tool_error") continue;
                auto payload_raw = machina::json_mini::get_object_raw(line, "payload").value_or("");
                if (payload_raw.empty()) continue;
                auto aid = machina::json_mini::get_string(payload_raw, "aid").value_or("");
                auto dur = (int)machina::json_mini::get_int(payload_raw, "duration_ms").value_or(0);
                if (!aid.empty()) {
                    jr.tool_metrics.push_back({aid, ev == "tool_ok", dur});
                }
            }
        }
    } else {
        jr.log_path = "";
    }

    // Read retry/backoff config from _queue metadata
    const int max_attempts_env = runner_detail::getenv_int("MACHINA_AUTOPILOT_MAX_ATTEMPTS", 5);
    const int64_t backoff_base_env = getenv_i64("MACHINA_AUTOPILOT_BACKOFF_BASE_MS", 2000);
    const int64_t backoff_mult_env = getenv_i64("MACHINA_AUTOPILOT_BACKOFF_MULT", 2);
    const int64_t backoff_max_env = getenv_i64("MACHINA_AUTOPILOT_BACKOFF_MAX_MS", 60000);
    const int64_t backoff_jitter_env = getenv_i64("MACHINA_AUTOPILOT_BACKOFF_JITTER_MS", 250);

    jr.max_attempts = max_attempts_env;
    int64_t backoff_base_ms = backoff_base_env;
    int64_t backoff_mult = backoff_mult_env;
    int64_t backoff_max_ms = backoff_max_env;
    int64_t backoff_jitter_ms = backoff_jitter_env;
    {
        auto req_str = slurp_file(proc_file);
        auto qmeta = machina::json_mini::get_object_raw(req_str, "_queue").value_or("");
        if (!qmeta.empty()) {
            if (auto v = machina::json_mini::get_int(qmeta, "max_attempts")) jr.max_attempts = (int)*v;
            if (auto v = machina::json_mini::get_int(qmeta, "backoff_base_ms")) backoff_base_ms = *v;
            if (auto v = machina::json_mini::get_int(qmeta, "backoff_mult")) backoff_mult = *v;
            if (auto v = machina::json_mini::get_int(qmeta, "backoff_max_ms")) backoff_max_ms = *v;
            if (auto v = machina::json_mini::get_int(qmeta, "backoff_jitter_ms")) backoff_jitter_ms = *v;
        }
    }
    if (jr.max_attempts < 1) jr.max_attempts = 1;

    std::error_code ec;
    if (jr.exit_code == 0) {
        jr.final_path = done_dir / base_name;
        std::filesystem::rename(proc_file, jr.final_path, ec);
    } else {
        if (jr.attempt < jr.max_attempts) {
            int next_attempt = jr.attempt + 1;
            int64_t delay = backoff_delay_ms(next_attempt, backoff_base_ms, backoff_mult, backoff_max_ms, backoff_jitter_ms);
            int64_t due = now_ms_i64() + delay;

            std::string rest = base_name;
            auto a_pos = rest.rfind(".a");
            if (a_pos != std::string::npos) {
                auto dot = rest.find('.', a_pos + 2);
                if (dot != std::string::npos) rest.erase(a_pos, dot - a_pos);
            }
            if (rest.size() >= 5 && rest.substr(rest.size()-5) == ".json") {
                rest = rest.substr(0, rest.size()-5);
            }
            rest += ".a" + std::to_string(next_attempt) + ".json";

            auto req_str = slurp_file(proc_file);
            std::string last_error = std::string("exit_code=") + std::to_string(jr.exit_code) + ";log=" + jr.log_path;
            std::string patched = patch_queue_meta_for_retry(req_str, next_attempt, jr.max_attempts, due, last_error);

            std::string retry_name = std::string("retry_") + std::to_string(due) + "_" + rest;
            std::filesystem::path retry_path = retry_dir / retry_name;
            std::string werr = write_atomic_json(retry_path, patched);
            if (werr.empty()) {
                jr.scheduled_retry = true;
                jr.final_path = retry_path;
                std::filesystem::remove(proc_file, ec);
            } else {
                jr.final_path = dlq_dir / base_name;
                std::filesystem::rename(proc_file, jr.final_path, ec);
                jr.deadletter = true;
            }
        } else {
            jr.final_path = dlq_dir / base_name;
            std::filesystem::rename(proc_file, jr.final_path, ec);
            jr.deadletter = true;
        }
    }
    if (ec) {
        std::cerr << "[process_queue_job] move failed: " << ec.message() << "\n";
    }

    // Write result metadata
    std::ostringstream meta;
    meta << "{";
    meta << "\"ok\":" << (jr.exit_code == 0 ? "true" : "false") << ",";
    meta << "\"exit_code\":" << jr.exit_code << ",";
    {
        auto rel_job = std::filesystem::relative(jr.final_path, root);
        meta << "\"job\":\"" << json_escape(rel_job.empty() ? jr.final_path.filename().string() : rel_job.string()) << "\",";
    }
    meta << "\"log\":\"" << json_escape(jr.log_path) << "\"";
    meta << ",\"attempt\":" << jr.attempt;
    meta << ",\"max_attempts\":" << jr.max_attempts;
    meta << ",\"scheduled_retry\":" << (jr.scheduled_retry ? "true" : "false");
    meta << ",\"deadletter\":" << (jr.deadletter ? "true" : "false");
    meta << "}";
    jr.result_json = meta.str();

    std::string rname = base_name + ".attempt" + std::to_string(jr.attempt) + ".result.json";
    (void)write_atomic_json(out_dir / rname, jr.result_json);

    return jr;
}

} // namespace machina
