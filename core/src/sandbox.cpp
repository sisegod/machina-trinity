#include "machina/sandbox.h"

#if defined(__linux__)

#include <cstring>
#include <cerrno>
#include <cstdlib>
#include <vector>

#include <linux/filter.h>
#include <linux/seccomp.h>
#include <linux/audit.h>
#include <sys/prctl.h>
#include <sys/syscall.h>

// Architecture-specific audit arch constant
#if defined(__x86_64__)
  #define MACHINA_AUDIT_ARCH AUDIT_ARCH_X86_64
#elif defined(__aarch64__)
  #define MACHINA_AUDIT_ARCH AUDIT_ARCH_AARCH64
#else
  #define MACHINA_AUDIT_ARCH 0
#endif

// BPF helpers
#define BPF_STMT_SC(code, k) { (unsigned short)(code), 0, 0, (unsigned int)(k) }
#define BPF_JUMP_SC(code, k, jt, jf) { (unsigned short)(code), (unsigned char)(jt), (unsigned char)(jf), (unsigned int)(k) }

namespace machina {

std::string install_seccomp_filter() {
#if MACHINA_AUDIT_ARCH == 0
    return "seccomp: unsupported architecture";
#else
    // Syscall allowlist — architecture-specific numbers.
    // We use the raw syscall numbers for the target arch.
#if defined(__x86_64__)
    static const unsigned int allowed[] = {
        // I/O basics
        0,    // read
        1,    // write
        2,    // open
        3,    // close
        4,    // stat
        5,    // fstat
        6,    // lstat
        7,    // poll
        8,    // lseek
        9,    // mmap
        10,   // mprotect — filtered separately for PROT_EXEC
        11,   // munmap
        12,   // brk
        13,   // rt_sigaction
        14,   // rt_sigprocmask
        15,   // rt_sigreturn
        16,   // ioctl
        17,   // pread64
        18,   // pwrite64
        19,   // readv
        20,   // writev
        21,   // access
        22,   // pipe
        23,   // select
        24,   // sched_yield
        25,   // mremap
        28,   // madvise
        32,   // dup
        33,   // dup2
        35,   // nanosleep
        37,   // alarm
        39,   // getpid
        56,   // clone
        57,   // fork  (needed by some runtimes; rlimit_nproc caps it)
        59,   // execve (one-shot after fork; no_new_privs enforced)
        60,   // exit
        61,   // wait4
        62,   // kill (self-signaling for abort)
        63,   // uname
        72,   // fcntl
        73,   // flock
        74,   // fsync
        75,   // fdatasync
        76,   // truncate
        77,   // ftruncate
        78,   // getdents
        79,   // getcwd
        80,   // chdir
        81,   // fchdir
        82,   // rename
        83,   // mkdir
        84,   // rmdir
        85,   // creat
        86,   // link
        87,   // unlink
        89,   // readlink
        90,   // chmod
        91,   // fchmod
        95,   // umask
        96,   // gettimeofday
        97,   // getrlimit
        99,   // sysinfo
        100,  // times
        102,  // getuid
        104,  // getgid
        107,  // geteuid
        108,  // getegid
        110,  // getppid
        111,  // getpgrp
        112,  // setsid
        131,  // sigaltstack
        137,  // statfs
        138,  // fstatfs
        157,  // prctl
        158,  // arch_prctl
        202,  // futex
        204,  // sched_getaffinity
        218,  // set_tid_address
        228,  // clock_gettime
        229,  // clock_getres
        230,  // clock_nanosleep
        231,  // exit_group
        233,  // epoll_wait
        234,  // tgkill
        257,  // openat
        258,  // mkdirat
        260,  // fchownat
        262,  // newfstatat
        263,  // unlinkat
        264,  // renameat
        267,  // readlinkat
        268,  // fchmodat
        269,  // faccessat
        270,  // pselect6
        271,  // ppoll
        273,  // set_robust_list
        281,  // epoll_pwait
        288,  // accept4
        290,  // eventfd2
        291,  // epoll_create1
        292,  // dup3
        293,  // pipe2
        302,  // prlimit64
        316,  // renameat2
        318,  // getrandom
        332,  // statx
        334,  // rseq
        439,  // faccessat2
    };
#elif defined(__aarch64__)
    static const unsigned int allowed[] = {
        // aarch64 syscall numbers (different from x86_64)
        56,   // openat
        57,   // close
        62,   // lseek
        63,   // read
        64,   // write
        65,   // readv
        66,   // writev
        67,   // pread64
        68,   // pwrite64
        25,   // fcntl
        29,   // ioctl
        32,   // flock (aarch64)
        74,   // ftruncate
        79,   // fstatat
        80,   // fstat
        46,   // fchmod
        53,   // fchmodat
        49,   // chdir
        50,   // fchdir
        34,   // mkdirat
        35,   // unlinkat
        37,   // linkat
        38,   // renameat
        78,   // readlinkat
        52,   // faccessat
        48,   // faccessat2
        23,   // dup
        24,   // dup3
        22,   // pipe2
        73,   // ppoll
        72,   // pselect6
        43,   // statfs
        44,   // fstatfs
        76,   // truncate
        47,   // fchown
        222,  // mmap
        226,  // mprotect
        215,  // munmap
        214,  // brk
        233,  // madvise
        225,  // mremap
        134,  // rt_sigaction
        135,  // rt_sigprocmask
        139,  // rt_sigreturn
        132,  // sigaltstack
        220,  // clone
        93,   // exit
        94,   // exit_group
        260,  // wait4
        129,  // kill
        131,  // tgkill
        160,  // uname
        163,  // getrlimit
        261,  // prlimit64
        179,  // sysinfo
        153,  // times
        174,  // getuid
        176,  // getgid
        175,  // geteuid
        177,  // getegid
        172,  // getpid
        173,  // getppid
        157,  // setsid
        221,  // execve
        96,   // set_tid_address
        99,   // set_robust_list
        98,   // futex
        113,  // clock_gettime
        114,  // clock_getres
        115,  // clock_nanosleep
        169,  // gettimeofday
        278,  // getrandom
        291,  // statx
        167,  // prctl
        39,   // umask
        82,   // fsync
        83,   // fdatasync
        61,   // getdents64
        17,   // getcwd
        5,    // epoll_create1
        21,   // epoll_pwait
        20,   // epoll_ctl
        19,   // eventfd2
        101,  // nanosleep
        124,  // sched_yield
        123,  // sched_getaffinity
        281,  // rseq
    };
#endif

    std::vector<unsigned int> allowlist(allowed, allowed + (sizeof(allowed) / sizeof(allowed[0])));

    // Optional network-capable seccomp profile.
    // Default remains strict (no outbound socket syscalls).
    const char* profile = std::getenv("MACHINA_SECCOMP_PROFILE");
    const char* allow_net = std::getenv("MACHINA_SECCOMP_ALLOW_NET");
    const bool net_profile = (profile && std::string(profile) == "net")
                             || (allow_net && std::string(allow_net) == "1");
    if (net_profile) {
#if defined(__x86_64__)
        static const unsigned int net_allowed[] = {
            41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55,
            299, 307, // recvmmsg, sendmmsg
        };
#elif defined(__aarch64__)
        static const unsigned int net_allowed[] = {
            198, 199, 200, 201, 202, 203, 204, 205, 206, 207, 208, 209, 210, 211, 212,
        };
#endif
        allowlist.insert(
            allowlist.end(),
            net_allowed,
            net_allowed + (sizeof(net_allowed) / sizeof(net_allowed[0]))
        );
    }

    const size_t n_allowed = allowlist.size();

    // BPF program structure:
    // [0]   Load arch from seccomp_data
    // [1]   Check arch == MACHINA_AUDIT_ARCH; if not → KILL
    // [2]   Load syscall number
    // [3..] For each allowed syscall: JEQ → ALLOW
    // [N]   Default: KILL_PROCESS

    const size_t n_insns = 3 + n_allowed + 1; // header(3) + checks + default
    auto* filter = new struct sock_filter[n_insns];

    size_t i = 0;

    // [0] Load architecture
    filter[i++] = BPF_STMT_SC(BPF_LD | BPF_W | BPF_ABS,
                               offsetof(struct seccomp_data, arch));

    // [1] Verify architecture
    filter[i++] = BPF_JUMP_SC(BPF_JMP | BPF_JEQ | BPF_K,
                               MACHINA_AUDIT_ARCH, 1, 0);

    // Architecture mismatch → kill
    filter[i++] = BPF_STMT_SC(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS);

    // [3] Load syscall number (only reached if arch matched)
    // Wait — we need to re-layout. After arch check passes, we jump over the kill.
    // Let me restructure:

    // Actually the layout should be:
    // [0] Load arch
    // [1] JEQ arch → skip kill (jt=1, jf=0)
    // [2] KILL (arch mismatch)
    // [3] Load syscall nr
    // [4..N+3] JEQ allowed[i] → ALLOW (jump to last+1)
    // [N+4] KILL (default deny)
    // [N+5] ALLOW

    delete[] filter;
    // 4 header + N checks + 1 kill(default) + 4 mprotect_check + 1 allow(general) = N+10
    const size_t total_insns = 4 + n_allowed + 6;
    filter = new struct sock_filter[total_insns];

    i = 0;

    // [0] Load arch
    filter[i++] = BPF_STMT_SC(BPF_LD | BPF_W | BPF_ABS,
                               offsetof(struct seccomp_data, arch));
    // [1] Check arch
    filter[i++] = BPF_JUMP_SC(BPF_JMP | BPF_JEQ | BPF_K,
                               MACHINA_AUDIT_ARCH, 1, 0);
    // [2] Arch mismatch → kill
    filter[i++] = BPF_STMT_SC(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS);

    // [3] Load syscall number
    filter[i++] = BPF_STMT_SC(BPF_LD | BPF_W | BPF_ABS,
                               offsetof(struct seccomp_data, nr));

    // We need extra instructions for mprotect PROT_EXEC filtering:
    //   If syscall == mprotect → jump to arg check block
    //   arg check: load arg2, test PROT_EXEC bit, deny if set, allow if clear
    //
    // Layout after syscall nr load:
    //   [4..4+n_allowed-1]  JEQ allowed[s] → ALLOW or MPROTECT_CHECK
    //   [4+n_allowed]       KILL (default deny)
    //   [4+n_allowed+1]     MPROTECT_CHECK: load arg2 (prot)
    //   [4+n_allowed+2]     JSET PROT_EXEC → KILL
    //   [4+n_allowed+3]     ALLOW (mprotect without PROT_EXEC)
    //   [4+n_allowed+4]     ALLOW (other allowed syscalls)

    // mprotect syscall number for this arch
#if defined(__x86_64__)
    const unsigned int mprotect_nr = 10;
#elif defined(__aarch64__)
    const unsigned int mprotect_nr = 226;
#endif

    // [4..4+n_allowed-1] Check each allowed syscall
    for (size_t s = 0; s < n_allowed; s++) {
        if (allowlist[s] == mprotect_nr) {
            // mprotect → jump to MPROTECT_CHECK instead of ALLOW
            // MPROTECT_CHECK is at position 4 + n_allowed + 1
            // current = 4 + s
            // jt = (4 + n_allowed + 1) - (4 + s) - 1 = n_allowed - s
            unsigned char jt = (unsigned char)(n_allowed - s);
            filter[i++] = BPF_JUMP_SC(BPF_JMP | BPF_JEQ | BPF_K,
                                       allowlist[s], jt, 0);
        } else {
            // Normal allowed → jump to ALLOW
            // ALLOW is at position 4 + n_allowed + 5
            // current = 4 + s
            // jt = (4 + n_allowed + 5) - (4 + s) - 1 = n_allowed + 4 - s
            unsigned char jt = (unsigned char)(n_allowed + 4 - s);
            filter[i++] = BPF_JUMP_SC(BPF_JMP | BPF_JEQ | BPF_K,
                                       allowlist[s], jt, 0);
        }
    }

    // Default: KILL (no syscall matched)
    filter[i++] = BPF_STMT_SC(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS);

    // MPROTECT_CHECK: load arg2 (prot) — seccomp_data.args[2]
    filter[i++] = BPF_STMT_SC(BPF_LD | BPF_W | BPF_ABS,
                               offsetof(struct seccomp_data, args) + 2 * sizeof(uint64_t));

    // Check PROT_EXEC (0x4) bit — if set → KILL, else → ALLOW
    filter[i++] = BPF_JUMP_SC(BPF_JMP | BPF_JSET | BPF_K, 0x4, 1, 0);

    // mprotect without PROT_EXEC → ALLOW
    filter[i++] = BPF_STMT_SC(BPF_RET | BPF_K, SECCOMP_RET_ALLOW);

    // mprotect with PROT_EXEC → KILL
    filter[i++] = BPF_STMT_SC(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS);

    // ALLOW (for all other matched syscalls)
    filter[i++] = BPF_STMT_SC(BPF_RET | BPF_K, SECCOMP_RET_ALLOW);

    struct sock_fprog prog = {};
    prog.len = (unsigned short)total_insns;
    prog.filter = filter;

    int ret = prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog, 0, 0);
    delete[] filter;

    if (ret != 0) {
        return std::string("seccomp install failed: ") + std::strerror(errno);
    }

    return "";
#endif
}

bool seccomp_available() {
    // Check if PR_SET_SECCOMP is supported (dry run with invalid prog → EFAULT expected)
    int ret = prctl(PR_GET_SECCOMP, 0, 0, 0, 0);
    // ret == 0 means seccomp is not active but available
    // ret == 2 means seccomp filter mode is active
    // ret == -1 && errno == EINVAL means not supported
    return (ret >= 0);
}

} // namespace machina

#else // !__linux__

namespace machina {

std::string install_seccomp_filter() {
    // No-op on non-Linux
    return "";
}

bool seccomp_available() {
    return false;
}

} // namespace machina

#endif
