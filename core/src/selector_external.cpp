#include "machina/selector.h"
#include "machina/proc.h"
#include "machina/crypto.h"

#include <json-c/json.h>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <chrono>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#if defined(__linux__)
  #include <sys/random.h>
#endif

namespace machina {

static std::string trim_ws(std::string s) {
    while (!s.empty() && (s.back()=='\n' || s.back()=='\r' || s.back()==' ' || s.back()=='\t')) s.pop_back();
    size_t i=0;
    while (i<s.size() && (s[i]=='\n' || s[i]=='\r' || s[i]==' ' || s[i]=='\t')) i++;
    if (i) s.erase(0,i);
    return s;
}

static const char* mode_name(ControlMode m) {
    switch (m) {
        case ControlMode::FALLBACK_ONLY: return "FALLBACK_ONLY";
        case ControlMode::POLICY_ONLY: return "POLICY_ONLY";
        case ControlMode::SHADOW_POLICY: return "SHADOW_POLICY";
        case ControlMode::BLENDED: return "BLENDED";
        default: return "UNKNOWN";
    }
}

static std::string lower_ascii(std::string s) {
    for (char& c : s) {
        if (c >= 'A' && c <= 'Z') c = (char)(c - 'A' + 'a');
    }
    return s;
}

static std::vector<std::string> split_csv(const std::string& s) {
    std::vector<std::string> out;
    std::string cur;
    for (char c : s) {
        if (c == ',') {
            if (!cur.empty()) out.push_back(trim_ws(cur));
            cur.clear();
        } else {
            cur.push_back(c);
        }
    }
    if (!cur.empty()) out.push_back(trim_ws(cur));
    out.erase(std::remove_if(out.begin(), out.end(), [](const std::string& x){ return x.empty(); }), out.end());
    return out;
}

static bool is_path_under(const std::filesystem::path& p, const std::filesystem::path& root) {
    std::error_code ec;
    auto rp = std::filesystem::weakly_canonical(p, ec);
    if (ec) return false;
    auto rr = std::filesystem::weakly_canonical(root, ec);
    if (ec) return false;
    auto ps = rp.generic_string();
    auto rs = rr.generic_string();
    if (ps == rs) return true;
    if (!rs.empty() && rs.back() != '/') rs.push_back('/');
    return ps.rfind(rs, 0) == 0;
}

static int64_t now_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(steady_clock::now().time_since_epoch()).count();
}

static int getenv_int(const char* name, int defv) {
    if (const char* v = std::getenv(name)) {
        try { return std::stoi(v); } catch (...) { return defv; }
    }
    return defv;
}

static size_t getenv_size_t(const char* name, size_t defv) {
    if (const char* v = std::getenv(name)) {
        try { return (size_t)std::stoull(v); } catch (...) { return defv; }
    }
    return defv;
}

ExternalProcessSelector::ExternalProcessSelector(std::unique_ptr<ISelector> fallback,
                                                 std::filesystem::path repo_root,
                                                 std::string policy_cmd)
    : fallback_(std::move(fallback)), repo_root_(std::move(repo_root)), policy_cmd_(std::move(policy_cmd)) {

    argv_ = split_argv_quoted(policy_cmd_);

    // defaults
    allowed_exec_basenames_ = {"python3", "python", "bash", "sh", "node"};
    if (const char* al = std::getenv("MACHINA_POLICY_ALLOWED_EXE")) {
        allowed_exec_basenames_.clear();
        for (auto& t : split_csv(al)) allowed_exec_basenames_.push_back(lower_ascii(t));
    }

    allowed_script_root_ = repo_root_ / "policies";
    if (const char* sr = std::getenv("MACHINA_POLICY_ALLOWED_SCRIPT_ROOT")) {
        allowed_script_root_ = std::filesystem::path(sr);
    }

    allow_unsafe_ = false;
    if (const char* u = std::getenv("MACHINA_POLICY_ALLOW_UNSAFE")) {
        allow_unsafe_ = (std::string(u) == "1");
    }

    lim_.timeout_ms = getenv_int("MACHINA_POLICY_TIMEOUT_MS", 2500);
    lim_.stdout_max_bytes = getenv_size_t("MACHINA_POLICY_STDOUT_MAX", 64 * 1024);
    lim_.rlimit_cpu_sec = getenv_int("MACHINA_POLICY_RLIMIT_CPU_SEC", 2);
    lim_.rlimit_as_mb = getenv_size_t("MACHINA_POLICY_RLIMIT_AS_MB", 768);
    lim_.rlimit_fsize_mb = getenv_size_t("MACHINA_POLICY_RLIMIT_FSIZE_MB", 10);
    lim_.rlimit_nofile = getenv_int("MACHINA_POLICY_RLIMIT_NOFILE", 64);
    lim_.rlimit_nproc = getenv_int("MACHINA_POLICY_RLIMIT_NPROC", 32);

    policy_fail_threshold_ = getenv_int("MACHINA_POLICY_FAIL_THRESHOLD", 5);
    if (policy_fail_threshold_ < 1) policy_fail_threshold_ = 1;
    policy_cooldown_ms_ = (int64_t)getenv_int("MACHINA_POLICY_COOLDOWN_MS", 30000);
    if (policy_cooldown_ms_ < 0) policy_cooldown_ms_ = 0;
}

