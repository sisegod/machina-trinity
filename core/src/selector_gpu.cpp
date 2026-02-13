#include "machina/selector_gpu.h"
#include "machina/embedding.h"
#include "machina/embedding_provider.h"
#include "machina/hash.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <iomanip>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#ifdef MACHINA_USE_CUDA
// GPU kernels are optional; build only when CUDA is enabled in CMake.
extern "C" void machina_cuda_batched_dot(const float* goal, const float* centroids, int n, int dim, float* out_scores);
#endif

namespace machina {

static std::string sid_to_pick(uint16_t sid) {
    std::ostringstream oss;
    oss << "<PICK><SID" << std::setw(4) << std::setfill('0') << sid << "><END>";
    return oss.str();
}

static size_t parse_dim_env() {
    if (const char* e = std::getenv("MACHINA_GPU_DIM")) {
        try {
            int v = std::stoi(e);
            if (v >= 8 && v <= 2048) return (size_t)v;
        } catch (...) {}
    }
    return 64;
}

#ifdef MACHINA_USE_CUDA
static bool env_truthy(const char* v) {
    if (!v) return false;
    std::string s(v);
    for (auto& c : s) c = (char)std::tolower((unsigned char)c);
    return (s == "1" || s == "true" || s == "yes" || s == "y" || s == "on");
}

static bool env_falsy(const char* v) {
    if (!v) return false;
    std::string s(v);
    for (auto& c : s) c = (char)std::tolower((unsigned char)c);
    return (s == "0" || s == "false" || s == "no" || s == "n" || s == "off");
}
#endif

static float dot_ptr(const std::vector<float>& a, const float* b, size_t dim) {
    double s = 0.0;
    for (size_t i = 0; i < dim; i++) s += (double)a[i] * (double)b[i];
    return (float)s;
}

// Embed a text string using embed_text_best_effort (real model when available,
// hash fallback otherwise).  The result is L2-normalized.
static std::vector<float> smart_embed(const std::string& text, size_t dim) {
    auto r = embed_text_best_effort(text, dim);
    if (r.embedding.size() == dim) {
        l2_normalize(r.embedding);
        return r.embedding;
    }
    // Should not happen — embed_text_best_effort always returns dim-sized vector.
    auto fb = hash_embedding(text, dim);
    return fb;
}

// Cache centroid matrices by (menu_digest, dim) to avoid recomputing per step.
// When using real embeddings the cache prevents repeated subprocess calls.
struct CentroidCacheEntry {
    size_t dim{0};
    size_t n{0};
    std::shared_ptr<std::vector<float>> centroids; // row-major: n x dim
};

static std::mutex g_centroid_mu;
static std::unordered_map<std::string, CentroidCacheEntry> g_centroid_cache;

// Goal embedding cache: avoids re-embedding the same goal_digest string.
static std::mutex g_goal_mu;
static std::unordered_map<std::string, std::vector<float>> g_goal_cache;

static std::string cache_key(const Menu& menu, size_t dim) {
    return menu.digest() + "|dim=" + std::to_string(dim);
}

static CentroidCacheEntry build_centroids(const Menu& menu, size_t dim) {
    CentroidCacheEntry e;
    e.dim = dim;
    e.n = menu.items.size();
    e.centroids = std::make_shared<std::vector<float>>();
    e.centroids->resize(e.n * dim);

    // Collect all seed strings, then embed in a single batch subprocess call.
    std::vector<std::string> seeds;
    seeds.reserve(e.n);
    for (size_t i = 0; i < e.n; i++) {
        std::string seed = menu.items[i].aid;
        std::vector<std::string> t = menu.items[i].tags;
        std::sort(t.begin(), t.end());
        for (auto& x : t) seed += "|" + x;
        seeds.push_back(std::move(seed));
    }

    auto results = embed_texts_batch(seeds, dim);
    for (size_t i = 0; i < e.n; i++) {
        std::vector<float> vec;
        if (i < results.size() && results[i].embedding.size() == dim) {
            vec = std::move(results[i].embedding);
            l2_normalize(vec);
        } else {
            vec = hash_embedding(seeds[i], dim);
        }
        std::copy(vec.begin(), vec.end(), e.centroids->begin() + i * dim);
    }
    return e;
}

#ifdef MACHINA_USE_CUDA
static bool want_gpu_for_this_run() {
    // default: auto (use GPU if available when compiled with CUDA)
    const char* e = std::getenv("MACHINA_USE_GPU");
    if (!e) return true; // auto
    std::string s(e);
    for (auto& c : s) c = (char)std::tolower((unsigned char)c);
    if (s == "auto" || s.empty()) return true;
    if (s == "cpu") return false;
    if (env_truthy(e)) return true;
    if (env_falsy(e)) return false;
    return true;
}
#endif

Selection GpuCentroidSelector::select(const Menu& menu,
                                     const std::string& goal_digest,
                                     const std::string& /*state_digest*/,
                                     ControlMode /*mode*/,
                                     const std::string& /*inputs_json*/) {
    if (menu.items.empty()) {
        last_backend_ = "CPU";
        return parse_selector_output("<NOOP><END>");
    }

    const size_t dim = parse_dim_env();

    // Goal embedding (cached per goal_digest string).
    std::vector<float> goal_vec;
    {
        std::string gk = goal_digest + "|dim=" + std::to_string(dim);
        std::lock_guard<std::mutex> lk(g_goal_mu);
        if (g_goal_cache.size() > 256) g_goal_cache.clear();
        auto git = g_goal_cache.find(gk);
        if (git != g_goal_cache.end()) {
            goal_vec = git->second;
        } else {
            goal_vec = smart_embed(goal_digest, dim);
            g_goal_cache.emplace(gk, goal_vec);
        }
    }

    // Obtain (or build) cached centroids
    const std::string ck = cache_key(menu, dim);
    CentroidCacheEntry entry;
    {
        std::lock_guard<std::mutex> lk(g_centroid_mu);
        // Cap cache size to prevent unbounded growth in long-running daemons.
        // Evict oldest-ish entry instead of clearing all (avoids thundering herd rebuild).
        if (g_centroid_cache.size() > 128) {
            g_centroid_cache.erase(g_centroid_cache.begin());
        }
        auto it = g_centroid_cache.find(ck);
        if (it == g_centroid_cache.end()) {
            g_centroid_cache.emplace(ck, build_centroids(menu, dim));
            it = g_centroid_cache.find(ck);
        }
        entry = it->second; // shared_ptr copy (cheap)
    }
    if (!entry.centroids || entry.centroids->empty()) {
        last_backend_ = "CPU";
        return parse_selector_output("<NOOP><END>");
    }

#ifdef MACHINA_USE_CUDA
    if (want_gpu_for_this_run()) {
        GpuContext ctx = GpuContext::create();
        if (ctx.available()) {
            std::vector<float> scores(entry.n, 0.0f);
            machina_cuda_batched_dot(goal_vec.data(), entry.centroids->data(), (int)entry.n, (int)dim, scores.data());

            float best_score = -1e30f;
            uint16_t best_sid = menu.items[0].sid.value;
            for (size_t i = 0; i < entry.n; i++) {
                float sc = scores[i];
                uint16_t sid = menu.items[i].sid.value;
                if (sc > best_score || (sc == best_score && sid < best_sid)) {
                    best_score = sc;
                    best_sid = sid;
                }
            }
            last_backend_ = "CUDA";
            return parse_selector_output(sid_to_pick(best_sid));
        }
    }
#endif

    // CPU path (always available) — uses cached centroids.
    float best_score = -1e30f;
    uint16_t best_sid = menu.items[0].sid.value;
    for (size_t i = 0; i < entry.n; i++) {
        const float* c = &(*entry.centroids)[i * dim];
        float sc = dot_ptr(goal_vec, c, dim);
        uint16_t sid = menu.items[i].sid.value;
        if (sc > best_score || (sc == best_score && sid < best_sid)) {
            best_score = sc;
            best_sid = sid;
        }
    }
    last_backend_ = "CPU";
    return parse_selector_output(sid_to_pick(best_sid));
}

} // namespace machina
