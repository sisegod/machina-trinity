#include "tools/tier0/genesis.h"

#include "machina/plugin_api.h"
#include "machina/json_mini.h"
#include "machina/state.h"
#include "machina/proc.h"
#include "machina/hash.h"
#include "machina/crypto.h"
#include "machina/serialization.h"

#include <json-c/json.h>

#include <chrono>
#include <memory>
#include <mutex>
#include <condition_variable>

#ifndef _WIN32
#include <dlfcn.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/wait.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <poll.h>
#include <signal.h>
  #ifdef __linux__
    #include <sys/prctl.h>
  #endif
#endif

#include <fstream>
#include <sstream>
#include <cstdlib>
#include <cstdio>
#include <vector>
#include <algorithm>

namespace machina {

static PluginManager* g_pm = nullptr;
static IToolRegistrar* g_registrar = nullptr;
static std::filesystem::path g_root;

static Registry* g_core_reg = nullptr;
static ToolRunner* g_core_runner = nullptr;
static bool g_allow_override = false;

static bool env_true(const char* name, bool def=false) {
    const char* v = std::getenv(name);
    if (!v) return def;
    std::string s = v;
    if (s == "1" || s == "true" || s == "TRUE" || s == "yes" || s == "YES") return true;
    if (s == "0" || s == "false" || s == "FALSE" || s == "no" || s == "NO") return false;
    return def;
}

static int env_int(const char* name, int def) {
    const char* v = std::getenv(name);
    if (!v) return def;
    try { return std::stoi(v); } catch (...) { return def; }
}



struct GenesisBreakerState {
    int fail_count{0};
    int64_t first_fail_ms{0};
    int64_t last_fail_ms{0};
    int64_t block_until_ms{0};
};

static int64_t now_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

static std::filesystem::path breaker_base_dir() {
    auto d = g_root / "toolpacks" / "runtime_genesis" / "breakers";
    std::filesystem::create_directories(d);
    return d;
}

static std::filesystem::path breaker_file_for(const std::string& key) {
    auto fname = key.substr(0, 40) + std::string(".json");
    return breaker_base_dir() / fname;
}

static bool breaker_enabled() {
    return env_true("MACHINA_GENESIS_BREAKER_ENABLE", true);
}

static GenesisBreakerState breaker_load(const std::filesystem::path& p) {
    GenesisBreakerState st;
    std::ifstream f(p);
    if (!f) return st;
    std::stringstream buf;
    buf << f.rdbuf();
    const std::string js = buf.str();
    if (auto v = json_mini::get_int(js, "fail_count")) st.fail_count = (int)*v;
    if (auto v = json_mini::get_int(js, "first_fail_ms")) st.first_fail_ms = *v;
    if (auto v = json_mini::get_int(js, "last_fail_ms")) st.last_fail_ms = *v;
    if (auto v = json_mini::get_int(js, "block_until_ms")) st.block_until_ms = *v;
    return st;
}

static void breaker_store(const std::filesystem::path& p, const GenesisBreakerState& st) {
    std::ofstream f(p, std::ios::binary | std::ios::trunc);
    if (!f) return;
    f << "{\"fail_count\":" << st.fail_count
      << ",\"first_fail_ms\":" << st.first_fail_ms
      << ",\"last_fail_ms\":" << st.last_fail_ms
      << ",\"block_until_ms\":" << st.block_until_ms
      << "}";
    f.close();
}

static std::string breaker_key(const std::string& kind, const std::string& name) {
    return hash::sha256_hex(kind + ":" + name);
}

static bool breaker_is_blocked(const std::string& kind, const std::string& name, GenesisBreakerState* out) {
    if (!breaker_enabled()) return false;
    const std::string key = breaker_key(kind, name);
    const auto fp = breaker_file_for(key);
    GenesisBreakerState st = breaker_load(fp);
    if (out) *out = st;
    const int64_t now = now_ms();
    return (st.block_until_ms > 0 && now < st.block_until_ms);
}

static void breaker_record_fail(const std::string& kind, const std::string& name) {
    if (!breaker_enabled()) return;
    const std::string key = breaker_key(kind, name);
    const auto fp = breaker_file_for(key);
    GenesisBreakerState st = breaker_load(fp);

    const int threshold = std::max(1, env_int("MACHINA_GENESIS_BREAKER_THRESHOLD", 3));
    const int64_t window_ms = (int64_t)std::max(1, env_int("MACHINA_GENESIS_BREAKER_WINDOW_SEC", 300)) * 1000LL;
    const int64_t cooldown_ms = (int64_t)std::max(1, env_int("MACHINA_GENESIS_BREAKER_COOLDOWN_SEC", 600)) * 1000LL;

    const int64_t now = now_ms();
    if (st.first_fail_ms == 0 || (now - st.first_fail_ms) > window_ms) {
        st.fail_count = 1;
        st.first_fail_ms = now;
    } else {
        st.fail_count += 1;
    }
    st.last_fail_ms = now;
    if (st.fail_count >= threshold) {
        st.block_until_ms = now + cooldown_ms;
    }
    breaker_store(fp, st);
}

static void breaker_record_success(const std::string& kind, const std::string& name) {
    if (!breaker_enabled()) return;
    const std::string key = breaker_key(kind, name);
    const auto fp = breaker_file_for(key);
    std::error_code ec;
    std::filesystem::remove(fp, ec);
}

static std::filesystem::path ensure_under(const std::filesystem::path& base,
                                          const std::filesystem::path& rel) {
    auto p = (base / rel).lexically_normal();

    // Canonicalize best-effort (base should exist; p's parents should exist).
    auto p_weak = std::filesystem::weakly_canonical(p);
    auto base_weak = std::filesystem::weakly_canonical(base.lexically_normal());

    auto ps = p_weak.generic_string();
    auto bs = base_weak.generic_string();
    if (ps == bs) return p_weak;
    if (!bs.empty() && bs.back() != '/') bs.push_back('/');
    if (ps.rfind(bs, 0) != 0) {
        throw std::runtime_error("path escapes sandbox base");
    }
    return p_weak;
}

void genesis_set_context(PluginManager* pm,
                         IToolRegistrar* registrar,
                         Registry* reg,
                         ToolRunner* runner,
                         bool allow_override,
                         const std::filesystem::path& root) {
    g_pm = pm;
    g_registrar = registrar;
    g_core_reg = reg;
    g_core_runner = runner;
    g_allow_override = allow_override;
    g_root = root;
}

// json_quote, artifact_to_json/from_json, dsstate_to_json/from_json,
// stepstatus_from_str, json_get_string/bool/string_array are now in
// machina/serialization.h (core/src/serialization.cpp).

static void write_artifact(DSState& ds_tmp, const std::string& type, const std::string& content_json) {
    Artifact a;
    a.type = type;
    a.provenance = "genesis";
    a.content_json = content_json;
    a.size_bytes = content_json.size();
    ds_tmp.slots[(uint8_t)DSSlot::DS7] = a;
}

static void set_stage(DSState& ds_tmp, const std::string& /*stage*/, const std::string& payload_json) {
    Artifact a;
    a.type = "genesis_stage";
    a.provenance = "genesis";
    a.content_json = payload_json;
    a.size_bytes = payload_json.size();
    ds_tmp.slots[(uint8_t)DSSlot::DS6] = a;
}

static std::string slurp_file(const std::filesystem::path& p, size_t max_bytes = 512 * 1024) {
    std::ifstream f(p, std::ios::binary);
    if (!f) throw std::runtime_error("cannot open file: " + p.string());
    std::string buf;
    buf.resize(max_bytes);
    f.read(buf.data(), (std::streamsize)max_bytes);
    std::streamsize n = f.gcount();
    buf.resize((size_t)n);
    return buf;
}



static bool plugin_mode_oop() {
    const char* v = std::getenv("MACHINA_PLUGIN_MODE");
    if (!v) return false;
    std::string s = v;
    for (auto& c : s) c = (char)std::tolower((unsigned char)c);
    return (s == "oop" || s == "1" || s == "true" || s == "yes" || s == "on");
}

static std::string toolhost_bin() {
    if (const char* b = std::getenv("MACHINA_TOOLHOST_BIN")) {
        if (*b) return std::string(b);
    }
    return "machina_toolhost";
}

static ProcLimits toolhost_proc_limits() {
    ProcLimits lim;
    lim.timeout_ms = env_int("MACHINA_TOOLHOST_TIMEOUT_MS", 8000);
    lim.stdout_max_bytes = (size_t)env_int("MACHINA_TOOLHOST_STDOUT_MAX_BYTES", 512 * 1024);
    lim.rlimit_cpu_sec = env_int("MACHINA_TOOLHOST_RLIMIT_CPU_SEC", 6);
    lim.rlimit_as_mb = (size_t)env_int("MACHINA_TOOLHOST_RLIMIT_AS_MB", 1024);
    lim.rlimit_fsize_mb = (size_t)env_int("MACHINA_TOOLHOST_RLIMIT_FSIZE_MB", 16);
    lim.rlimit_nofile = env_int("MACHINA_TOOLHOST_RLIMIT_NOFILE", 64);
    lim.rlimit_nproc = env_int("MACHINA_TOOLHOST_RLIMIT_NPROC", 32);
    lim.no_new_privs = true;
    lim.enable_seccomp = env_true("MACHINA_SECCOMP_ENABLE", false);
    return lim;
}

// ---- Persistent toolhost session for --serve NDJSON mode ----
// Avoids forking a new process per tool invocation in OOP plugin mode.
// Falls back to --run (fork-per-call) if the session cannot be started or dies.

struct ToolHostSession {
    std::string plugin_str;
    pid_t pid{-1};
    int to_child{-1};     // write end: parent -> child stdin
    int from_child{-1};   // read end:  child stdout -> parent
    DSState base_ds;       // delta tracking base
    int fail_count{0};     // (no per-session mutex; pool Lease provides exclusive access)

