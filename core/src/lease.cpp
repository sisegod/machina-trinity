#include "machina/lease.h"
#include "machina/crypto.h"

#include <algorithm>
#include <chrono>
#include <sstream>
#include <iomanip>

namespace machina {

static int64_t now_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

static std::string random_hex(size_t bytes = 16) {
    // Use cryptographically secure random from crypto.h
    std::ostringstream oss;
    oss << std::hex << std::setfill('0');
    for (size_t i = 0; i < bytes; i += 4) {
        uint32_t r = secure_rand32();
        size_t remain = bytes - i;
        size_t use = remain < 4 ? remain : 4;
        for (size_t j = 0; j < use; j++) {
            oss << std::setw(2) << ((r >> (j * 8)) & 0xFF);
        }
    }
    return oss.str();
}

// --- Tool Tier Classification ---

ToolTier classify_tool_tier(const std::string& aid,
                            const std::vector<std::string>& side_effects) {
    // Tier 3: Genesis, plugin loading
    if (aid.find("GENESIS") != std::string::npos) return ToolTier::DANGEROUS;

    // Tier 2: Shell, network, process
    if (aid.find("SHELL") != std::string::npos) return ToolTier::SYSTEM;
    if (aid.find("NET.HTTP") != std::string::npos) return ToolTier::SYSTEM;

    // Check side_effects for classification
    for (const auto& se : side_effects) {
        if (se == "none") continue;
        if (se.find("exec") != std::string::npos ||
            se.find("network") != std::string::npos ||
            se.find("process") != std::string::npos) {
            return ToolTier::SYSTEM;
        }
        if (se.find("write") != std::string::npos ||
            se.find("append") != std::string::npos ||
            se.find("delete") != std::string::npos ||
            se.find("create") != std::string::npos) {
            return ToolTier::WRITE;
        }
    }

    // Tier 1: File writes, memory mutations
    if (aid.find("FILE.WRITE") != std::string::npos) return ToolTier::WRITE;
    if (aid.find("MEMORY.APPEND") != std::string::npos) return ToolTier::WRITE;
    if (aid.find("VECDB.UPSERT") != std::string::npos) return ToolTier::WRITE;

    // Default: SAFE (read-only tools)
    return ToolTier::SAFE;
}

// --- LeaseManager ---

LeaseToken LeaseManager::issue_lease(const std::string& tool_aid,
                                     int tier,
                                     int64_t ttl_ms,
                                     const std::string& issuer) {
    // Clamp TTL: minimum 1s, maximum 300s
    if (ttl_ms < 1000) ttl_ms = 1000;
    if (ttl_ms > 300000) ttl_ms = 300000;

    LeaseToken tok;
    tok.token_id = "lease_" + random_hex(16);
    tok.tool_aid = tool_aid;
    tok.tier = tier;
    tok.issued_ms = now_ms();
    tok.expires_ms = tok.issued_ms + ttl_ms;
    tok.consumed = false;
    tok.issuer = issuer;

    std::lock_guard<std::mutex> lk(mu_);
    leases_[tok.token_id] = tok;
    total_issued_++;
    return tok;
}

bool LeaseManager::verify_and_consume(const std::string& token_id,
                                      const std::string& tool_aid,
                                      std::string* reason) {
    std::lock_guard<std::mutex> lk(mu_);

    auto it = leases_.find(token_id);
    if (it == leases_.end()) {
        if (reason) *reason = "lease not found";
        total_rejected_++;
        return false;
    }

    LeaseToken& tok = it->second;

    // Check expiry
    if (now_ms() > tok.expires_ms) {
        if (reason) *reason = "lease expired";
        leases_.erase(it);
        total_rejected_++;
        return false;
    }

    // Check single-use
    if (tok.consumed) {
        if (reason) *reason = "lease already consumed";
        total_rejected_++;
        return false;
    }

    // Check tool match (wildcard "*" matches anything at the right tier)
    if (tok.tool_aid != "*" && tok.tool_aid != tool_aid) {
        if (reason) *reason = "lease tool mismatch: expected=" + tok.tool_aid + " got=" + tool_aid;
        total_rejected_++;
        return false;
    }

    // All checks passed — consume the lease
    tok.consumed = true;
    total_consumed_++;
    return true;
}

bool LeaseManager::requires_lease(const std::string& aid,
                                  const std::vector<std::string>& side_effects) const {
    return static_cast<int>(classify_tool_tier(aid, side_effects)) > 0;
}

void LeaseManager::gc() {
    std::lock_guard<std::mutex> lk(mu_);
    int64_t now = now_ms();
    for (auto it = leases_.begin(); it != leases_.end(); ) {
        if (it->second.expires_ms < now || it->second.consumed) {
            it = leases_.erase(it);
        } else {
            ++it;
        }
    }
}

size_t LeaseManager::active_count() const {
    std::lock_guard<std::mutex> lk(mu_);
    int64_t now = now_ms();
    size_t count = 0;
    for (const auto& kv : leases_) {
        if (!kv.second.consumed && kv.second.expires_ms > now) {
            count++;
        }
    }
    return count;
}

} // namespace machina
