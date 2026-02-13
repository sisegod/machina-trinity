#include "test_common.h"
#include "machina/config.h"
#include <cstdlib>

int main() {
    // Test 1: Default profile is DEV
    unsetenv("MACHINA_PROFILE");
    auto p = machina::detect_profile();
    expect_true(p == machina::Profile::DEV, "default should be DEV");

    // Test 2: PROD detection
    setenv("MACHINA_PROFILE", "prod", 1);
    p = machina::detect_profile();
    expect_true(p == machina::Profile::PROD, "should detect PROD");

    // Test 3: Case insensitive
    setenv("MACHINA_PROFILE", "PROD", 1);
    p = machina::detect_profile();
    expect_true(p == machina::Profile::PROD, "should detect PROD case-insensitive");

    // Test 4: Apply defaults (won't override existing)
    setenv("MACHINA_WAL_FSYNC", "42", 1); // pre-existing
    machina::apply_profile_defaults(machina::Profile::PROD);
    std::string val = std::getenv("MACHINA_WAL_FSYNC") ? std::getenv("MACHINA_WAL_FSYNC") : "";
    expect_true(val == "42", "should NOT override pre-existing env var");

    // Test 5: Apply sets missing vars
    unsetenv("MACHINA_GENESIS_GUARD");
    machina::apply_profile_defaults(machina::Profile::PROD);
    val = std::getenv("MACHINA_GENESIS_GUARD") ? std::getenv("MACHINA_GENESIS_GUARD") : "";
    expect_true(val == "1", "PROD should set GENESIS_GUARD=1");

    // Test 6: Profile name
    expect_true(std::string(machina::profile_name(machina::Profile::DEV)) == "dev", "dev name");
    expect_true(std::string(machina::profile_name(machina::Profile::PROD)) == "prod", "prod name");

    // Cleanup
    unsetenv("MACHINA_PROFILE");
    unsetenv("MACHINA_WAL_FSYNC");
    unsetenv("MACHINA_GENESIS_GUARD");

    std::cerr << "test_config: ALL PASSED" << std::endl;
    return 0;
}
