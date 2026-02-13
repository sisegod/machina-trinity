#pragma once

#include <string>
#include <vector>
#include <optional>

namespace machina {

struct ProcLimits {
    int timeout_ms{2000};
    size_t stdout_max_bytes{64 * 1024};

    int rlimit_cpu_sec{2};          // CPU time seconds
    size_t rlimit_as_mb{512};       // virtual memory MB
    size_t rlimit_fsize_mb{10};     // max file size MB
    int rlimit_nofile{64};          // max open fds
    int rlimit_nproc{32};           // max processes (best-effort)

    bool no_new_privs{true};

    // seccomp-BPF syscall allowlist (Linux only, requires no_new_privs).
    // Opt-in: set to true or MACHINA_SECCOMP_ENABLE=1.
    bool enable_seccomp{false};
};

struct ProcResult {
    int exit_code{127};
    bool timed_out{false};
    bool output_truncated{false};
    std::string output; // stdout+stderr merged
    std::string error;  // internal runner error, not child stderr
};

// Run a process (argv[0] is executable), capture stdout+stderr (merged),
// enforce timeout and rlimits (POSIX best-effort). Returns true if process started.
bool proc_run_capture_sandboxed(const std::vector<std::string>& argv,
                               const std::string& cwd,
                               const ProcLimits& lim,
                               ProcResult* res);

// Run a process and provide stdin data. Captures stdout+stderr (merged),
// enforce timeout and rlimits (POSIX best-effort). Returns true if process started.
bool proc_run_capture_sandboxed_stdin(const std::vector<std::string>& argv,
                                     const std::string& cwd,
                                     const std::string& stdin_data,
                                     const ProcLimits& lim,
                                     ProcResult* res);


// Small helper: split a command string into argv tokens.
// Supports basic quotes (single/double) and backslash escaping inside double quotes.
// Returns empty vector on parse error.
std::vector<std::string> split_argv_quoted(const std::string& cmd);

} // namespace machina
