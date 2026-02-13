#pragma once
#include "ids.h"
#include <string>
#include <unordered_map>
#include <vector>

namespace machina {

// Tool descriptor loaded from ToolPack
struct ToolDesc {
    AID aid;
    std::string name;
    bool deterministic{true};
    std::vector<std::string> tags;

    // Production-facing metadata (Extreme snapshot 4)
    // - side_effects: normalized list. Use ["none"] if truly pure.
    // - replay_inputs: fences required for deterministic tools with side effects.
    std::vector<std::string> side_effects;
    std::vector<std::string> replay_inputs;
};

// Registry holds all tools and supports tag queries
class Registry {
public:
    void loadToolPackManifest(const std::string& manifest_path);

    // Register a tool descriptor programmatically (e.g., from a hot-loaded plugin).
    // If allow_override is false, duplicate AIDs throw.
    void registerToolDesc(const ToolDesc& d, bool allow_override=false);

    const ToolDesc* getTool(const AID& aid) const;
    std::vector<ToolDesc> queryByTags(const std::vector<std::string>& tags) const;
    std::vector<ToolDesc> allToolDescs() const;

private:
    std::unordered_map<std::string, ToolDesc> tools_;
};

} // namespace machina
