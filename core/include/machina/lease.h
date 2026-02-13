#pragma once

// Machina Permission Lease — TTL-based single-use authorization tokens.
//
// Architecture:
//   - Tools are categorized into tiers (0=safe, 1=write, 2=system, 3=dangerous)
//   - By default, only Tier 0 tools execute freely
//   - Higher-tier tools require a lease: a short-lived, single-use token
//   - Leases are issued by the policy/operator layer, not by the LLM
//   - Each lease issuance and consumption is WAL-audited
//
// Flow: LLM requests tier-N tool → runner checks lease → if valid, execute + consume
//       If no lease → reject with "lease_required" status

#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace machina {

struct LeaseToken {
    std::string token_id;     // unique random hex string
    std::string tool_aid;     // which tool (or "*" for any in tier)
    int tier{0};              // required tier level
    int64_t issued_ms{0};     // epoch ms
    int64_t expires_ms{0};    // epoch ms
    bool consumed{false};     // single-use flag
    std::string issuer;       // who/what issued this lease
};

// Classification of tool tiers for permission gating
enum class ToolTier : int {
    SAFE = 0,       // read-only, deterministic, no side effects
    WRITE = 1,      // file writes, memory appends, state mutations
    SYSTEM = 2,     // shell exec, network, process creation
    DANGEROUS = 3,  // genesis, plugin loading, raw syscalls
};

// Determine tool tier from AID and tool descriptor metadata
ToolTier classify_tool_tier(const std::string& aid,
                            const std::vector<std::string>& side_effects);

class LeaseManager {
public:
    // Issue a new lease for a specific tool or tier.
    // ttl_ms: how long the lease is valid (default 60s, max 300s).
    // Returns the token. The caller is responsible for WAL-logging.
    LeaseToken issue_lease(const std::string& tool_aid,
                           int tier,
                           int64_t ttl_ms = 60000,
                           const std::string& issuer = "operator");

    // Verify and consume a lease. Returns true if valid.
    // On success, the lease is marked consumed (single-use).
    // On failure, reason is set.
    bool verify_and_consume(const std::string& token_id,
                            const std::string& tool_aid,
                            std::string* reason = nullptr);

    // Check if a tool requires a lease (tier > 0).
    bool requires_lease(const std::string& aid,
                        const std::vector<std::string>& side_effects) const;

    // Garbage-collect expired leases.
    void gc();

    // Stats
    size_t active_count() const;
    size_t total_issued() const { return total_issued_; }
    size_t total_consumed() const { return total_consumed_; }
    size_t total_rejected() const { return total_rejected_; }

private:
    mutable std::mutex mu_;
    std::unordered_map<std::string, LeaseToken> leases_;
    size_t total_issued_{0};
    size_t total_consumed_{0};
    size_t total_rejected_{0};
};

} // namespace machina