    bool start() {
#ifdef _WIN32
        return false;
#else
        shutdown(); // clean up any previous session

        // Ignore SIGPIPE so write() to dead child returns EPIPE instead of killing us
        ::signal(SIGPIPE, SIG_IGN);

        int in_pipe[2], out_pipe[2];
        if (::pipe(in_pipe) != 0) return false;
        if (::pipe(out_pipe) != 0) { ::close(in_pipe[0]); ::close(in_pipe[1]); return false; }

        pid_t child = ::fork();
        if (child < 0) {
            ::close(in_pipe[0]); ::close(in_pipe[1]);
            ::close(out_pipe[0]); ::close(out_pipe[1]);
            return false;
        }

        if (child == 0) {
            // New process group for clean group-kill on shutdown
            ::setpgid(0, 0);

            // child: wire stdin/stdout, leave stderr for debug logging
            ::dup2(in_pipe[0], STDIN_FILENO);
            ::dup2(out_pipe[1], STDOUT_FILENO);
            ::close(in_pipe[0]); ::close(in_pipe[1]);
            ::close(out_pipe[0]); ::close(out_pipe[1]);

            long maxfd = ::sysconf(_SC_OPEN_MAX);
            if (maxfd < 256) maxfd = 256;
            for (int fd = 3; fd < (int)maxfd; fd++) ::close(fd);

            ::umask(077);
#ifdef __linux__
            ::prctl(PR_SET_PDEATHSIG, SIGKILL);
#endif
            // Apply resource limits (NOT CPU — process is long-lived)
            ProcLimits lim = toolhost_proc_limits();
            if (lim.rlimit_as_mb > 0) {
                struct rlimit rl;
                rl.rlim_cur = rl.rlim_max = (rlim_t)lim.rlimit_as_mb * 1024ULL * 1024ULL;
                ::setrlimit(RLIMIT_AS, &rl);
            }
            if (lim.rlimit_fsize_mb > 0) {
                struct rlimit rl;
                rl.rlim_cur = rl.rlim_max = (rlim_t)lim.rlimit_fsize_mb * 1024ULL * 1024ULL;
                ::setrlimit(RLIMIT_FSIZE, &rl);
            }
            if (lim.rlimit_nofile > 0) {
                struct rlimit rl;
                rl.rlim_cur = rl.rlim_max = (rlim_t)lim.rlimit_nofile;
                ::setrlimit(RLIMIT_NOFILE, &rl);
            }
            if (lim.rlimit_nproc > 0) {
                struct rlimit rl;
                rl.rlim_cur = rl.rlim_max = (rlim_t)lim.rlimit_nproc;
                ::setrlimit(RLIMIT_NPROC, &rl);
            }
#ifdef __linux__
            if (lim.no_new_privs) {
                ::prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
            }
#endif

            std::string th = toolhost_bin();
            ::execl(th.c_str(), th.c_str(), "--serve", plugin_str.c_str(), nullptr);
            ::_exit(127);
        }

        // parent
        ::close(in_pipe[0]);
        ::close(out_pipe[1]);
        to_child = in_pipe[1];
        from_child = out_pipe[0];
        pid = child;
        ::setpgid(child, child); // mirror child's setpgid (race safety)

        int flags = ::fcntl(from_child, F_GETFL, 0);
        if (flags >= 0) ::fcntl(from_child, F_SETFL, flags | O_NONBLOCK);

        fail_count = 0;
        base_ds = DSState{};
        return true;
#endif
    }

