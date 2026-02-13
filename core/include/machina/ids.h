#pragma once
#include "types.h"
#include <unordered_map>
#include <vector>
#include <string>

namespace machina {

// Menu mapping: SID -> AID (+ small metadata)
struct MenuItem {
    SID sid;
    AID aid;
    std::string name;
    std::vector<std::string> tags;
};

struct Menu {
    std::vector<MenuItem> items;
    std::unordered_map<uint16_t, size_t> sid_index;

    void buildIndex();
    const MenuItem* resolve(const SID& sid) const;
    std::string digest_raw() const; // deterministic canonical menu representation
    std::string digest() const;     // SHA-256 of digest_raw() (compact)
};

} // namespace machina
