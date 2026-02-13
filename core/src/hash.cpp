#include "machina/hash.h"

#include <cstring>

namespace machina::hash {

namespace detail {
static inline uint32_t rotr(uint32_t x, uint32_t n){ return (x>>n) | (x<<(32-n)); }
static inline uint32_t ch(uint32_t x, uint32_t y, uint32_t z){ return (x & y) ^ (~x & z); }
static inline uint32_t maj(uint32_t x, uint32_t y, uint32_t z){ return (x & y) ^ (x & z) ^ (y & z); }
static inline uint32_t bsig0(uint32_t x){ return rotr(x,2) ^ rotr(x,13) ^ rotr(x,22); }
static inline uint32_t bsig1(uint32_t x){ return rotr(x,6) ^ rotr(x,11) ^ rotr(x,25); }
static inline uint32_t ssig0(uint32_t x){ return rotr(x,7) ^ rotr(x,18) ^ (x>>3); }
static inline uint32_t ssig1(uint32_t x){ return rotr(x,17) ^ rotr(x,19) ^ (x>>10); }

static constexpr std::array<uint32_t, 64> K = {
  0x428a2f98u,0x71374491u,0xb5c0fbcfu,0xe9b5dba5u,0x3956c25bu,0x59f111f1u,0x923f82a4u,0xab1c5ed5u,
  0xd807aa98u,0x12835b01u,0x243185beu,0x550c7dc3u,0x72be5d74u,0x80deb1feu,0x9bdc06a7u,0xc19bf174u,
  0xe49b69c1u,0xefbe4786u,0x0fc19dc6u,0x240ca1ccu,0x2de92c6fu,0x4a7484aau,0x5cb0a9dcu,0x76f988dau,
  0x983e5152u,0xa831c66du,0xb00327c8u,0xbf597fc7u,0xc6e00bf3u,0xd5a79147u,0x06ca6351u,0x14292967u,
  0x27b70a85u,0x2e1b2138u,0x4d2c6dfcu,0x53380d13u,0x650a7354u,0x766a0abbu,0x81c2c92eu,0x92722c85u,
  0xa2bfe8a1u,0xa81a664bu,0xc24b8b70u,0xc76c51a3u,0xd192e819u,0xd6990624u,0xf40e3585u,0x106aa070u,
  0x19a4c116u,0x1e376c08u,0x2748774cu,0x34b0bcb5u,0x391c0cb3u,0x4ed8aa4au,0x5b9cca4fu,0x682e6ff3u,
  0x748f82eeu,0x78a5636fu,0x84c87814u,0x8cc70208u,0x90befffau,0xa4506cebu,0xbef9a3f7u,0xc67178f2u
};

static void process_block(const uint8_t block[64], uint32_t H[8]) {
    uint32_t W[64];
    for (int i=0;i<16;i++){
        W[i] = (uint32_t(block[i*4])<<24) | (uint32_t(block[i*4+1])<<16) | (uint32_t(block[i*4+2])<<8) | uint32_t(block[i*4+3]);
    }
    for (int i=16;i<64;i++){
        W[i] = ssig1(W[i-2]) + W[i-7] + ssig0(W[i-15]) + W[i-16];
    }

    uint32_t a=H[0], b=H[1], c=H[2], d=H[3], e=H[4], f=H[5], g=H[6], h=H[7];
    for (int i=0;i<64;i++){
        uint32_t T1 = h + bsig1(e) + ch(e,f,g) + K[i] + W[i];
        uint32_t T2 = bsig0(a) + maj(a,b,c);
        h=g; g=f; f=e; e=d + T1;
        d=c; c=b; b=a; a=T1 + T2;
    }
    H[0]+=a; H[1]+=b; H[2]+=c; H[3]+=d; H[4]+=e; H[5]+=f; H[6]+=g; H[7]+=h;
}
} // namespace detail

std::array<uint8_t, 32> sha256_bytes(const uint8_t* data, size_t n) {
    uint32_t H[8] = {
        0x6a09e667u,0xbb67ae85u,0x3c6ef372u,0xa54ff53au,0x510e527fu,0x9b05688cu,0x1f83d9abu,0x5be0cd19u
    };

    // process full blocks
    size_t full = n / 64;
    for (size_t i=0;i<full;i++){
        detail::process_block(data + i*64, H);
    }

    // final block(s) with padding
    uint8_t block[128];
    size_t rem = n % 64;
    std::memset(block, 0, sizeof(block));
    if (rem) std::memcpy(block, data + full*64, rem);
    block[rem] = 0x80;

    uint64_t bitlen = uint64_t(n) * 8ULL;
    // if not enough room for length, use 2 blocks
    size_t pad_block_len = (rem >= 56) ? 128 : 64;
    // write bit length in last 8 bytes
    size_t off = pad_block_len - 8;
    block[off+0] = uint8_t((bitlen >> 56) & 0xff);
    block[off+1] = uint8_t((bitlen >> 48) & 0xff);
    block[off+2] = uint8_t((bitlen >> 40) & 0xff);
    block[off+3] = uint8_t((bitlen >> 32) & 0xff);
    block[off+4] = uint8_t((bitlen >> 24) & 0xff);
    block[off+5] = uint8_t((bitlen >> 16) & 0xff);
    block[off+6] = uint8_t((bitlen >>  8) & 0xff);
    block[off+7] = uint8_t((bitlen >>  0) & 0xff);

    detail::process_block(block, H);
    if (pad_block_len == 128) detail::process_block(block + 64, H);

    std::array<uint8_t, 32> out{};
    for (int i=0;i<8;i++){
        out[i*4+0] = uint8_t((H[i] >> 24) & 0xff);
        out[i*4+1] = uint8_t((H[i] >> 16) & 0xff);
        out[i*4+2] = uint8_t((H[i] >>  8) & 0xff);
        out[i*4+3] = uint8_t((H[i] >>  0) & 0xff);
    }
    return out;
}

std::string sha256_hex(const std::string& s) {
    auto b = sha256_bytes(reinterpret_cast<const uint8_t*>(s.data()), s.size());
    std::ostringstream oss;
    for (auto v : b) oss << std::hex << std::setw(2) << std::setfill('0') << int(v);
    return oss.str();
}

} // namespace machina::hash
