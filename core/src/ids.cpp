#include "machina/ids.h"
#include "machina/hash.h"
#include <algorithm>
#include <sstream>

namespace machina {

void Menu::buildIndex() {
    sid_index.clear();
    for (size_t i=0;i<items.size();i++) {
        sid_index[items[i].sid.value] = i;
    }
}

const MenuItem* Menu::resolve(const SID& sid) const {
    auto it = sid_index.find(sid.value);
    if (it == sid_index.end()) return nullptr;
    return &items[it->second];
}

static std::string join_tags(const std::vector<std::string>& tags) {
    std::vector<std::string> t = tags;
    std::sort(t.begin(), t.end());
    std::ostringstream oss;
    for (size_t i=0;i<t.size();i++) {
        if (i) oss << ",";
        oss << t[i];
    }
    return oss.str();
}

std::string Menu::digest_raw() const {
    // deterministic digest: "SID0001=AID....|SID0002=AID...."
    std::vector<MenuItem> tmp = items;
    std::sort(tmp.begin(), tmp.end(), [](const MenuItem& a, const MenuItem& b){ return a.sid.value < b.sid.value; });
    std::ostringstream oss;
    for (size_t i=0;i<tmp.size();i++) {
        if (i) oss << "|";
        oss << tmp[i].sid.toString() << "=" << tmp[i].aid << ":" << join_tags(tmp[i].tags);
    }
    return oss.str();
}

std::string Menu::digest() const {
    return hash::sha256_hex(digest_raw());
}

} // namespace machina
