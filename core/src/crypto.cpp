
#include "machina/crypto.h"

#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>

#if defined(__linux__)
  #include <sys/random.h>
#endif

namespace machina {

namespace {

constexpr std::array<uint32_t, 64> K = {
    0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
    0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
    0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
    0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
    0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
    0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
    0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
    0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u, 0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u
};

inline uint32_t rotr(uint32_t x, uint32_t n) { return (x >> n) | (x << (32 - n)); }
inline uint32_t ch(uint32_t x, uint32_t y, uint32_t z) { return (x & y) ^ (~x & z); }
inline uint32_t maj(uint32_t x, uint32_t y, uint32_t z) { return (x & y) ^ (x & z) ^ (y & z); }
inline uint32_t bsig0(uint32_t x) { return rotr(x, 2) ^ rotr(x, 13) ^ rotr(x, 22); }
inline uint32_t bsig1(uint32_t x) { return rotr(x, 6) ^ rotr(x, 11) ^ rotr(x, 25); }
inline uint32_t ssig0(uint32_t x) { return rotr(x, 7) ^ rotr(x, 18) ^ (x >> 3); }
inline uint32_t ssig1(uint32_t x) { return rotr(x, 17) ^ rotr(x, 19) ^ (x >> 10); }

static void transform(uint32_t state[8], const uint8_t block[64]) {
    uint32_t w[64];
    for (int i = 0; i < 16; i++) {
        w[i] = (uint32_t(block[i*4+0]) << 24) |
               (uint32_t(block[i*4+1]) << 16) |
               (uint32_t(block[i*4+2]) << 8) |
               (uint32_t(block[i*4+3]) << 0);
    }
    for (int i = 16; i < 64; i++) {
        w[i] = ssig1(w[i-2]) + w[i-7] + ssig0(w[i-15]) + w[i-16];
    }

    uint32_t a = state[0], b = state[1], c = state[2], d = state[3];
    uint32_t e = state[4], f = state[5], g = state[6], h = state[7];

    for (int i = 0; i < 64; i++) {
        uint32_t t1 = h + bsig1(e) + ch(e,f,g) + K[i] + w[i];
        uint32_t t2 = bsig0(a) + maj(a,b,c);
        h = g;
        g = f;
        f = e;
        e = d + t1;
        d = c;
        c = b;
        b = a;
        a = t1 + t2;
    }

    state[0] += a; state[1] += b; state[2] += c; state[3] += d;
    state[4] += e; state[5] += f; state[6] += g; state[7] += h;
}

static std::string to_hex(const std::vector<uint8_t>& b) {
    static const char* H = "0123456789abcdef";
    std::string out;
    out.resize(b.size() * 2);
    for (size_t i = 0; i < b.size(); i++) {
        out[i*2+0] = H[(b[i] >> 4) & 0xF];
        out[i*2+1] = H[(b[i] >> 0) & 0xF];
    }
    return out;
}

} // namespace

std::vector<uint8_t> sha256_bytes(const uint8_t* data, size_t n) {
    uint32_t state[8] = {
        0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
        0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u
    };

    uint64_t bit_len = uint64_t(n) * 8ull;

    uint8_t block[64];
    size_t off = 0;

    while (off + 64 <= n) {
        transform(state, data + off);
        off += 64;
    }

    // pad
    size_t rem = n - off;
    std::memset(block, 0, sizeof(block));
    if (rem) std::memcpy(block, data + off, rem);
    block[rem] = 0x80;

    if (rem >= 56) {
        transform(state, block);
        std::memset(block, 0, sizeof(block));
    }

    // length in bits big-endian
    for (int i = 0; i < 8; i++) {
        block[63 - i] = uint8_t((bit_len >> (i * 8)) & 0xFF);
    }
    transform(state, block);

    std::vector<uint8_t> out(32);
    for (int i = 0; i < 8; i++) {
        out[i*4+0] = uint8_t((state[i] >> 24) & 0xFF);
        out[i*4+1] = uint8_t((state[i] >> 16) & 0xFF);
        out[i*4+2] = uint8_t((state[i] >> 8) & 0xFF);
        out[i*4+3] = uint8_t((state[i] >> 0) & 0xFF);
    }
    return out;
}

std::string sha256_hex(const uint8_t* data, size_t n) {
    return to_hex(sha256_bytes(data, n));
}

std::string hmac_sha256_hex(const std::string& key, const uint8_t* data, size_t n) {
    // HMAC(K, m) = H((K0 xor opad) || H((K0 xor ipad) || m))
    std::vector<uint8_t> kbytes(reinterpret_cast<const uint8_t*>(key.data()),
                                reinterpret_cast<const uint8_t*>(key.data()) + key.size());
    if (kbytes.size() > 64) {
        auto kh = sha256_bytes(kbytes.data(), kbytes.size());
        kbytes = kh;
    }
    kbytes.resize(64, 0x00);

    std::vector<uint8_t> o_key_pad(64), i_key_pad(64);
    for (size_t i = 0; i < 64; i++) {
        o_key_pad[i] = uint8_t(kbytes[i] ^ 0x5c);
        i_key_pad[i] = uint8_t(kbytes[i] ^ 0x36);
    }

    // inner = sha256(i_key_pad || data)
    std::vector<uint8_t> inner;
    inner.reserve(64 + n);
    inner.insert(inner.end(), i_key_pad.begin(), i_key_pad.end());
    inner.insert(inner.end(), data, data + n);
    auto inner_hash = sha256_bytes(inner.data(), inner.size());

    // outer = sha256(o_key_pad || inner_hash)
    std::vector<uint8_t> outer;
    outer.reserve(64 + inner_hash.size());
    outer.insert(outer.end(), o_key_pad.begin(), o_key_pad.end());
    outer.insert(outer.end(), inner_hash.begin(), inner_hash.end());
    auto out_hash = sha256_bytes(outer.data(), outer.size());
    return to_hex(out_hash);
}

bool constant_time_eq(const std::string& a, const std::string& b) {
    // Compare in constant time regardless of length mismatch.
    // Use the longer length to avoid leaking which string is shorter.
    const size_t len = (a.size() >= b.size()) ? a.size() : b.size();
    volatile uint8_t v = (a.size() != b.size()) ? 1 : 0;
    for (size_t i = 0; i < len; i++) {
        uint8_t ca = (i < a.size()) ? (uint8_t)a[i] : 0;
        uint8_t cb = (i < b.size()) ? (uint8_t)b[i] : 0;
        v |= ca ^ cb;
    }
    return v == 0;
}

std::string sha256_hex_file(const std::filesystem::path& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return "";
    std::vector<uint8_t> data((std::istreambuf_iterator<char>(f)),
                               std::istreambuf_iterator<char>());
    return sha256_hex(data.data(), data.size());
}

uint32_t secure_rand32() {
    uint32_t v = 0;
#if defined(__linux__)
    if (::getrandom(&v, sizeof(v), 0) == (ssize_t)sizeof(v)) return v;
#endif
    FILE* f = std::fopen("/dev/urandom", "rb");
    if (f) {
        size_t got = std::fread(&v, sizeof(v), 1, f);
        std::fclose(f);
        if (got == 1) return v;
    }
    // Both getrandom and /dev/urandom failed — abort rather than return predictable 0
    std::fprintf(stderr, "FATAL: secure_rand32() cannot obtain random bytes\n");
    std::abort();
}

} // namespace machina
