#pragma once

#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <filesystem>

#include "machina/plugin_api.h"

namespace machina {

// Loads shared libraries that export machina_plugin_init(...).
// Keeps handles alive for the process lifetime.
class PluginManager {
public:
    ~PluginManager();

    // Load a single plugin.
    // Returns true on success, false on failure (err is filled).
    bool load_plugin(const std::filesystem::path& path,
                     IToolRegistrar* registrar,
                     std::string* err);

    // Load all new plugins from a directory (non-recursive).
    // Returns number of newly loaded plugins.
    size_t load_new_from_dir(const std::filesystem::path& dir,
                             IToolRegistrar* registrar,
                             std::string* err);

    bool is_loaded(const std::filesystem::path& path) const;
    size_t loaded_count() const { return handles_.size(); }

    // Set expected SHA256 hash for a plugin path (optional).
    // If set, load_plugin() verifies before dlopen.
    void set_expected_hash(const std::string& canonical_path, const std::string& sha256_hex);

    // Set maximum allowed capabilities for dynamically loaded plugins.
    // Plugins declaring capabilities beyond this mask are rejected.
    // Default: CAP_ALL (backwards-compatible, no restriction).
    void set_allowed_capabilities(uint32_t cap_mask) { allowed_caps_ = cap_mask; }
    uint32_t allowed_capabilities() const { return allowed_caps_; }

private:
    struct Handle {
        std::string canonical;
        void* handle{nullptr};
    };

    std::vector<Handle> handles_;
    std::unordered_set<std::string> loaded_;
    std::unordered_map<std::string, std::string> expected_hashes_;
    uint32_t allowed_caps_{0xFFFFFFFFu}; // default: CAP_ALL
};

} // namespace machina
