#include "machina/proc.h"
#include "machina/sandbox.h"

#include <cerrno>
#include <cstring>
#include <cctype>
#include <chrono>
#include <filesystem>

#ifndef _WIN32
  #include <unistd.h>
  #include <fcntl.h>
  #include <sys/stat.h>
  #include <sys/types.h>
  #include <sys/wait.h>
  #include <sys/resource.h>
  #include <poll.h>
  #ifdef __linux__
    #include <sys/prctl.h>
  #endif
#endif

namespace machina {

std::vector<std::string> split_argv_quoted(const std::string& cmd) {
    std::vector<std::string> out;
    std::string cur;
    enum { NORM, SQ, DQ } st = NORM;
    bool esc = false;

    auto flush = [&]() {
        if (!cur.empty()) {
            out.push_back(cur);
            cur.clear();
        }
    };

    for (size_t i = 0; i < cmd.size(); i++) {
        char c = cmd[i];
        if (st == NORM) {
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
                flush();
                continue;
            }
            if (c == '\'') { st = SQ; continue; }
            if (c == '"') { st = DQ; esc = false; continue; }
            cur.push_back(c);
        } else if (st == SQ) {
            if (c == '\'') { st = NORM; continue; }
            cur.push_back(c);
        } else { // DQ
            if (esc) {
                cur.push_back(c);
                esc = false;
                continue;
            }
            if (c == '\\') { esc = true; continue; }
            if (c == '"') { st = NORM; continue; }
            cur.push_back(c);
        }
    }
    if (st != NORM) return {};
    flush();
    return out;
}

#ifndef _WIN32
static void set_rlimit(int resource, rlim_t soft, rlim_t hard) {
    struct rlimit rl;
    rl.rlim_cur = soft;
    rl.rlim_max = hard;
    (void)setrlimit(resource, &rl);
}

#endif

