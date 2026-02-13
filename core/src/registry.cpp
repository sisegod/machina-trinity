#include "machina/registry.h"
#include "machina/json_mini.h"

#include <fstream>
#include <sstream>
#include <stdexcept>
#include <algorithm>

namespace machina {

static std::string slurp(const std::string& path) {
    std::ifstream f(path);
    if (!f) throw std::runtime_error("cannot open: " + path);
    std::stringstream ss; ss << f.rdbuf();
    return ss.str();
}

void Registry::loadToolPackManifest(const std::string& manifest_path) {
    std::string j = slurp(manifest_path);

    // Parse minimal fields from the toolpack manifest.
    // We intentionally avoid validating/processing full schemas in RC1.
    if (!json_mini::has_key(j, "toolpack_id") || !json_mini::has_key(j, "tools")) {
        throw std::runtime_error("toolpack manifest missing required keys (toolpack_id/tools)");
    }

    auto tools_raw = json_mini::get_array_raw(j, "tools");
    if (!tools_raw) {
        throw std::runtime_error("toolpack manifest: tools is not a JSON array");
    }

    auto tool_objs = json_mini::parse_array_objects_raw(*tools_raw);
    for (const auto& obj : tool_objs) {
        ToolDesc d;
        d.aid = json_mini::get_string(obj, "aid").value_or("");
        d.name = json_mini::get_string(obj, "name").value_or("");
        d.tags = json_mini::get_array_strings(obj, "tags");
        d.deterministic = json_mini::get_bool(obj, "deterministic").value_or(true);

        // Optional production metadata (Extreme snapshot 4)
        // Default side_effects to ["none"] if absent.
        d.side_effects = json_mini::get_array_strings(obj, "side_effects");
        if (d.side_effects.empty()) d.side_effects.push_back("none");
        d.replay_inputs = json_mini::get_array_strings(obj, "replay_inputs");

        if (d.aid.empty()) {
            continue;
        }
        registerToolDesc(d, /*allow_override=*/false);
    }

    if (tools_.empty()) {
        throw std::runtime_error("toolpack parse produced 0 tools");
    }
}

void Registry::registerToolDesc(const ToolDesc& d, bool allow_override) {
    if (d.aid.empty()) return;
    // AID is a string typedef in Snapshot 6 RC1.
    auto key = d.aid;
    auto it = tools_.find(key);
    if (it != tools_.end() && !allow_override) {
        throw std::runtime_error("duplicate aid in registry: " + key);
    }
    tools_[key] = d;
}

const ToolDesc* Registry::getTool(const AID& aid) const {
    auto it = tools_.find(aid);
    if (it == tools_.end()) return nullptr;
    return &it->second;
}

std::vector<ToolDesc> Registry::queryByTags(const std::vector<std::string>& tags) const {
    std::vector<ToolDesc> res;
    // RC2+ change: treat candidate tags as a UNION (OR), not an INTERSECTION (AND).
    // Rationale: in Profile A we want a compact, resilient menu. AND semantics makes
    // menus accidentally empty as tags accumulate across steps.
    for (const auto& kv : tools_) {
        const auto& d = kv.second;
        if (tags.empty()) {
            res.push_back(d);
            continue;
        }
        bool any = false;
        for (const auto& t : tags) {
            if (std::find(d.tags.begin(), d.tags.end(), t) != d.tags.end()) { any = true; break; }
        }
        if (any) res.push_back(d);
    }
    // deterministic order by AID
    std::sort(res.begin(), res.end(), [](const ToolDesc& a, const ToolDesc& b){ return a.aid < b.aid; });
    return res;
}

std::vector<ToolDesc> Registry::allToolDescs() const {
    std::vector<ToolDesc> res;
    res.reserve(tools_.size());
    for (const auto& kv : tools_)
        res.push_back(kv.second);
    std::sort(res.begin(), res.end(), [](const ToolDesc& a, const ToolDesc& b){ return a.aid < b.aid; });
    return res;
}

} // namespace machina
