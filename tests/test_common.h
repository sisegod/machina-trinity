#pragma once

#include <cstdlib>
#include <iostream>
#include <string>

inline void die(const std::string& msg) {
    std::cerr << "TEST FAIL: " << msg << std::endl;
    std::exit(1);
}

inline void expect_true(bool cond, const std::string& msg) {
    if (!cond) die(msg);
}

inline void expect_eq_ll(long long a, long long b, const std::string& msg) {
    if (a != b) {
        die(msg + " (got=" + std::to_string(a) + ", want=" + std::to_string(b) + ")");
    }
}
