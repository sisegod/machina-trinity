#pragma once

// Machina Sandbox — seccomp-BPF syscall allowlist for child processes.
//
// Design: allowlist-only. Any syscall NOT on the list triggers SIGSYS (kill).
// Opt-in via ProcLimits.enable_seccomp or MACHINA_SECCOMP_ENABLE=1.
//
// Architecture-aware: supports x86_64 and aarch64.

#include <string>

namespace machina {

// Install a seccomp-BPF filter that restricts the calling process to a safe
// subset of syscalls. Must be called AFTER prctl(PR_SET_NO_NEW_PRIVS, 1).
//
// The allowlist covers: read, write, open, close, stat, fstat, lstat, poll,
// mmap, mprotect(non-exec), munmap, brk, ioctl, access, pipe, select, sched_yield,
// dup, dup2, nanosleep, clock_gettime, getpid, exit, exit_group, wait4,
// getcwd, chdir, fchdir, rename, mkdir, rmdir, creat, unlink, readlink,
// lseek, getdents, fcntl, flock, fsync, ftruncate, getrlimit, sysinfo,
// times, uname, arch_prctl, set_tid_address, set_robust_list,
// futex, rt_sigaction, rt_sigprocmask, rt_sigreturn, sigaltstack, clone,
// execve (one-shot: only if already past exec — BPF can't enforce this
// perfectly, but no_new_privs + rlimits handle escalation).
//
// BLOCKED (notable): socket, connect, bind, listen, accept, sendto, recvfrom,
// ptrace, mount, umount, pivot_root, reboot, sethostname, setns, unshare,
// kexec_load, init_module, finit_module, personality.
//
// Returns empty string on success, error message on failure.
// On non-Linux platforms, returns success (no-op).
std::string install_seccomp_filter();

// Check if seccomp is available on this system.
bool seccomp_available();

} // namespace machina
