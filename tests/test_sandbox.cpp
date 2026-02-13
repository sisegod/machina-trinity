#include "test_common.h"
#include "machina/sandbox.h"

#ifdef __linux__
#include <unistd.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <sys/prctl.h>
#endif

int main() {
    // Test 1: seccomp_available() should return a valid boolean
    bool avail = machina::seccomp_available();
    // On Linux this should be true; on other platforms false
#ifdef __linux__
    expect_true(avail, "seccomp should be available on Linux");
#else
    expect_true(!avail, "seccomp should not be available on non-Linux");
#endif

    // Test 2: install_seccomp_filter() — we can't fully test this in the
    // main test process because it would restrict the test runner itself.
    // Instead, verify the function exists and returns gracefully on
    // non-Linux platforms.
#ifndef __linux__
    std::string err = machina::install_seccomp_filter();
    expect_true(err.empty(), "install_seccomp_filter should no-op on non-Linux");
#endif

    // Test 3: Fork a child, install seccomp, verify it can still do basic I/O
#ifdef __linux__
    {
        pid_t pid = fork();
        if (pid == 0) {
            // Child: install seccomp then try write (should succeed)
            prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
            std::string err = machina::install_seccomp_filter();
            if (!err.empty()) _exit(1);
            // write() is in the allowlist, this should work
            const char* msg = "seccomp_ok\n";
            ssize_t n = write(STDOUT_FILENO, msg, 10);
            _exit(n > 0 ? 0 : 2);
        }
        int status = 0;
        waitpid(pid, &status, 0);
        expect_true(WIFEXITED(status) && WEXITSTATUS(status) == 0,
                    "child with seccomp should exit cleanly after write()");
    }
#endif

    std::cerr << "test_sandbox: ALL PASSED" << std::endl;
    return 0;
}