bool proc_run_capture_sandboxed(const std::vector<std::string>& argv,
                               const std::string& cwd,
                               const ProcLimits& lim,
                               ProcResult* res) {
    if (!res) return false;
    *res = ProcResult{};

#ifdef _WIN32
    res->error = "proc_run_capture_sandboxed: not supported on Windows in this snapshot";
    return false;
#else
    if (argv.empty() || argv[0].empty()) {
        res->error = "empty argv";
        return false;
    }

    // Optional operator-provided wrapper (e.g., nsjail/firejail/bwrap).
    // Disabled by default; enable explicitly to avoid surprising behavior.
    auto env_true = [](const char* key) -> bool {
        const char* v = std::getenv(key);
        if (!v) return false;
        std::string s = v;
        for (auto& c : s) c = (char)std::tolower((unsigned char)c);
        return (s == "1" || s == "true" || s == "yes" || s == "on");
    };

    std::vector<std::string> eff_argv = argv;
    if (env_true("MACHINA_PROC_WRAPPER_ENABLE")) {
        if (const char* w = std::getenv("MACHINA_PROC_WRAPPER")) {
            auto toks = split_argv_quoted(w);
            if (!toks.empty()) {
                // Prepend wrapper tokens to argv.
                std::vector<std::string> merged;
                merged.reserve(toks.size() + eff_argv.size());
                merged.insert(merged.end(), toks.begin(), toks.end());
                merged.insert(merged.end(), eff_argv.begin(), eff_argv.end());
                eff_argv.swap(merged);
            }
        }
    }

    int pipefd[2];
    if (pipe(pipefd) != 0) {
        res->error = std::string("pipe failed: ") + std::strerror(errno);
        return false;
    }

    // Make read end non-blocking
    int flags = fcntl(pipefd[0], F_GETFL, 0);
    if (flags >= 0) (void)fcntl(pipefd[0], F_SETFL, flags | O_NONBLOCK);

    pid_t pid = fork();
    if (pid < 0) {
        close(pipefd[0]); close(pipefd[1]);
        res->error = std::string("fork failed: ") + std::strerror(errno);
        return false;
    }

    if (pid == 0) {
        // child
        (void)dup2(pipefd[1], STDOUT_FILENO);
        (void)dup2(pipefd[1], STDERR_FILENO);
        close(pipefd[0]);
        close(pipefd[1]);

        // isolate process group so timeout can kill the whole subtree
        (void)setpgid(0, 0);

        // tighten default file permissions for any files created by the child
        (void)umask(077);

        // best-effort: close inherited fds beyond stdin/stdout/stderr
        long maxfd = sysconf(_SC_OPEN_MAX);
        if (maxfd < 256) maxfd = 256;
        for (int fd = 3; fd < maxfd; fd++) {
            (void)close(fd);
        }

        // best-effort sandboxing
        if (!cwd.empty()) {
            (void)chdir(cwd.c_str());
        }

        // scrub dangerous loader env vars
        unsetenv("LD_PRELOAD");
        unsetenv("LD_LIBRARY_PATH");

#ifdef __linux__
        if (lim.no_new_privs) {
            (void)prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
        }
        (void)prctl(PR_SET_PDEATHSIG, SIGKILL);
#endif

        if (lim.rlimit_cpu_sec > 0) {
            set_rlimit(RLIMIT_CPU, (rlim_t)lim.rlimit_cpu_sec, (rlim_t)lim.rlimit_cpu_sec);
        }
        if (lim.rlimit_as_mb > 0) {
            rlim_t bytes = (rlim_t)lim.rlimit_as_mb * 1024ULL * 1024ULL;
            set_rlimit(RLIMIT_AS, bytes, bytes);
        }
        if (lim.rlimit_fsize_mb > 0) {
            rlim_t bytes = (rlim_t)lim.rlimit_fsize_mb * 1024ULL * 1024ULL;
            set_rlimit(RLIMIT_FSIZE, bytes, bytes);
        }
        if (lim.rlimit_nofile > 0) {
            set_rlimit(RLIMIT_NOFILE, (rlim_t)lim.rlimit_nofile, (rlim_t)lim.rlimit_nofile);
        }
#ifdef RLIMIT_NPROC
        if (lim.rlimit_nproc > 0) {
            set_rlimit(RLIMIT_NPROC, (rlim_t)lim.rlimit_nproc, (rlim_t)lim.rlimit_nproc);
        }
#endif

        // seccomp-BPF: install syscall allowlist (must come after no_new_privs)
        if (lim.enable_seccomp) {
            (void)install_seccomp_filter(); // best-effort; errors are non-fatal
        }

        // build argv
        std::vector<char*> cargv;
        cargv.reserve(eff_argv.size() + 1);
        for (const auto& s : eff_argv) cargv.push_back(const_cast<char*>(s.c_str()));
        cargv.push_back(nullptr);

        // exec
        execvp(cargv[0], cargv.data());
        // if exec fails
        _exit(127);
    }

    // parent
    (void)setpgid(pid, pid);
    close(pipefd[1]);

    auto start = std::chrono::steady_clock::now();
    std::string out;
    out.reserve(std::min<size_t>(lim.stdout_max_bytes, 64 * 1024));

    bool child_exited = false;
    int status = 0;

    while (true) {
        // read available output
        char buf[4096];
        while (true) {
            ssize_t n = read(pipefd[0], buf, sizeof(buf));
            if (n > 0) {
                size_t can = lim.stdout_max_bytes > out.size() ? (lim.stdout_max_bytes - out.size()) : 0;
                if (can > 0) {
                    size_t take = (size_t)n;
                    if (take > can) {
                        take = can;
                        res->output_truncated = true;
                    }
                    out.append(buf, buf + take);
                } else {
                    res->output_truncated = true;
                }
                continue;
            }
            if (n == -1 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
            if (n == 0) break; // EOF
            break;
        }

        // check child
        pid_t w = waitpid(pid, &status, WNOHANG);
        if (w == pid) {
            child_exited = true;
            break;
        }

        // timeout?
        auto now = std::chrono::steady_clock::now();
        int elapsed_ms = (int)std::chrono::duration_cast<std::chrono::milliseconds>(now - start).count();
        if (lim.timeout_ms > 0 && elapsed_ms > lim.timeout_ms) {
            res->timed_out = true;
            // kill process group first (best-effort), then the direct pid
            (void)kill(-pid, SIGKILL);
            (void)kill(pid, SIGKILL);
            (void)waitpid(pid, &status, 0);
            child_exited = true;
            break;
        }

        // wait for more output or child exit
        struct pollfd pfd;
        pfd.fd = pipefd[0];
        pfd.events = POLLIN;
        int slice = 50;
        if (lim.timeout_ms > 0) {
            int remaining = lim.timeout_ms - elapsed_ms;
            if (remaining < slice) slice = std::max(1, remaining);
        }
        (void)poll(&pfd, 1, slice);
    }

    // drain any remaining output
    while (true) {
        char buf[4096];
        ssize_t n = read(pipefd[0], buf, sizeof(buf));
        if (n > 0) {
            size_t can = lim.stdout_max_bytes > out.size() ? (lim.stdout_max_bytes - out.size()) : 0;
            if (can > 0) {
                size_t take = (size_t)n;
                if (take > can) {
                    take = can;
                    res->output_truncated = true;
                }
                out.append(buf, buf + take);
            } else {
                res->output_truncated = true;
            }
            continue;
        }
        break;
    }
    close(pipefd[0]);

    res->output = std::move(out);
    if (!child_exited) {
        res->exit_code = 128;
        res->error = "child did not exit";
        return true;
    }

    if (WIFEXITED(status)) res->exit_code = WEXITSTATUS(status);
    else if (WIFSIGNALED(status)) res->exit_code = 128 + WTERMSIG(status);
    else res->exit_code = 128;

    return true;
#endif
}



bool proc_run_capture_sandboxed_stdin(const std::vector<std::string>& argv,
                                     const std::string& cwd,
                                     const std::string& stdin_data,
                                     const ProcLimits& lim,
                                     ProcResult* res) {
    if (!res) return false;
    *res = ProcResult{};

#ifdef _WIN32
    res->error = "proc_run_capture_sandboxed_stdin: not supported on Windows in this snapshot";
    return false;
#else
    if (argv.empty() || argv[0].empty()) {
        res->error = "empty argv";
        return false;
    }

    // Optional operator-provided wrapper (e.g., nsjail/firejail/bwrap).
    auto env_true = [](const char* key) -> bool {
        const char* v = std::getenv(key);
        if (!v) return false;
        std::string s = v;
        for (auto& c : s) c = (char)std::tolower((unsigned char)c);
        return (s == "1" || s == "true" || s == "yes" || s == "on");
    };

    std::vector<std::string> eff_argv = argv;
    if (env_true("MACHINA_PROC_WRAPPER_ENABLE")) {
        if (const char* w = std::getenv("MACHINA_PROC_WRAPPER")) {
            auto toks = split_argv_quoted(w);
            if (!toks.empty()) {
                std::vector<std::string> merged;
                merged.reserve(toks.size() + eff_argv.size());
                merged.insert(merged.end(), toks.begin(), toks.end());
                merged.insert(merged.end(), eff_argv.begin(), eff_argv.end());
                eff_argv.swap(merged);
            }
        }
    }

    int out_pipe[2];
    if (pipe(out_pipe) != 0) {
        res->error = std::string("pipe(out) failed: ") + std::strerror(errno);
        return false;
    }

    int in_pipe[2];
    if (pipe(in_pipe) != 0) {
        close(out_pipe[0]); close(out_pipe[1]);
        res->error = std::string("pipe(in) failed: ") + std::strerror(errno);
        return false;
    }

    // Make read end non-blocking
    int flags = fcntl(out_pipe[0], F_GETFL, 0);
    if (flags >= 0) (void)fcntl(out_pipe[0], F_SETFL, flags | O_NONBLOCK);

    pid_t pid = fork();
    if (pid < 0) {
        close(out_pipe[0]); close(out_pipe[1]);
        close(in_pipe[0]); close(in_pipe[1]);
        res->error = std::string("fork failed: ") + std::strerror(errno);
        return false;
    }

    if (pid == 0) {
        // child
        (void)dup2(in_pipe[0], STDIN_FILENO);
        (void)dup2(out_pipe[1], STDOUT_FILENO);
        (void)dup2(out_pipe[1], STDERR_FILENO);

        close(out_pipe[0]);
        close(out_pipe[1]);
        close(in_pipe[0]);
        close(in_pipe[1]);

        (void)setpgid(0, 0);
        (void)umask(077);

        long maxfd = sysconf(_SC_OPEN_MAX);
        if (maxfd < 256) maxfd = 256;
        for (int fd = 3; fd < maxfd; fd++) {
            (void)close(fd);
        }

        if (!cwd.empty()) {
            (void)chdir(cwd.c_str());
        }

        unsetenv("LD_PRELOAD");
        unsetenv("LD_LIBRARY_PATH");

#ifdef __linux__
        if (lim.no_new_privs) {
            (void)prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
        }
        (void)prctl(PR_SET_PDEATHSIG, SIGKILL);
#endif

        if (lim.rlimit_cpu_sec > 0) {
            set_rlimit(RLIMIT_CPU, (rlim_t)lim.rlimit_cpu_sec, (rlim_t)lim.rlimit_cpu_sec);
        }
        if (lim.rlimit_as_mb > 0) {
            rlim_t bytes = (rlim_t)lim.rlimit_as_mb * 1024ULL * 1024ULL;
            set_rlimit(RLIMIT_AS, bytes, bytes);
        }
        if (lim.rlimit_fsize_mb > 0) {
            rlim_t bytes = (rlim_t)lim.rlimit_fsize_mb * 1024ULL * 1024ULL;
            set_rlimit(RLIMIT_FSIZE, bytes, bytes);
        }
        if (lim.rlimit_nofile > 0) {
            set_rlimit(RLIMIT_NOFILE, (rlim_t)lim.rlimit_nofile, (rlim_t)lim.rlimit_nofile);
        }
#ifdef RLIMIT_NPROC
        if (lim.rlimit_nproc > 0) {
            set_rlimit(RLIMIT_NPROC, (rlim_t)lim.rlimit_nproc, (rlim_t)lim.rlimit_nproc);
        }
#endif

        // seccomp-BPF: install syscall allowlist (must come after no_new_privs)
        if (lim.enable_seccomp) {
            (void)install_seccomp_filter();
        }

        std::vector<char*> cargv;
        cargv.reserve(eff_argv.size() + 1);
        for (const auto& ss : eff_argv) cargv.push_back(const_cast<char*>(ss.c_str()));
        cargv.push_back(nullptr);

        execvp(cargv[0], cargv.data());
        _exit(127);
    }

    // parent
    (void)setpgid(pid, pid);
    close(out_pipe[1]);
    close(in_pipe[0]);

    // Interleaved stdin write + stdout read using poll() to prevent pipe deadlock.
    // Without this, large stdin payloads can deadlock: parent blocks on write(stdin)
    // while child blocks on write(stdout) because nobody is reading stdout.
    int in_fd = in_pipe[1];
    // Set stdin pipe to non-blocking for poll-based interleaving
    if (!stdin_data.empty()) {
        int flags = fcntl(in_fd, F_GETFL, 0);
        if (flags >= 0) fcntl(in_fd, F_SETFL, flags | O_NONBLOCK);
    } else {
        close(in_fd);
        in_fd = -1;
    }
    size_t write_off = 0;

    auto start = std::chrono::steady_clock::now();
    std::string out;
    out.reserve(std::min<size_t>(lim.stdout_max_bytes, 64 * 1024));

    bool child_exited = false;
    int status = 0;

    auto append_stdout = [&](const char* buf, ssize_t n) {
        size_t can = lim.stdout_max_bytes > out.size() ? (lim.stdout_max_bytes - out.size()) : 0;
        if (can > 0) {
            size_t take = (size_t)n;
            if (take > can) { take = can; res->output_truncated = true; }
            out.append(buf, buf + take);
        } else {
            res->output_truncated = true;
        }
    };

    while (true) {
        struct pollfd fds[2];
        int nfds = 0;
        int in_idx = -1, out_idx = -1;
        if (in_fd >= 0) {
            in_idx = nfds;
            fds[nfds].fd = in_fd;
            fds[nfds].events = POLLOUT;
            nfds++;
        }
        out_idx = nfds;
        fds[nfds].fd = out_pipe[0];
        fds[nfds].events = POLLIN;
        nfds++;

        int elapsed_ms = (int)std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start).count();
        int slice = 100;
        if (lim.timeout_ms > 0) {
            int remaining = lim.timeout_ms - elapsed_ms;
            if (remaining <= 0) {
                res->timed_out = true;
                (void)kill(-pid, SIGKILL);
                (void)kill(pid, SIGKILL);
                (void)waitpid(pid, &status, 0);
                child_exited = true;
                break;
            }
            if (remaining < slice) slice = remaining;
        }

        int pr = poll(fds, (nfds_t)nfds, slice);
        if (pr < 0 && errno == EINTR) continue;

        // Handle stdin write
        if (in_idx >= 0 && (fds[in_idx].revents & (POLLOUT | POLLERR | POLLHUP))) {
            while (write_off < stdin_data.size()) {
                ssize_t n = write(in_fd, stdin_data.data() + write_off, stdin_data.size() - write_off);
                if (n > 0) { write_off += (size_t)n; continue; }
                if (n == -1 && errno == EINTR) continue;
                if (n == -1 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
                write_off = stdin_data.size(); // error — stop writing
                break;
            }
            if (write_off >= stdin_data.size()) {
                close(in_fd);
                in_fd = -1;
            }
        }

        // Handle stdout read
        if (fds[out_idx].revents & POLLIN) {
            char buf[4096];
            ssize_t n = read(out_pipe[0], buf, sizeof(buf));
            if (n > 0) append_stdout(buf, n);
        }
        if (fds[out_idx].revents & (POLLERR | POLLHUP)) {
            // Pipe closed — drain remaining
            char buf[4096];
            while (true) {
                ssize_t n = read(out_pipe[0], buf, sizeof(buf));
                if (n > 0) { append_stdout(buf, n); continue; }
                break;
            }
        }

        // Check child exit
        pid_t w = waitpid(pid, &status, WNOHANG);
        if (w == pid) {
            child_exited = true;
            break;
        }
    }

    // Close stdin if still open
    if (in_fd >= 0) close(in_fd);

    // Drain any remaining output after child exits
    while (true) {
        char buf[4096];
        ssize_t n = read(out_pipe[0], buf, sizeof(buf));
        if (n > 0) { append_stdout(buf, n); continue; }
        break;
    }
    close(out_pipe[0]);

    res->output = out;
    if (child_exited) {
        if (WIFEXITED(status)) res->exit_code = WEXITSTATUS(status);
        else if (WIFSIGNALED(status)) res->exit_code = 128 + WTERMSIG(status);
        else res->exit_code = 127;
    } else {
        res->exit_code = 127;
    }

    return true;
#endif
}

} // namespace machina
