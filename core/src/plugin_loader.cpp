#include "machina/plugin_loader.h"
#include "machina/crypto.h"

#include <algorithm>
#include <fstream>
#include <sstream>

#ifdef _WIN32
  #include <windows.h>
#else
  #include <dlfcn.h>
#endif

namespace machina {

PluginManager::~PluginManager() {
    for (auto& h : handles_) {
        if (!h.handle) continue;
#ifdef _WIN32
        FreeLibrary((HMODULE)h.handle);
#else
        dlclose(h.handle);
#endif
        h.handle = nullptr;
    }
    handles_.clear();
    loaded_.clear();
}

static std::string canonical_str(const std::filesystem::path& p) {
    try {
        return std::filesystem::weakly_canonical(p).string();
    } catch (...) {
        return p.string();
    }
}

bool PluginManager::is_loaded(const std::filesystem::path& path) const {
    auto c = canonical_str(path);
    return loaded_.find(c) != loaded_.end();
}

void PluginManager::set_expected_hash(const std::string& canonical_path, const std::string& sha256_hex) {
    expected_hashes_[canonical_path] = sha256_hex;
}

bool PluginManager::load_plugin(const std::filesystem::path& path,
                                IToolRegistrar* registrar,
                                std::string* err) {
    if (!registrar) {
        if (err) *err = "registrar is null";
        return false;
    }

    auto canonical = canonical_str(path);
    if (loaded_.count(canonical)) {
        return true; // already loaded
    }

    if (!std::filesystem::exists(path)) {
        if (err) *err = "plugin not found: " + path.string();
        return false;
    }

    // Verify SHA256 hash if one was registered for this plugin
    {
        auto it = expected_hashes_.find(canonical);
        if (it != expected_hashes_.end()) {
            std::string actual = sha256_hex_file(path);
            if (actual.empty()) {
                if (err) *err = "failed to compute hash for: " + path.string();
                return false;
            }
            if (!constant_time_eq(actual, it->second)) {
                if (err) *err = "hash mismatch for " + path.string()
                              + ": expected=" + it->second + " actual=" + actual;
                return false;
            }
        }
    }

#ifdef _WIN32
    HMODULE h = LoadLibraryA(path.string().c_str());
    if (!h) {
        if (err) *err = "LoadLibrary failed";
        return false;
    }
    auto init = (machina_plugin_init_fn)GetProcAddress(h, "machina_plugin_init");
    if (!init) {
        if (err) *err = "missing symbol machina_plugin_init";
        FreeLibrary(h);
        return false;
    }
    init(registrar);
    handles_.push_back({canonical, (void*)h});
    loaded_.insert(canonical);
    return true;
#else
    void* h = dlopen(path.string().c_str(), RTLD_NOW | RTLD_LOCAL);
    if (!h) {
        const char* dl_err = dlerror();  // call once — dlerror() clears on read
        if (err) *err = std::string("dlopen failed: ") + (dl_err ? dl_err : "(unknown)");
        return false;
    }

    // Check plugin capabilities if declared
    dlerror(); // clear
    auto cap_fn = (machina_plugin_capabilities_fn)dlsym(h, "machina_plugin_capabilities");
    dlerror(); // clear — cap_fn is optional
    if (cap_fn) {
        uint32_t declared = cap_fn();
        uint32_t excess = declared & ~allowed_caps_;
        if (excess != 0) {
            if (err) *err = "plugin capabilities exceed allowed mask: declared=0x"
                          + ([](uint32_t v) {
                              char buf[16]; snprintf(buf, sizeof(buf), "%08x", v); return std::string(buf);
                          })(declared)
                          + " allowed=0x"
                          + ([](uint32_t v) {
                              char buf[16]; snprintf(buf, sizeof(buf), "%08x", v); return std::string(buf);
                          })(allowed_caps_)
                          + " excess=0x"
                          + ([](uint32_t v) {
                              char buf[16]; snprintf(buf, sizeof(buf), "%08x", v); return std::string(buf);
                          })(excess);
            dlclose(h);
            return false;
        }
    }

    // ABI version check — always enforced (not optional)
    dlerror(); // clear
    auto abi_fn = (machina_plugin_abi_version_fn)dlsym(h, "machina_plugin_abi_version");
    dlerror(); // clear — check result, not dlerror
    if (abi_fn) {
        int plugin_abi = abi_fn();
        if (plugin_abi != MACHINA_ABI_VERSION) {
            if (err) *err = "ABI version mismatch: host=" + std::to_string(MACHINA_ABI_VERSION)
                          + " plugin=" + std::to_string(plugin_abi)
                          + " for " + path.string();
            dlclose(h);
            return false;
        }
    } else {
        // Plugin does not export ABI version — reject unless env override
        const char* lax = std::getenv("MACHINA_PLUGIN_ABI_LAX");
        if (!lax || std::string(lax) != "1") {
            if (err) *err = "plugin missing machina_plugin_abi_version() export: " + path.string()
                          + " (set MACHINA_PLUGIN_ABI_LAX=1 to allow)";
            dlclose(h);
            return false;
        }
    }

    dlerror(); // clear
    auto init = (machina_plugin_init_fn)dlsym(h, "machina_plugin_init");
    const char* sym_err = dlerror();
    if (sym_err != nullptr || !init) {
        if (err) *err = std::string("dlsym(machina_plugin_init) failed: ") + (sym_err ? sym_err : "(null)");
        dlclose(h);
        return false;
    }

    init(registrar);
    handles_.push_back({canonical, h});
    loaded_.insert(canonical);
    return true;
#endif
}

size_t PluginManager::load_new_from_dir(const std::filesystem::path& dir,
                                        IToolRegistrar* registrar,
                                        std::string* err) {
    size_t loaded = 0;
    if (!std::filesystem::exists(dir) || !std::filesystem::is_directory(dir)) return 0;

    std::vector<std::filesystem::path> candidates;
    for (const auto& ent : std::filesystem::directory_iterator(dir)) {
        if (!ent.is_regular_file()) continue;
        auto p = ent.path();
#ifdef _WIN32
        if (p.extension() != ".dll") continue;
#elif defined(__APPLE__)
        if (p.extension() != ".dylib" && p.extension() != ".so") continue;
#else
        if (p.extension() != ".so") continue;
#endif
        candidates.push_back(p);
    }
    std::sort(candidates.begin(), candidates.end());

    for (const auto& p : candidates) {
        if (is_loaded(p)) continue;
        std::string e;
        if (load_plugin(p, registrar, &e)) {
            loaded++;
        } else {
            // Best-effort: keep going, but report first error.
            if (err && err->empty()) *err = e;
        }
    }
    return loaded;
}

} // namespace machina
