#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace machina {

// SHA256
std::vector<uint8_t> sha256_bytes(const uint8_t* data, size_t n);
std::string sha256_hex(const uint8_t* data, size_t n);
inline std::string sha256_hex(const std::string& s) {
    return sha256_hex(reinterpret_cast<const uint8_t*>(s.data()), s.size());
}

// HMAC-SHA256 (hex)
std::string hmac_sha256_hex(const std::string& key, const uint8_t* data, size_t n);
inline std::string hmac_sha256_hex(const std::string& key, const std::string& s) {
    return hmac_sha256_hex(key, reinterpret_cast<const uint8_t*>(s.data()), s.size());
}

// Constant-time string equality (for comparing hex signatures)
bool constant_time_eq(const std::string& a, const std::string& b);

// SHA256 of a file's contents (empty string on error)
std::string sha256_hex_file(const std::filesystem::path& path);

// Cryptographically secure 32-bit random
uint32_t secure_rand32();

} // namespace machina