Selection ExternalProcessSelector::select(const Menu& menu,
                                         const std::string& goal_digest,
                                         const std::string& state_digest,
                                         ControlMode mode,
                                         const std::string& inputs_json) {
    if (mode == ControlMode::FALLBACK_ONLY || policy_cmd_.empty() || argv_.empty()) {
        return fallback_->select(menu, goal_digest, state_digest, mode, inputs_json);
    }

    // Circuit breaker: if policy is flapping (timeouts/crashes), temporarily disable it.
    // Design intent:
    //   Trip threshold: MACHINA_POLICY_FAIL_THRESHOLD consecutive failures (default 5).
    //   Cooldown:       MACHINA_POLICY_COOLDOWN_MS milliseconds (default 30 000 ms / 30 s).
    //   Behavior:       When tripped the selector auto-switches to FALLBACK_ONLY
    //                   for the cooldown period, then auto-recovers (next call retries
    //                   the external policy). This prevents a broken/slow policy from
    //                   stalling the entire run loop while still allowing self-healing.
    const int64_t now = now_ms();
    if (policy_disabled_until_ms_ > now) {
        return fallback_->select(menu, goal_digest, state_digest, ControlMode::FALLBACK_ONLY, inputs_json);
    }

#ifdef _WIN32
    (void)menu; (void)goal_digest; (void)state_digest; (void)mode;
    return fallback_->select(menu, goal_digest, state_digest, ControlMode::FALLBACK_ONLY, inputs_json);
#else
    // Allowlist checks (no shell)
    if (!allow_unsafe_) {
        std::filesystem::path exe(argv_[0]);
        std::string exe_base = lower_ascii(exe.filename().string());
        bool ok = false;
        for (const auto& a : allowed_exec_basenames_) {
            if (exe_base == lower_ascii(a)) { ok = true; break; }
        }
        if (!ok) {
            Selection invalid; invalid.kind = Selection::Kind::INVALID; invalid.raw = "policy exe not allowed: " + exe_base;
            return invalid;
        }

        // If second token looks like a script path, ensure it stays under allowed_script_root_.
        if (argv_.size() >= 2) {
            std::filesystem::path p(argv_[1]);
            std::string ext = lower_ascii(p.extension().string());
            if (ext == ".py" || ext == ".sh" || ext == ".js") {
                std::filesystem::path resolved = p;
                if (!p.is_absolute()) resolved = repo_root_ / p;
                if (!is_path_under(resolved, allowed_script_root_)) {
                    Selection invalid; invalid.kind = Selection::Kind::INVALID;
                    invalid.raw = "policy script path not allowed: " + resolved.string();
                    return invalid;
                }
            }
        }
    }

    // Build payload JSON
    json_object* root = json_object_new_object();
    json_object_object_add(root, "goal_digest", json_object_new_string_len(goal_digest.c_str(), (int)goal_digest.size()));
    json_object_object_add(root, "state_digest", json_object_new_string_len(state_digest.c_str(), (int)state_digest.size()));
    json_object_object_add(root, "control_mode", json_object_new_string(mode_name(mode)));

    // include inputs (runner provided). Must be a JSON object.
    if (!inputs_json.empty()) {
        json_object* in = json_tokener_parse(inputs_json.c_str());
        if (in && json_object_is_type(in, json_type_object)) {
            json_object_object_add(root, "inputs", in);
        } else if (in) {
            json_object_put(in);
        }
    }

    json_object* arr = json_object_new_array();
    for (const auto& it : menu.items) {
        json_object* o = json_object_new_object();
        std::string sid = it.sid.toString();
        json_object_object_add(o, "sid", json_object_new_string(sid.c_str()));
        json_object_object_add(o, "aid", json_object_new_string(it.aid.c_str()));
        json_object_object_add(o, "name", json_object_new_string(it.name.c_str()));
        json_object* tags = json_object_new_array();
        for (const auto& t : it.tags) json_object_array_add(tags, json_object_new_string(t.c_str()));
        json_object_object_add(o, "tags", tags);
        json_object_array_add(arr, o);
    }
    json_object_object_add(root, "menu", arr);

    const char* payload_c = json_object_to_json_string_ext(root, JSON_C_TO_STRING_PLAIN);
    std::string payload = payload_c ? payload_c : "{}";

    // Temp payload file
    // RAII guard for root: ensures json_object_put on all paths (including exceptions)
    struct JsonGuard { json_object* o; ~JsonGuard() { if (o) json_object_put(o); } };
    JsonGuard root_guard{root};

    std::filesystem::path payload_path;
    try {
        auto tmp_dir = std::filesystem::temp_directory_path();
        std::ostringstream fname;
        fname << "machina_policy_payload_" << std::to_string((uint64_t)machina::secure_rand32()) << ".json";
        payload_path = tmp_dir / fname.str();
    } catch (...) {
        // temp_directory_path() can throw filesystem_error
        return fallback_->select(menu, goal_digest, state_digest, ControlMode::FALLBACK_ONLY, inputs_json);
    }
    {
        std::ofstream f(payload_path, std::ios::binary);
        if (!f) {
            return fallback_->select(menu, goal_digest, state_digest, ControlMode::FALLBACK_ONLY, inputs_json);
        }
        f.write(payload.data(), (std::streamsize)payload.size());
    }

    // argv + payload_path
    std::vector<std::string> av = argv_;
    av.push_back(payload_path.string());

    ProcResult pr;
    bool started = proc_run_capture_sandboxed(av, repo_root_.string(), lim_, &pr);

    // Cleanup (root freed by root_guard destructor)
    std::error_code ec;
    std::filesystem::remove(payload_path, ec);

    auto mark_failure = [&](const std::string& why) -> Selection {
        consecutive_policy_fail_ += 1;
        if (consecutive_policy_fail_ >= policy_fail_threshold_) {
            policy_disabled_until_ms_ = now_ms() + policy_cooldown_ms_;
        }
        // If policy is used in POLICY_ONLY mode, we still return fallback selection rather than INVALID,
        // because INVALID would hard-fail the whole run. We preserve the policy error in raw.
        Selection fb = fallback_->select(menu, goal_digest, state_digest, ControlMode::FALLBACK_ONLY, inputs_json);
        fb.raw = std::string("[policy_failure]") + why + "\n" + fb.raw;
        return fb;
    };

    if (!started) {
        return mark_failure(pr.error.empty() ? "policy not started" : pr.error);
    }
    if (pr.timed_out) {
        return mark_failure("policy timed out");
    }
    if (pr.exit_code != 0) {
        return mark_failure(std::string("policy exit_code=") + std::to_string(pr.exit_code));
    }

    std::string sel = trim_ws(pr.output);
    if (sel.empty()) {
        return mark_failure("empty policy output");
    }

    Selection parsed = parse_selector_output(sel);
    if (parsed.kind == Selection::Kind::INVALID) {
        return mark_failure("invalid policy output");
    }

    // Phase 3 Layer 1: validate that a PICK sid actually exists in the menu
    if (parsed.kind == Selection::Kind::PICK && parsed.sid) {
        if (!menu.resolve(*parsed.sid)) {
            return mark_failure("policy picked non-existent sid: " + parsed.sid->toString());
        }
    }

    // policy OK: clear breaker
    consecutive_policy_fail_ = 0;
    policy_disabled_until_ms_ = 0;
    return parsed;
#endif
}

} // namespace machina