    // Send NDJSON request line, read one response line. Returns empty on error.
    std::string send_receive(const std::string& request, int timeout_ms) {
#ifdef _WIN32
        return "";
#else
        if (pid < 0 || to_child < 0 || from_child < 0) return "";

        // Check child alive — if dead, clean up FDs immediately (defense-in-depth)
        int st = 0;
        pid_t w = ::waitpid(pid, &st, WNOHANG);
        if (w == pid) {
            pid = -1;
            if (to_child >= 0) { ::close(to_child); to_child = -1; }
            if (from_child >= 0) { ::close(from_child); from_child = -1; }
            return "";
        }

        // Write request + newline
        std::string line = request;
        if (line.empty() || line.back() != '\n') line += '\n';
        size_t off = 0;
        while (off < line.size()) {
            ssize_t n = ::write(to_child, line.data() + off, line.size() - off);
            if (n > 0) { off += (size_t)n; continue; }
            if (n == -1 && errno == EINTR) continue;
            return ""; // write error (child likely dead)
        }

        // Read response line with timeout
        auto t0 = std::chrono::steady_clock::now();
        static constexpr size_t MAX_RESPONSE_BYTES = 1024 * 1024; // 1 MB hard cap
        std::string resp;
        while (true) {
            char buf[8192];
            ssize_t n = ::read(from_child, buf, sizeof(buf));
            if (n > 0) {
                if (resp.size() + (size_t)n > MAX_RESPONSE_BYTES) return ""; // response too large
                resp.append(buf, (size_t)n);
                auto nl = resp.find('\n');
                if (nl != std::string::npos) return resp.substr(0, nl);
                continue;
            }
            if (n == 0) return ""; // EOF — child closed stdout
            if (n == -1 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                auto now = std::chrono::steady_clock::now();
                int elapsed = (int)std::chrono::duration_cast<std::chrono::milliseconds>(now - t0).count();
                if (timeout_ms > 0 && elapsed >= timeout_ms) return "";
                struct pollfd pfd;
                pfd.fd = from_child;
                pfd.events = POLLIN;
                pfd.revents = 0;
                int remain = timeout_ms > 0 ? timeout_ms - elapsed : 1000;
                ::poll(&pfd, 1, std::min(remain, 100));
                continue;
            }
            return ""; // read error
        }
#endif
    }

    void shutdown() {
#ifndef _WIN32
        if (to_child >= 0) {
            ssize_t wr = ::write(to_child, "\n", 1); // graceful shutdown signal
            (void)wr;
            ::close(to_child);
            to_child = -1;
        }
        if (pid > 0) {
            int st = 0;
            ::usleep(50000); // 50ms grace
            pid_t w = ::waitpid(pid, &st, WNOHANG);
            if (w != pid) {
                ::killpg(pid, SIGTERM); // kill entire process group
                ::usleep(100000); // 100ms
                w = ::waitpid(pid, &st, WNOHANG);
                if (w != pid) {
                    ::killpg(pid, SIGKILL); // force kill entire group
                    ::waitpid(pid, &st, 0);
                }
            }
            pid = -1;
        }
        if (from_child >= 0) {
            ::close(from_child);
            from_child = -1;
        }
#endif
    }

    ~ToolHostSession() { shutdown(); }

    ToolHostSession() = default;
    ToolHostSession(const ToolHostSession&) = delete;
    ToolHostSession& operator=(const ToolHostSession&) = delete;
};

// ---- Session pool for concurrent OOP tool execution ----
// Maintains N persistent toolhost sessions per plugin.
// Lease-based: acquire() returns an exclusive Lease; released on destruction.
struct ToolHostPool {
    struct Lease {
        ToolHostPool* pool{nullptr};
        int idx{-1};
        ToolHostSession* session{nullptr};

        Lease() = default;
        Lease(ToolHostPool* p, int i, ToolHostSession* s) : pool(p), idx(i), session(s) {}
        ~Lease() { if (pool && idx >= 0) pool->release(idx); }

        Lease(const Lease&) = delete;
        Lease& operator=(const Lease&) = delete;
        Lease(Lease&& o) noexcept : pool(o.pool), idx(o.idx), session(o.session) {
            o.pool = nullptr; o.idx = -1; o.session = nullptr;
        }
        Lease& operator=(Lease&& o) noexcept {
            if (this != &o) {
                if (pool && idx >= 0) pool->release(idx);
                pool = o.pool; idx = o.idx; session = o.session;
                o.pool = nullptr; o.idx = -1; o.session = nullptr;
            }
            return *this;
        }
    };

    int pool_size;
    std::vector<std::unique_ptr<ToolHostSession>> sessions;
    std::vector<bool> in_use;
    std::mutex mu;
    std::condition_variable cv;

    explicit ToolHostPool(const std::string& plugin, int size)
        : pool_size(std::max(size, 1)) {
        sessions.resize(pool_size);
        in_use.resize(pool_size, false);
        for (int i = 0; i < pool_size; i++) {
            sessions[i] = std::make_unique<ToolHostSession>();
            sessions[i]->plugin_str = plugin;
        }
    }

    // Block until a session is available, then return exclusive lease.
    Lease acquire() {
        std::unique_lock<std::mutex> lk(mu);
        while (true) {
            for (int i = 0; i < pool_size; i++) {
                if (!in_use[i]) {
                    in_use[i] = true;
                    return Lease{this, i, sessions[i].get()};
                }
            }
            cv.wait(lk);
        }
    }

    void release(int idx) {
        {
            std::lock_guard<std::mutex> lk(mu);
            in_use[idx] = false;
        }
        cv.notify_one();
    }

