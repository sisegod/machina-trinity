#pragma once
#include <cstdint>
#include <string>
#include <vector>
#include <optional>

namespace machina {

// Stable Action ID (AID): persistent identifier for audit/learning
using AID = std::string;

// Session ID (SID): compact per-run identifier
struct SID {
    uint16_t value{0}; // 0..65535
    std::string toString() const; // "SID0007"
    static std::optional<SID> parse(const std::string& s); // expects "SIDdddd"
};

// Control modes
enum class ControlMode {
    FALLBACK_ONLY,
    SHADOW_POLICY,
    BLENDED,
    POLICY_ONLY
};

struct RunHeader {
    std::string spec_version{"1.4.2"};
    std::string profile_id{"A"}; // Profile A: MVP-Core
    std::string run_id;          // uuid-like
    std::string request_id;      // caller-supplied tracing ID (optional)
};

// Minimal budget model for Profile A
struct Budget {
    int max_steps{64};
    int max_invalid_picks{8};
};

// A single step outcome
enum class StepStatus {
    OK,
    INVALID_PICK,
    TOOL_ERROR,
    BREAKER_TRIP,
};

} // namespace machina
