#include "machina/tools.h"
#include "machina/json_mini.h"
#include "machina/proc.h"

#include <algorithm>
#include <filesystem>
#include <sstream>
#include <string>
#include <vector>

namespace {

static std::string trim_ws(std::string s) {
    while (!s.empty() && (s.back()=='\n' || s.back()=='\r' || s.back()==' ' || s.back()=='\t')) s.pop_back();
    size_t i=0;
    while (i<s.size() && (s[i]=='\n' || s[i]=='\r' || s[i]==' ' || s[i]=='\t')) i++;
    if (i) s.erase(0,i);
    return s;
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

} // namespace

namespace machina {

// Tool: AID.SHELL.EXEC.v1
// Runs a whitelisted executable with argv array. No shell.
ToolResult tool_shell_exec(const std::string& input_json, DSState& ds_tmp) {
#ifdef _WIN32
    (void)input_json; (void)ds_tmp;
    return {StepStatus::TOOL_ERROR, "{}", "shell_exec not supported on Windows build"};
#else
    auto cmd = json_mini::get_array_strings(input_json, "cmd");
    if (cmd.empty()) return {StepStatus::TOOL_ERROR, "{}", "missing cmd (array of strings)"};

    std::filesystem::path repo_root = std::filesystem::current_path();
    if (const char* r = std::getenv("MACHINA_ROOT")) repo_root = std::filesystem::path(r);

    std::string cwd = json_mini::get_string(input_json, "cwd").value_or(repo_root.string());
    std::filesystem::path cwdp = std::filesystem::path(cwd);
    if (!cwdp.is_absolute()) cwdp = repo_root / cwdp;
    if (!is_path_under(cwdp, repo_root)) {
        return {StepStatus::TOOL_ERROR, "{}", "cwd not allowed (must be under MACHINA_ROOT)"};
    }

    // allowlist
    std::vector<std::string> allowed = {
        "python3","python","gcc","g++","clang","clang++","cmake","make","ninja","git",
        "curl","wget","zip","unzip","rg","grep","sed","awk","find","ls","cat","head","tail",
        "uname","whoami","date","hostname","pwd","wc","sort","uniq","tr","diff"
    };
    if (const char* al = std::getenv("MACHINA_SHELL_ALLOWED_EXE")) {
        allowed.clear();
        for (auto& t : split_csv(al)) allowed.push_back(lower_ascii(t));
    }

    std::filesystem::path exe(cmd[0]);
    // Resolve symlinks to prevent allowlist bypass via symlink or $PATH manipulation
    std::error_code resolve_ec;
    auto resolved_exe = std::filesystem::canonical(exe, resolve_ec);
    if (!resolve_ec) exe = resolved_exe;
    std::string exe_base = lower_ascii(exe.filename().string());
    bool ok = false;
    for (const auto& a : allowed) {
        if (exe_base == lower_ascii(a)) { ok = true; break; }
    }
    if (!ok) {
        return {StepStatus::TOOL_ERROR, "{}", "exe not allowed: " + exe_base};
    }

    ProcLimits lim;
    lim.timeout_ms = (int)json_mini::get_int(input_json, "timeout_ms").value_or(getenv_int("MACHINA_SHELL_TIMEOUT_MS", 5000));
    lim.stdout_max_bytes = (size_t)json_mini::get_int(input_json, "stdout_max").value_or((int64_t)getenv_size_t("MACHINA_SHELL_STDOUT_MAX", 128 * 1024));
    lim.rlimit_cpu_sec = getenv_int("MACHINA_SHELL_RLIMIT_CPU_SEC", 3);
    lim.rlimit_as_mb = getenv_size_t("MACHINA_SHELL_RLIMIT_AS_MB", 1024);
    lim.rlimit_fsize_mb = getenv_size_t("MACHINA_SHELL_RLIMIT_FSIZE_MB", 16);
    lim.rlimit_nofile = getenv_int("MACHINA_SHELL_RLIMIT_NOFILE", 64);
    lim.rlimit_nproc = getenv_int("MACHINA_SHELL_RLIMIT_NPROC", 32);

    ProcResult pr;
    bool started = proc_run_capture_sandboxed(cmd, cwdp.string(), lim, &pr);
    if (!started) {
        return {StepStatus::TOOL_ERROR, "{}", pr.error};
    }

    Artifact a;
    a.type = "shell_exec";
    a.provenance = "shell:" + exe_base;
    a.size_bytes = pr.output.size();

    std::ostringstream payload;
    payload << "{";
    payload << "\"ok\":" << (pr.timed_out ? "false" : "true") << ",";
    payload << "\"exit_code\":" << pr.exit_code << ",";
    payload << "\"timed_out\":" << (pr.timed_out ? "true" : "false") << ",";
    payload << "\"truncated\":" << (pr.output_truncated ? "true" : "false") << ",";
    payload << "\"output\":\"" << json_mini::json_escape(pr.output) << "\"";
    payload << "}";

    a.content_json = payload.str();
    ds_tmp.slots[(uint8_t)DSSlot::DS0] = a;

    return {StepStatus::OK, a.content_json, ""};
#endif
}

} // namespace machina
