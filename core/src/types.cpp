#include "machina/types.h"
#include <iomanip>
#include <sstream>

namespace machina {

std::string SID::toString() const {
    std::ostringstream oss;
    oss << "SID" << std::setfill('0') << std::setw(4) << value;
    return oss.str();
}

std::optional<SID> SID::parse(const std::string& s) {
    if (s.size() != 7) return std::nullopt;
    if (s.rfind("SID", 0) != 0) return std::nullopt;
    for (size_t i=3;i<7;i++) if (s[i] < '0' || s[i] > '9') return std::nullopt;
    int v = std::stoi(s.substr(3,4));
    if (v < 0 || v > 65535) return std::nullopt;
    SID sid; sid.value = static_cast<uint16_t>(v);
    return sid;
}

} // namespace machina
