#pragma once
#include <cstdint>
#include <cstring>
#include <string>
#include <sstream>
#include <iomanip>
#include <array>

namespace machina::hash {

// ---------- FNV-1a 64 (stable, non-crypto) ----------
inline uint64_t fnv1a64_bytes(const uint8_t* data, size_t n) {
    uint64_t h = 1469598103934665603ULL;
    for (size_t i=0;i<n;i++) { h ^= data[i]; h *= 1099511628211ULL; }
    return h;
}
inline uint64_t fnv1a64(const std::string& s) {
    return fnv1a64_bytes(reinterpret_cast<const uint8_t*>(s.data()), s.size());
}
inline std::string hex64(uint64_t v) {
    std::ostringstream oss;
    oss << std::hex << std::setw(16) << std::setfill('0') << v;
    return oss.str();
}

// ---------- SHA-256 (crypto hash) ----------
// Purpose: tamper-evident digests for logs/state/menu.
// NOTE: Implementation lives in core/src/hash.cpp to keep compile time low.
// NOTE: A separate implementation also exists in machina::crypto (crypto.h/crypto.cpp)
// which returns std::vector<uint8_t>. Prefer machina::crypto for new code.

std::array<uint8_t, 32> sha256_bytes(const uint8_t* data, size_t n);
std::string sha256_hex(const std::string& s);

} // namespace machina::hash
