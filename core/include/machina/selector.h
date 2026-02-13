#pragma once
#include "types.h"
#include "ids.h"
#include "proc.h"
#include <atomic>
#include <string>
#include <memory>
#include <filesystem>
#include <vector>

namespace machina {

// Selector output contract
struct Selection {
    enum class Kind { PICK, ASK_SUP, NOOP, INVALID } kind{Kind::INVALID};
    std::optional<SID> sid;           // present only if PICK
    std::optional<std::string> input_patch_json; // optional JSON object to merge into runner inputs
    std::string raw;                  // raw text returned by selector
};

// Abstract selector interface
class ISelector {
public:
    virtual ~ISelector() = default;
    virtual Selection select(const Menu& menu,
                             const std::string& goal_digest,
                             const std::string& state_digest,
                             ControlMode mode,
                             const std::string& inputs_json) = 0;
};

// Profile A default selector: deterministic heuristic / stub
class HeuristicSelector final : public ISelector {
public:
    Selection select(const Menu& menu,
                     const std::string& goal_digest,
                     const std::string& state_digest,
                     ControlMode mode,
                     const std::string& inputs_json) override;
};


// External policy selector (process-based).
// If configured (MACHINA_POLICY_CMD), POLICY_ONLY selection calls out to an external
// program that receives a JSON payload file path as argv[1] and returns a selector
// output string (optionally with <INP64> patch).
// FALLBACK_ONLY selection delegates to the wrapped selector.
class ExternalProcessSelector final : public ISelector {
public:
    ExternalProcessSelector(std::unique_ptr<ISelector> fallback,
                            std::filesystem::path repo_root,
                            std::string policy_cmd);
    Selection select(const Menu& menu,
                     const std::string& goal_digest,
                     const std::string& state_digest,
                     ControlMode mode,
                     const std::string& inputs_json) override;
private:
    std::unique_ptr<ISelector> fallback_;
    std::filesystem::path repo_root_;
    std::string policy_cmd_;
    std::vector<std::string> argv_;
    ProcLimits lim_;
    std::vector<std::string> allowed_exec_basenames_;
    std::filesystem::path allowed_script_root_;
    bool allow_unsafe_{false};
    // Ops hardening: circuit breaker for policy crashes/timeouts.
    int policy_fail_threshold_{5};
    int64_t policy_cooldown_ms_{30000};
    std::atomic<int> consecutive_policy_fail_{0};
    std::atomic<int64_t> policy_disabled_until_ms_{0};
};
Selection parse_selector_output(const std::string& s);

} // namespace machina
