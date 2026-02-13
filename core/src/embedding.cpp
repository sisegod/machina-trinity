#include "machina/embedding.h"
#include "machina/hash.h"
#include <cmath>
#include <stdexcept>

namespace machina {

std::vector<float> hash_embedding(const std::string& text, size_t dim) {
    if (dim == 0) return {};
    // Expand a 64-bit stable hash stream into dim floats in [-1, 1].
    // We avoid std::random_device and platform RNG to keep determinism tight.
    std::vector<float> v(dim, 0.0f);

    // Seed with SHA-256 then mix into 64-bit chunks.
    std::string h = hash::sha256_hex(text);
    // Use 16 hex chars -> 64-bit at a time, step through h.
    auto hex_to_u64 = [](const std::string& s)->uint64_t{
        uint64_t out = 0;
        for (char c : s) {
            out <<= 4;
            if (c >= '0' && c <= '9') out |= uint64_t(c - '0');
            else if (c >= 'a' && c <= 'f') out |= uint64_t(10 + (c - 'a'));
            else if (c >= 'A' && c <= 'F') out |= uint64_t(10 + (c - 'A'));
        }
        return out;
    };

    size_t chunks = (dim + 1) / 2; // two floats per 64-bit chunk
    for (size_t i=0;i<chunks;i++){
        size_t off = (i*16) % (h.size() - 16);
        uint64_t x = hex_to_u64(h.substr(off,16));
        // Split to two 32-bit lanes, map to float [-1,1]
        uint32_t lo = uint32_t(x & 0xffffffffULL);
        uint32_t hi = uint32_t((x >> 32) & 0xffffffffULL);
        auto to_f = [](uint32_t u)->float{
            // map [0,2^32-1] to [-1,1]
            double d = double(u) / double(0xffffffffULL);
            return float(d * 2.0 - 1.0);
        };
        size_t idx = i*2;
        if (idx < dim) v[idx] = to_f(lo);
        if (idx+1 < dim) v[idx+1] = to_f(hi);
    }

    // L2 normalize (deterministic)
    double sum = 0.0;
    for (float f : v) sum += double(f) * double(f);
    double inv = (sum > 0.0) ? (1.0 / std::sqrt(sum)) : 1.0;
    for (float& f : v) f = float(double(f) * inv);
    return v;
}

float dot(const std::vector<float>& a, const std::vector<float>& b) {
    if (a.size() != b.size()) throw std::runtime_error("dot: dimension mismatch");
    double s = 0.0;
    for (size_t i=0;i<a.size();i++) s += double(a[i]) * double(b[i]);
    return float(s);
}

} // namespace machina
