#include "machina/state.h"
#include "machina/hash.h"
#include <sstream>
#include <vector>
#include <algorithm>

namespace machina {

static std::string canonical_slot_string(const DSState& st) {
    // deterministic: sort slots by key, include type+provenance+size AND a content fingerprint.
    // RC2+ change: include sha256(content_json) to close the audit/replay gap where content
    // changes were not reflected in the DS digest.
    std::vector<uint8_t> keys;
    keys.reserve(st.slots.size());
    for (auto& kv : st.slots) keys.push_back(kv.first);
    std::sort(keys.begin(), keys.end());
    std::ostringstream oss;
    for (auto k : keys) {
        const auto& a = st.slots.at(k);
        const auto content_sha = hash::sha256_hex(a.content_json);
        oss << int(k) << ":" << a.type << ":" << a.provenance << ":" << a.size_bytes
            << ":" << content_sha << "|";
    }
    return oss.str();
}

std::string DSState::digest_fast() const {
    return hash::hex64(hash::fnv1a64(canonical_slot_string(*this)));
}

std::string DSState::digest() const {
    return hash::sha256_hex(canonical_slot_string(*this));
}

} // namespace machina
