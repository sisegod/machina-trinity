#include "machina/config.h"
#include <cstdlib>
#include <algorithm>
#include <cctype>

namespace machina {

Profile detect_profile() {
    const char* env = std::getenv("MACHINA_PROFILE");
    if (!env) return Profile::DEV;

    std::string val(env);
    std::transform(val.begin(), val.end(), val.begin(),
                   [](unsigned char c) { return std::tolower(c); });

    if (val == "prod" || val == "production") return Profile::PROD;
    return Profile::DEV;
}

const char* profile_name(Profile p) {
    switch (p) {
        case Profile::PROD: return "prod";
        case Profile::DEV:  return "dev";
    }
    return "dev";
}

void apply_profile_defaults(Profile p) {
    // SAFETY: Must be called before any worker threads are created.
    // setenv() is not thread-safe with getenv() on some platforms.
    // overwrite=0: won't override existing env vars
    constexpr int NO_OVERWRITE = 0;

    switch (p) {
        case Profile::DEV:
            setenv("MACHINA_WAL_FSYNC",         "0",     NO_OVERWRITE);
            setenv("MACHINA_GENESIS_ENABLE",    "1",     NO_OVERWRITE);
            setenv("MACHINA_SECCOMP_ENABLE",    "0",     NO_OVERWRITE);
            setenv("MACHINA_GENESIS_GUARD",     "0",     NO_OVERWRITE);
            setenv("MACHINA_SHELL_TIMEOUT_MS",  "30000", NO_OVERWRITE);
            setenv("MACHINA_POLICY_TIMEOUT_MS", "60000", NO_OVERWRITE);
            break;

        case Profile::PROD:
            setenv("MACHINA_WAL_FSYNC",         "1",     NO_OVERWRITE);
            setenv("MACHINA_GENESIS_ENABLE",    "0",     NO_OVERWRITE);
            setenv("MACHINA_SECCOMP_ENABLE",    "1",     NO_OVERWRITE);
            setenv("MACHINA_GENESIS_GUARD",     "1",     NO_OVERWRITE);
            setenv("MACHINA_SHELL_TIMEOUT_MS",  "10000", NO_OVERWRITE);
            setenv("MACHINA_POLICY_TIMEOUT_MS", "30000", NO_OVERWRITE);
            setenv("MACHINA_GENESIS_PROD_MODE", "1",     NO_OVERWRITE);
            // Network: default deny — must set explicit allowlist for prod
            setenv("MACHINA_HTTP_DEFAULT_DENY",  "1",    NO_OVERWRITE);
            // Toolhost: route side-effect tools through subprocess isolation
            setenv("MACHINA_TOOLHOST_ISOLATE",   "1",    NO_OVERWRITE);
            break;
    }
}

} // namespace machina
