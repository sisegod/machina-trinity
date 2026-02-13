#pragma once
#include <cstdint>
#include <string>
#include <vector>
#include <unordered_map>
#include <optional>

namespace machina {

// DS slots (Profile A): 8 fixed slots
enum class DSSlot : uint8_t { DS0,DS1,DS2,DS3,DS4,DS5,DS6,DS7 };

struct Artifact {
    std::string type;          // e.g. "table", "text", "viewspec"
    std::string content_json;  // JSON string payload (policy input never uses raw)
    std::string provenance;    // hash or simple tag for MVP
    size_t size_bytes{0};
};

struct DSState {
    std::unordered_map<uint8_t, Artifact> slots; // key = DSSlot as uint8

    // Deterministic digest over slots (no raw/content requirement).
    // - digest_fast(): stable non-crypto (FNV-1a 64)
    // - digest(): SHA-256 (crypto) for tamper-evident logging/replay
    std::string digest_fast() const;
    std::string digest() const;
};

} // namespace machina