    ToolHostPool(const ToolHostPool&) = delete;
    ToolHostPool& operator=(const ToolHostPool&) = delete;
};

static bool toolhost_list(const std::filesystem::path& toolhost, const std::filesystem::path& plugin_path, std::vector<ToolDesc>* out_tools, std::string* out_err) {
    if (!out_tools) return false;
    out_tools->clear();

    ProcResult pr;
    ProcLimits lim = toolhost_proc_limits();
    std::vector<std::string> argv = {toolhost.string(), "--list", plugin_path.string()};
    if (!proc_run_capture_sandboxed(argv, "", lim, &pr)) {
        if (out_err) *out_err = pr.error.empty() ? "toolhost_list: proc failed" : pr.error;
        return false;
    }
    if (pr.exit_code != 0) {
        if (out_err) *out_err = "toolhost_list exit_code=" + std::to_string(pr.exit_code) + ": " + pr.output;
        return false;
    }

    json_object* root = json_tokener_parse(pr.output.c_str());
    if (!root) {
        if (out_err) *out_err = "toolhost_list: invalid JSON output";
        return false;
    }
    json_object* okv = nullptr;
    if (!json_object_object_get_ex(root, "ok", &okv) || !okv || !json_object_get_boolean(okv)) {
        std::string e;
        (void)json_get_string(root, "error", &e);
        if (out_err) *out_err = e.empty() ? "toolhost_list: ok=false" : e;
        json_object_put(root);
        return false;
    }

    json_object* tools = nullptr;
    if (!json_object_object_get_ex(root, "tools", &tools) || !tools || !json_object_is_type(tools, json_type_array)) {
        if (out_err) *out_err = "toolhost_list: missing tools array";
        json_object_put(root);
        return false;
    }

    const int n = json_object_array_length(tools);
    out_tools->reserve((size_t)n);
    for (int i=0;i<n;i++) {
        json_object* t = json_object_array_get_idx(tools, i);
        if (!t || !json_object_is_type(t, json_type_object)) continue;
        ToolDesc d;
        std::string tmp;
        if (!json_get_string(t, "aid", &d.aid) || d.aid.empty()) continue;
        (void)json_get_string(t, "name", &d.name);
        bool det = true;
        if (json_get_bool(t, "deterministic", &det)) d.deterministic = det;
        d.tags = json_get_string_array(t, "tags");
        d.side_effects = json_get_string_array(t, "side_effects");
        d.replay_inputs = json_get_string_array(t, "replay_inputs");
        out_tools->push_back(std::move(d));
    }

    json_object_put(root);
    return true;
}

static std::string dynlib_ext() {
#ifdef _WIN32
    return ".dll";
#elif defined(__APPLE__)
    return ".dylib";
#else
    return ".so";
#endif
}

static ProcLimits genesis_proc_limits() {
    ProcLimits lim;
    lim.timeout_ms = env_int("MACHINA_GENESIS_TIMEOUT_MS", 30000);
    lim.stdout_max_bytes = (size_t)env_int("MACHINA_GENESIS_STDOUT_MAX_BYTES", 256 * 1024);
    lim.rlimit_cpu_sec = env_int("MACHINA_GENESIS_RLIMIT_CPU_SEC", 20);
    // 0 = no AS limit (g++ needs large virtual address space for cc1plus)
    lim.rlimit_as_mb = (size_t)env_int("MACHINA_GENESIS_RLIMIT_AS_MB", 0);
    lim.rlimit_fsize_mb = (size_t)env_int("MACHINA_GENESIS_RLIMIT_FSIZE_MB", 64);
    lim.rlimit_nofile = env_int("MACHINA_GENESIS_RLIMIT_NOFILE", 256);
    lim.rlimit_nproc = env_int("MACHINA_GENESIS_RLIMIT_NPROC", 0);
    lim.no_new_privs = true;
    return lim;
}

static bool tool_allowed() {
    // Opt-in is recommended in production (prevents self-evolution surprises).
    // For dev / demos you can set MACHINA_GENESIS_ENABLE=1.
    return env_true("MACHINA_GENESIS_ENABLE", false);
}

static bool genesis_prod_mode() {
    return env_true("MACHINA_GENESIS_PROD_MODE", false);
}

static bool contains_any(const std::string& s, const std::vector<std::string>& needles) {
    for (const auto& n : needles) {
        if (s.find(n) != std::string::npos) return true;
    }
    return false;
}

// Conservative guard: block obvious process/network/memory-privilege escalations.
// Can be disabled via MACHINA_GENESIS_GUARD=0 (dev only).
static bool genesis_guard_source(const std::string& src, std::string* why) {
    if (!env_true("MACHINA_GENESIS_GUARD", true)) return true;

    // Lowercase copy for case-insensitive search (rough).
    std::string low = src;
    std::transform(low.begin(), low.end(), low.begin(), [](unsigned char c){ return (char)std::tolower(c); });

    const std::vector<std::string> banned = {
        "system(", "popen(", "fork(", "vfork(", "posix_spawn",
        "execl(", "execle(", "execlp(", "execv(", "execve(", "execvp(", "execvpe(",
        "fexecve(",
        "socket(", "connect(", "bind(", "listen(", "accept(",
        "mprotect(", "ptrace(", "syscall(", "prctl(", "unshare(", "clone(",
        "setuid(", "setgid(", "setreuid(", "setregid(", "capset(",
        "dlopen(", "dlsym(", "loadlibrary", "getprocaddress",
        "asm(", "__asm", "inline asm",
        "mmap(", "munmap(", "mremap(",         // memory manipulation
        "mount(", "umount(", "pivot_root(",    // filesystem escape
        "sethostname(", "setdomainname(",      // namespace manipulation
        "keyctl(", "add_key(", "request_key(", // kernel keyring
    };

    if (contains_any(low, banned)) {
        if (why) *why = "genesis_guard: source contains banned APIs/tokens";
        return false;
    }

    // Block some headers commonly used for OS-level capability.
    const std::vector<std::string> banned_headers = {
        "<unistd.h>", "<sys/socket.h>", "<netinet", "<arpa/inet.h>",
        "<sys/mman.h>", "<sys/ptrace.h>", "<sys/prctl.h>", "<sys/syscall.h>",
        "<windows.h>",
        "<cstdlib>", "<cstdio>", "<cstring>"  // block indirect access to system()/popen()/etc.
    };
    if (contains_any(low, banned_headers)) {
        if (why) *why = "genesis_guard: source includes banned headers";
        return false;
    }

    return true;
}

static std::vector<std::string> pkg_config_jsonc_flags(const ProcLimits& lim) {
#ifdef _WIN32
    (void)lim;
    return {};
#else
    std::vector<std::string> out;
    ProcResult r;
    if (!proc_run_capture_sandboxed({"pkg-config", "--cflags", "--libs", "json-c"}, "", lim, &r)) return out;
    if (r.exit_code != 0) return out;
    auto toks = split_argv_quoted(r.output);
    if (!toks.empty()) out = toks;
    return out;
#endif
}

static bool try_run_version(const std::string& exe, const ProcLimits& lim) {
#ifdef _WIN32
    (void)exe; (void)lim;
    return false;
#else
    if (exe.empty()) return false;
    if (exe.find(' ') != std::string::npos || exe.find('\t') != std::string::npos) return false;
    ProcResult r;
    ProcLimits l = lim;
    l.timeout_ms = std::min(l.timeout_ms, 1000);
    l.stdout_max_bytes = std::min<size_t>(l.stdout_max_bytes, 4096);
    if (!proc_run_capture_sandboxed({exe, "--version"}, "", l, &r)) return false;
    return r.exit_code == 0;
#endif
}

static std::string pick_default_compiler(const ProcLimits& lim) {
    if (const char* e = std::getenv("MACHINA_GENESIS_CXX")) {
        std::string s = e;
        if (!s.empty()) return s;
    }
    // Prefer g++ then clang++
    if (try_run_version("g++", lim)) return "g++";
    if (try_run_version("clang++", lim)) return "clang++";
    return ""; // none found
}

ToolResult tool_genesis_write_file(const std::string& input_json, DSState& ds_tmp) {
    try {
        if (!tool_allowed()) {
            return {StepStatus::TOOL_ERROR, "{}", "genesis disabled (set MACHINA_GENESIS_ENABLE=1 to enable)"};
        }

        auto rel = json_mini::get_string(input_json, "relative_path").value_or("");
        auto content = json_mini::get_string(input_json, "content").value_or("");
        bool overwrite = json_mini::get_bool(input_json, "overwrite").value_or(true);

        if (rel.empty()) {
            return {StepStatus::TOOL_ERROR, "{}", "missing relative_path"};
        }

        // Safety: cap source size (prevents disk abuse).
        if (content.size() > 256 * 1024) {
            return {StepStatus::TOOL_ERROR, "{}", "content too large (>256KB) for genesis_write_file"};
        }

        // Prod mode: stricter size limit
        if (genesis_prod_mode()) {
            size_t max_kb = (size_t)env_int("MACHINA_GENESIS_PROD_MAX_SOURCE_KB", 32);
            if (content.size() > max_kb * 1024) {
                return {StepStatus::TOOL_ERROR, "{}", "prod mode: source file too large (>" + std::to_string(max_kb) + "KB)"};
            }
        }

        // Basic filename hygiene (keep it boring)
        if (rel.find("..") != std::string::npos) {
            return {StepStatus::TOOL_ERROR, "{}", "relative_path may not contain '..'"};
        }

        const auto base = g_root / "toolpacks" / "runtime_genesis" / "src";
        std::filesystem::create_directories(base);

        auto dst = ensure_under(base, rel);
        std::filesystem::create_directories(dst.parent_path());
        if (!overwrite && std::filesystem::exists(dst)) {
            return {StepStatus::TOOL_ERROR, "{}", "file exists (overwrite=false)"};
        }

        // Guard check on content before writing.
        std::string why;
        if (!genesis_guard_source(content, &why)) {
            return {StepStatus::TOOL_ERROR, "{}", why};
        }

        std::ofstream f(dst, std::ios::binary);
        if (!f) {
            return {StepStatus::TOOL_ERROR, "{}", "cannot open for write"};
        }
        f.write(content.data(), (std::streamsize)content.size());
        f.close();

        std::ostringstream out;
        out << "{\"ok\":true,\"written\":" << json_quote(dst.string())
            << ",\"bytes\":" << content.size()
            << ",\"sha256\":\"" << hash::sha256_hex(content) << "\"}";
        auto outj = out.str();
        write_artifact(ds_tmp, "genesis_write", outj);
        {
            std::ostringstream st;
            st << "{\"stage\":\"WROTE\",\"relative_path\":" << json_quote(rel)
               << ",\"written\":" << json_quote(dst.string())
               << ",\"sha256\":\"" << hash::sha256_hex(content) << "\"}";
            set_stage(ds_tmp, "WROTE", st.str());
        }
        return {StepStatus::OK, outj, ""};
    } catch (const std::exception& e) {
        return {StepStatus::TOOL_ERROR, "{}", e.what()};
    }
}

ToolResult tool_genesis_compile_shared(const std::string& input_json, DSState& ds_tmp) {
    try {
#ifdef _WIN32
        (void)input_json; (void)ds_tmp;
        return {StepStatus::TOOL_ERROR, "{}", "compile_shared not implemented on Windows in RC1"};
#else
        if (!tool_allowed()) {
            return {StepStatus::TOOL_ERROR, "{}", "genesis disabled (set MACHINA_GENESIS_ENABLE=1 to enable)"};
        }

        auto src_rel = json_mini::get_string(input_json, "src_relative_path").value_or("");
        auto out_name = json_mini::get_string(input_json, "out_name").value_or("");
        auto cxx_in = json_mini::get_string(input_json, "cxx").value_or("");
        auto extra = json_mini::get_array_strings(input_json, "extra_flags");
        bool run_tidy = json_mini::get_bool(input_json, "clang_tidy").value_or(env_true("MACHINA_GENESIS_CLANG_TIDY", false));
        bool sanitize = json_mini::get_bool(input_json, "sanitizers").value_or(env_true("MACHINA_GENESIS_SANITIZE", false));
        bool werror = json_mini::get_bool(input_json, "werror").value_or(env_true("MACHINA_GENESIS_WERROR", false));

        if (src_rel.empty() || out_name.empty()) {
            return {StepStatus::TOOL_ERROR, "{}", "missing src_relative_path or out_name"};
        }
        if (out_name.find('/') != std::string::npos || out_name.find('\\') != std::string::npos) {
            return {StepStatus::TOOL_ERROR, "{}", "out_name must not contain path separators"};
        }

        // Circuit breaker: prevents infinite compile-error loops.
        GenesisBreakerState bst;
        if (breaker_is_blocked("compile", out_name, &bst)) {
            std::ostringstream err;
            err << "genesis breaker OPEN for compile(out_name=" << out_name << "): fail_count="
                << bst.fail_count << ", block_until_ms=" << bst.block_until_ms;
            breaker_record_fail("compile", out_name);
            return {StepStatus::TOOL_ERROR, "{}", err.str()};
        }

        const auto src_base = g_root / "toolpacks" / "runtime_genesis" / "src";
        const auto out_base = g_root / "toolpacks" / "runtime_plugins";
        std::filesystem::create_directories(src_base);
        std::filesystem::create_directories(out_base);

        auto src = ensure_under(src_base, src_rel);

        // Read + guard
        std::string src_text = slurp_file(src, 512 * 1024);
        std::string why;
        if (!genesis_guard_source(src_text, &why)) {
            return {StepStatus::TOOL_ERROR, "{}", why};
        }

        // Prod mode: require plugin_api.h as first include (ABI compatibility)
        if (genesis_prod_mode()) {
            // Find first #include in source
            auto pos = src_text.find("#include");
            if (pos != std::string::npos) {
                auto line_end = src_text.find('\n', pos);
                std::string first_include = src_text.substr(pos, line_end != std::string::npos ? line_end - pos : std::string::npos);
                if (first_include.find("machina/plugin_api.h") == std::string::npos) {
                    return {StepStatus::TOOL_ERROR, "{}", "prod mode: first #include must be \"machina/plugin_api.h\" for ABI compatibility"};
                }
            } else {
                return {StepStatus::TOOL_ERROR, "{}", "prod mode: source must include \"machina/plugin_api.h\""};
            }
        }

        const auto ext = dynlib_ext();
        auto outp = ensure_under(out_base, out_name + ext);

        ProcLimits lim = genesis_proc_limits();

        // Optional clang-tidy (best-effort)
        if (run_tidy) {
            ProcResult tr;
            std::vector<std::string> argv = {
                "clang-tidy",
                src.string(),
                "--quiet",
                "--",
                "-std=c++2a",
                std::string("-I") + (g_root / "core" / "include").string()
            };
            auto jsonc_flags = pkg_config_jsonc_flags(lim);
            argv.insert(argv.end(), jsonc_flags.begin(), jsonc_flags.end());
            bool started = proc_run_capture_sandboxed(argv, "", lim, &tr);
            if (!started) {
                if (env_true("MACHINA_GENESIS_CLANG_TIDY_STRICT", false)) {
                    return {StepStatus::TOOL_ERROR, "{}", "clang-tidy failed to start"};
                }
            } else if (tr.exit_code != 0) {
                std::ostringstream jout;
                jout << "{\"ok\":false,\"tool\":\"clang-tidy\",\"exit_code\":" << tr.exit_code
                     << ",\"timed_out\":" << (tr.timed_out ? "true" : "false")
                     << ",\"output\":" << json_quote(tr.output) << "}";
                write_artifact(ds_tmp, "genesis_static_analysis", jout.str());
                return {StepStatus::TOOL_ERROR, "{}", "clang-tidy reported issues"};
            } else {
                std::ostringstream jout;
                jout << "{\"ok\":true,\"tool\":\"clang-tidy\",\"exit_code\":0}";
                write_artifact(ds_tmp, "genesis_static_analysis", jout.str());
            }
        }

        std::string cxx = cxx_in.empty() ? pick_default_compiler(lim) : cxx_in;
        if (cxx.empty()) {
            return {StepStatus::TOOL_ERROR, "{}", "no C++ compiler found (install g++/clang++ or set MACHINA_GENESIS_CXX)"};
        }
        if (cxx.find(' ') != std::string::npos || cxx.find('\t') != std::string::npos) {
            return {StepStatus::TOOL_ERROR, "{}", "cxx must be a single executable name (no spaces)"};
        }

        std::vector<std::string> argv;
        argv.push_back(cxx);

#ifdef __APPLE__
        argv.push_back("-dynamiclib");
#else
        argv.push_back("-shared");
        argv.push_back("-fPIC");
#endif
        // GCC 9 uses -std=c++2a; GCC 10+ and Clang use -std=c++20.
        // Detect via __GNUC__ major version at runtime would be fragile; probe instead.
        argv.push_back("-std=c++2a");
        argv.push_back("-O2");
        argv.push_back("-Wall");
        argv.push_back("-Wextra");
        argv.push_back("-Wpedantic");
        if (werror) argv.push_back("-Werror");

        // Hardening (best-effort)
#ifndef __APPLE__
        argv.push_back("-fstack-protector-strong");
        argv.push_back("-D_FORTIFY_SOURCE=2");
#endif
        if (sanitize) {
            argv.push_back("-fsanitize=address,undefined");
            argv.push_back("-fno-omit-frame-pointer");
        }

        argv.push_back(std::string("-I") + (g_root / "core" / "include").string());
        argv.push_back("-o");
        argv.push_back(outp.string());
        argv.push_back(src.string());

        // Extra flags (filtered: only safe compiler flags allowed)
        auto is_safe_extra_flag = [](const std::string& f) -> bool {
            if (f.empty()) return false;
            // Allowed prefixes (positive list)
            if (f.rfind("-l", 0) == 0) return true;   // -lfoo
            if (f.rfind("-L", 0) == 0) return true;   // -L/path
            if (f.rfind("-I", 0) == 0) return true;   // -I/path
            if (f.rfind("-D", 0) == 0) return true;   // -DFOO=BAR
            if (f.rfind("-O", 0) == 0) return true;   // -O0, -O2, -Os
            if (f.rfind("-std=", 0) == 0) return true; // -std=c++20
            if (f.rfind("-W", 0) == 0) return true;   // -Wall, -Wextra, etc.
            if (f.rfind("-m", 0) == 0) return true;   // -march, -mtune, etc.
            if (f == "-g") return true;
            if (f == "-c") return true;
            if (f == "-shared") return true;
            if (f == "-fPIC") return true;
            // -f flags: allow general -f* but block -fplugin* (arbitrary code exec)
            if (f.rfind("-f", 0) == 0) {
                if (f.rfind("-fplugin", 0) == 0) return false;
                return true;
            }
            return false;
        };
        for (const auto& x : extra) {
            if (is_safe_extra_flag(x)) {
                argv.push_back(x);
            }
            // Silently drop disallowed flags (e.g. -fplugin=, -Xlinker, -Wl,)
        }

        // json-c flags (best-effort)
        auto jsonc_flags = pkg_config_jsonc_flags(lim);
        argv.insert(argv.end(), jsonc_flags.begin(), jsonc_flags.end());

        ProcResult r;
        bool started = proc_run_capture_sandboxed(argv, "", lim, &r);
        if (!started) {
            breaker_record_fail("compile", out_name);
            return {StepStatus::TOOL_ERROR, "{}", "compile failed to start: " + r.error};
        }
        if (r.exit_code != 0) {
            std::ostringstream jout;
            jout << "{\"ok\":false,\"exit_code\":" << r.exit_code
                 << ",\"timed_out\":" << (r.timed_out ? "true" : "false")
                 << ",\"output_truncated\":" << (r.output_truncated ? "true" : "false")
                 << ",\"output\":" << json_quote(r.output) << "}";
            write_artifact(ds_tmp, "genesis_compile_output", jout.str());
            std::ostringstream err;
            err << "compile failed (exit_code=" << r.exit_code << ")";
            breaker_record_fail("compile", out_name);
            return {StepStatus::TOOL_ERROR, "{}", err.str()};
        }

        // Compute digest of built plugin (best-effort)
        std::string bin = slurp_file(outp, 2 * 1024 * 1024);
        std::string sha = hash::sha256_hex(bin);

        breaker_record_success("compile", out_name);

        std::ostringstream out;
        out << "{\"ok\":true,"
            << "\"shared\":" << json_quote(outp.string()) << ","
            << "\"ext\":" << json_quote(ext) << ","
            << "\"sha256\":" << json_quote(sha) << ","
            << "\"exit_code\":0}";
        auto outj = out.str();
        write_artifact(ds_tmp, "genesis_compile", outj);
        {
            std::ostringstream st;
            st << "{\"stage\":\"COMPILED\",\"out_name\":" << json_quote(out_name)
               << ",\"shared\":" << json_quote(outp.string())
               << ",\"sha256\":" << json_quote(sha) << "}";
            set_stage(ds_tmp, "COMPILED", st.str());
        }
        return {StepStatus::OK, outj, ""};
#endif
    } catch (const std::exception& e) {
        return {StepStatus::TOOL_ERROR, "{}", e.what()};
    }
}

ToolResult tool_genesis_load_plugin(const std::string& input_json, DSState& ds_tmp) {
    try {
        if (!tool_allowed()) {
            return {StepStatus::TOOL_ERROR, "{}", "genesis disabled (set MACHINA_GENESIS_ENABLE=1 to enable)"};
        }
        if (!g_pm) {
            return {StepStatus::TOOL_ERROR, "{}", "plugin manager not configured"};
        }

        auto rel = json_mini::get_string(input_json, "plugin_relative_path").value_or("");
        if (rel.empty()) {
            // convenience: allow {"out_name":"foo"} and append platform extension
            auto out_name = json_mini::get_string(input_json, "out_name").value_or("");
            if (!out_name.empty()) rel = out_name + dynlib_ext();
        }
        if (rel.empty()) {
            return {StepStatus::TOOL_ERROR, "{}", "missing plugin_relative_path/out_name"};
        }
        if (rel.find("..") != std::string::npos) {
            return {StepStatus::TOOL_ERROR, "{}", "plugin_relative_path may not contain '..'"};
        }

        const auto plugin_base = g_root / "toolpacks" / "runtime_plugins";
        std::filesystem::create_directories(plugin_base);
        auto p = ensure_under(plugin_base, rel);

        // The registrar is owned by the runner (see runner/main.cpp).

        GenesisBreakerState bst;
        if (breaker_is_blocked("load", rel, &bst)) {
            std::ostringstream err;
            err << "genesis breaker OPEN for load(plugin_relative_path=" << rel << "): fail_count="
                << bst.fail_count << ", block_until_ms=" << bst.block_until_ms;
            breaker_record_fail("load", rel);
            return {StepStatus::TOOL_ERROR, "{}", err.str()};
        }


        // Hash verification: compute actual hash and verify against compile-stage hash
        std::string bin = slurp_file(p, 8 * 1024 * 1024);
        std::string sha = hash::sha256_hex(bin);

        // If a compile stage recorded an expected hash (DS6), verify it matches
        {
            auto ds6_it = ds_tmp.slots.find((uint8_t)DSSlot::DS6);
            if (ds6_it != ds_tmp.slots.end()) {
                const auto& stage_json = ds6_it->second.content_json;
                // Extract sha256 from stage payload: {"stage":"COMPILED",...,"sha256":"<hex>"}
                auto expected_sha = json_mini::get_string(stage_json, "sha256").value_or("");
                if (!expected_sha.empty() && !machina::constant_time_eq(sha, expected_sha)) {
                    return {StepStatus::TOOL_ERROR, "{}",
                            "genesis load: hash mismatch — plugin binary was modified after compile. "
                            "expected=" + expected_sha + " actual=" + sha};
                }
            }
        }

        // In-proc mode: also register hash with PluginManager for future loads
        if (g_pm) {
            try {
                g_pm->set_expected_hash(std::filesystem::weakly_canonical(p).string(), sha);
            } catch (...) {
                g_pm->set_expected_hash(p.string(), sha);
            }
        }

        // OOP plugin mode: do NOT dlopen into runner; load/execute via machina_toolhost.
        if (plugin_mode_oop()) {
            if (!g_core_reg || !g_core_runner) {
                return {StepStatus::TOOL_ERROR, "{}", "oop plugin mode requires registry/runner context"};
            }
            const std::filesystem::path th = toolhost_bin();
            std::vector<ToolDesc> tool_list;
            std::string terr;
            if (!toolhost_list(th, p, &tool_list, &terr)) {
                breaker_record_fail("load", rel);
                return {StepStatus::TOOL_ERROR, "{}", terr};
            }

            // Create persistent toolhost session pool.
            // Uses --serve NDJSON mode. Pool size: MACHINA_TOOLHOST_POOL_SIZE (default 2).
            int pool_sz = env_int("MACHINA_TOOLHOST_POOL_SIZE", 2);
            auto pool = std::make_shared<ToolHostPool>(p.string(), pool_sz);

            for (const auto& desc : tool_list) {
                g_core_reg->registerToolDesc(desc, g_allow_override);
                const std::string aid = desc.aid;

                g_core_runner->registerTool(desc.aid, [pool, aid](const std::string& in, DSState& ds) -> ToolResult {
                    auto lease = pool->acquire();
                    auto* session = lease.session;

                    // Try --serve mode (persistent NDJSON session with delta serialization)
                    if (session->pid > 0 || (session->fail_count < 3 && session->start())) {
                        json_object* req = json_object_new_object();
                        json_object_object_add(req, "aid", json_object_new_string(aid.c_str()));
                        json_object_object_add(req, "input_json", json_object_new_string_len(in.c_str(), (int)in.size()));
                        json_object_object_add(req, "ds_state", dsstate_to_json_delta(ds, &session->base_ds));
                        std::string line = json_object_to_json_string_ext(req, JSON_C_TO_STRING_PLAIN);
                        json_object_put(req);

                        std::string resp = session->send_receive(line, toolhost_proc_limits().timeout_ms);
                        if (!resp.empty()) {
                            json_object* out = json_tokener_parse(resp.c_str());
                            if (out) {
                                std::string status_s, output_json, error;
                                (void)json_get_string(out, "status", &status_s);
                                (void)json_get_string(out, "output_json", &output_json);
                                (void)json_get_string(out, "error", &error);
                                json_object* dsv = nullptr;
                                if (json_object_object_get_ex(out, "ds_state", &dsv)) {
                                    (void)dsstate_apply_delta(dsv, &ds);
                                }
                                session->base_ds = ds; // update base for next delta
                                json_object_put(out);
                                return {stepstatus_from_str(status_s), output_json.empty() ? "{}" : output_json, error};
                            }
                        }
                        // Session failed — shutdown and increment fail counter
                        session->shutdown();
                        session->fail_count++;
                        session->base_ds = DSState{};
                    }

                    // Fallback: fork-per-call via --run mode (full state, no delta)
                    json_object* req = json_object_new_object();
                    json_object_object_add(req, "input_json", json_object_new_string_len(in.c_str(), (int)in.size()));
                    json_object_object_add(req, "ds_state", dsstate_to_json(ds));
                    const std::string stdin_data = json_object_to_json_string_ext(req, JSON_C_TO_STRING_PLAIN);
                    json_object_put(req);

                    ProcResult pr;
                    ProcLimits lim = toolhost_proc_limits();
                    std::vector<std::string> argv = {toolhost_bin(), "--run", session->plugin_str, aid};
                    if (!proc_run_capture_sandboxed_stdin(argv, "", stdin_data, lim, &pr)) {
                        return {StepStatus::TOOL_ERROR, "{}", pr.error.empty() ? "toolhost: proc failed" : pr.error};
                    }
                    if (pr.exit_code != 0) {
                        return {StepStatus::TOOL_ERROR, "{}", "toolhost exit_code=" + std::to_string(pr.exit_code) + ": " + pr.output};
                    }
                    json_object* out = json_tokener_parse(pr.output.c_str());
                    if (!out) {
                        return {StepStatus::TOOL_ERROR, "{}", "toolhost: invalid JSON output"};
                    }
                    std::string status_s, output_json, error;
                    (void)json_get_string(out, "status", &status_s);
                    (void)json_get_string(out, "output_json", &output_json);
                    (void)json_get_string(out, "error", &error);
                    json_object* dsv = nullptr;
                    if (json_object_object_get_ex(out, "ds_state", &dsv)) {
                        DSState parsed;
                        (void)dsstate_from_json(dsv, &parsed);
                        ds = std::move(parsed);
                    }
                    json_object_put(out);
                    return {stepstatus_from_str(status_s), output_json.empty() ? "{}" : output_json, error};
                });
            }

            breaker_record_success("load", rel);

            std::ostringstream out;
            out << "{\"ok\":true,\"loaded\":" << json_quote(p.string())
                << ",\"sha256\":" << json_quote(sha)
                << ",\"mode\":" << json_quote("oop")
                << ",\"tool_count\":" << (int)tool_list.size() << "}";
            auto outj = out.str();
            write_artifact(ds_tmp, "genesis_load", outj);
            {
                std::ostringstream st;
                st << "{\"stage\":\"LOADED\",\"plugin\":" << json_quote(p.string())
                   << ",\"sha256\":" << json_quote(sha) << ",\"mode\":\"oop\"}";
                set_stage(ds_tmp, "LOADED", st.str());
            }
            return {StepStatus::OK, outj, ""};
        }

        // In-proc plugin mode (default)
        if (!g_registrar) {
            return {StepStatus::TOOL_ERROR, "{}", "registrar not configured"};
        }

        // Prod mode: ABI version check before loading
        if (genesis_prod_mode()) {
#ifndef _WIN32
            void* probe = dlopen(p.string().c_str(), RTLD_NOW | RTLD_LOCAL);
            if (probe) {
                dlerror(); // clear
                auto abi_fn = (machina_plugin_abi_version_fn)dlsym(probe, "machina_plugin_abi_version");
                if (!abi_fn) {
                    dlclose(probe);
                    return {StepStatus::TOOL_ERROR, "{}", "prod mode: plugin missing machina_plugin_abi_version() export"};
                }
                int plugin_abi = abi_fn();
                dlclose(probe);
                if (plugin_abi != MACHINA_ABI_VERSION) {
                    return {StepStatus::TOOL_ERROR, "{}",
                            "prod mode: ABI mismatch (plugin=" + std::to_string(plugin_abi)
                            + ", host=" + std::to_string(MACHINA_ABI_VERSION) + ")"};
                }
            } else {
                return {StepStatus::TOOL_ERROR, "{}", std::string("prod mode: cannot probe plugin: ") + (dlerror() ? dlerror() : "(unknown)")};
            }
#endif
        }

        std::string err;
        if (!g_pm->load_plugin(p, g_registrar, &err)) {
            breaker_record_fail("load", rel);
            return {StepStatus::TOOL_ERROR, "{}", err};
        }

        breaker_record_success("load", rel);

        std::ostringstream out;
        out << "{\"ok\":true,\"loaded\":" << json_quote(p.string())
            << ",\"sha256\":" << json_quote(sha) << "}";
        auto outj = out.str();
        write_artifact(ds_tmp, "genesis_load", outj);
        {
            std::ostringstream st;
            st << "{\"stage\":\"LOADED\",\"plugin\":" << json_quote(p.string())
               << ",\"sha256\":" << json_quote(sha) << "}";
            set_stage(ds_tmp, "LOADED", st.str());
        }
        return {StepStatus::OK, outj, ""};
    } catch (const std::exception& e) {
        return {StepStatus::TOOL_ERROR, "{}", e.what()};
    }
}

} // namespace machina
