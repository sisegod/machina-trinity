#include "vectordb_tools.h"
#include "machina/tools.h"


#include "machina/embedding_provider.h"
#include "machina/hash.h"
#include "machina/json_mini.h"

#include <algorithm>
#include <cassert>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <sstream>
#include <string>
#include <vector>

namespace machina {

static std::mutex g_vecdb_mu;

static int64_t now_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

static size_t getenv_size_t(const char* k, size_t defv) {
    if (const char* e = std::getenv(k)) {
        try { return (size_t)std::stoull(e); } catch (...) {}
    }
    return defv;
}

static std::string sanitize_stream(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (char c : s) {
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-') {
            out.push_back(c);
        } else {
            out.push_back('_');
        }
    }
    if (out.empty()) out = "default";
    return out;
}

static std::filesystem::path vecdb_root() {
    const char* mr = std::getenv("MACHINA_ROOT");
    std::filesystem::path root = mr ? std::filesystem::path(mr) : std::filesystem::current_path();
    return root / "work" / "vectordb";
}

struct Schema {
    size_t dim{0};
};

static std::filesystem::path schema_path(const std::filesystem::path& stream_dir) {
    return stream_dir / "schema.json";
}

static Schema load_or_init_schema(const std::filesystem::path& stream_dir, size_t dim_hint) {
    Schema s;
    auto sp = schema_path(stream_dir);
    std::string raw;
    {
        std::ifstream in(sp);
        if (in.good()) {
            std::stringstream ss;
            ss << in.rdbuf();
            raw = ss.str();
        }
    }
    if (!raw.empty()) {
        if (auto d = json_mini::get_int(raw, "dim")) {
            if (*d > 0) s.dim = (size_t)*d;
        }
    }
    if (s.dim == 0) {
        s.dim = dim_hint;
        if (s.dim == 0) s.dim = getenv_size_t("MACHINA_VECDB_DIM", 384);
        if (s.dim == 0) s.dim = 384;
        std::ostringstream out;
        out << "{";
        out << "\"dim\":" << s.dim;
        out << "}";
        std::ofstream o(sp);
        o << out.str();
    }
    return s;
}

static std::filesystem::path emb_path(const std::filesystem::path& stream_dir) { return stream_dir / "embeddings.f32"; }
static std::filesystem::path meta_path(const std::filesystem::path& stream_dir) { return stream_dir / "meta.jsonl"; }

static bool append_f32_vec(const std::filesystem::path& p, const std::vector<float>& v, std::string* err) {
    std::ofstream out(p, std::ios::binary | std::ios::app);
    if (!out.good()) {
        if (err) *err = "failed to open embeddings";
        return false;
    }
    out.write(reinterpret_cast<const char*>(v.data()), (std::streamsize)(v.size() * sizeof(float)));
    if (!out.good()) {
        if (err) *err = "failed to write embeddings";
        return false;
    }
    return true;
}

static bool append_line(const std::filesystem::path& p, const std::string& line, std::string* err) {
    std::ofstream out(p, std::ios::app);
    if (!out.good()) {
        if (err) *err = "failed to open meta";
        return false;
    }
    out << line << "\n";
    if (!out.good()) {
        if (err) *err = "failed to write meta";
        return false;
    }
    return true;
}

bool vectordb_upsert_text(const std::string& stream, const std::string& text, const std::string& meta_json_raw, std::string* err) {
    std::lock_guard<std::mutex> lk(g_vecdb_mu);
    auto root = vecdb_root();
    std::filesystem::create_directories(root);
    std::string sname = sanitize_stream(stream);
    auto sd = root / sname;
    std::filesystem::create_directories(sd);

    Schema sc = load_or_init_schema(sd, 0);
    auto er = embed_text_best_effort(text, sc.dim);
    // Store normalized vectors to make cosine query a dot-product.
    l2_normalize(er.embedding);

    std::string werr;
    if (!append_f32_vec(emb_path(sd), er.embedding, &werr)) {
        if (err) *err = werr;
        return false;
    }

    // Determine index by file size.
    uint64_t idx = 0;
    {
        std::error_code ec;
        auto sz = std::filesystem::file_size(emb_path(sd), ec);
        if (!ec && sc.dim > 0) {
            idx = (uint64_t)(sz / (sc.dim * sizeof(float))) - 1ULL;
        }
    }

    std::ostringstream ml;
    ml << "{";
    ml << "\"i\":" << idx << ",";
    ml << "\"ts_ms\":" << now_ms() << ",";
    ml << "\"provider\":\"" << json_mini::json_escape(er.provider) << "\"";
    if (!er.error.empty()) {
        ml << ",\"warn\":\"" << json_mini::json_escape(er.error) << "\"";
    }
    if (!meta_json_raw.empty()) {
        // meta_json_raw is expected to be an object raw.
        ml << ",\"meta\":" << meta_json_raw;
    }
    // Keep text (useful for small stores; for large, rely on meta pointers)
    ml << ",\"text\":\"" << json_mini::json_escape(text) << "\"";
    ml << "}";

    if (!append_line(meta_path(sd), ml.str(), &werr)) {
        if (err) *err = werr;
        return false;
    }

    return true;
}

static bool load_schema_and_dim(const std::filesystem::path& sd, size_t* dim_out, std::string* err) {
    auto sp = schema_path(sd);
    std::ifstream in(sp);
    if (!in.good()) {
        if (err) *err = "missing schema";
        return false;
    }
    std::stringstream ss;
    ss << in.rdbuf();
    std::string raw = ss.str();
    auto d = json_mini::get_int(raw, "dim").value_or(0);
    if (d <= 0) {
        if (err) *err = "invalid schema.dim";
        return false;
    }
    *dim_out = (size_t)d;
    return true;
}

ToolResult tool_vecdb_upsert(const std::string& input_json, DSState& /*ds_tmp*/) {
    try {
        std::string stream = json_mini::get_string(input_json, "stream").value_or("default");
        std::string text = json_mini::get_string(input_json, "text").value_or("");
        if (text.empty()) {
            return {StepStatus::TOOL_ERROR, "{}", "missing text"};
        }
        std::string meta_raw = json_mini::get_object_raw(input_json, "meta").value_or("");

        std::string err;
        bool ok = vectordb_upsert_text(stream, text, meta_raw, &err);
        if (!ok) return {StepStatus::TOOL_ERROR, "{}", err};

        std::ostringstream out;
        out << "{\"ok\":true,\"stream\":\"" << json_mini::json_escape(sanitize_stream(stream)) << "\"}";
        return {StepStatus::OK, out.str(), ""};
    } catch (const std::exception& e) {
        return {StepStatus::TOOL_ERROR, "{}", e.what()};
    }
}

ToolResult tool_vecdb_query(const std::string& input_json, DSState& /*ds_tmp*/) {
    try {
        std::string stream = json_mini::get_string(input_json, "stream").value_or("default");
        std::string query = json_mini::get_string(input_json, "query").value_or("");
        int64_t top_k = json_mini::get_int(input_json, "top_k").value_or(8);
        if (top_k <= 0) top_k = 8;
        if (top_k > 50) top_k = 50;
        if (query.empty()) return {StepStatus::TOOL_ERROR, "{}", "missing query"};

        std::lock_guard<std::mutex> lk(g_vecdb_mu);
        auto root = vecdb_root();
        auto sd = root / sanitize_stream(stream);
        if (!std::filesystem::exists(sd)) return {StepStatus::OK, "{\"ok\":true,\"hits\":[]}", ""};

        size_t dim = 0;
        std::string err;
        if (!load_schema_and_dim(sd, &dim, &err)) return {StepStatus::TOOL_ERROR, "{}", err};

        auto er = embed_text_best_effort(query, dim);
        l2_normalize(er.embedding);

        auto ep = emb_path(sd);
        std::ifstream in(ep, std::ios::binary);
        if (!in.good()) return {StepStatus::TOOL_ERROR, "{}", "missing embeddings"};

        // Candidate heap (score, index)
        struct Cand { float s; uint64_t i; };
        std::vector<Cand> best;
        best.reserve((size_t)top_k + 1);

        const size_t chunk_vecs = 1024;
        std::vector<float> buf(chunk_vecs * dim);
        uint64_t idx = 0;
        while (in.good()) {
            in.read(reinterpret_cast<char*>(buf.data()), (std::streamsize)(buf.size() * sizeof(float)));
            std::streamsize got = in.gcount();
            if (got <= 0) break;
            size_t floats = (size_t)(got / (std::streamsize)sizeof(float));
            size_t vecs = floats / dim;
            for (size_t vi = 0; vi < vecs; vi++, idx++) {
                const float* v = &buf[vi * dim];
                double dot = 0.0;
                for (size_t j = 0; j < dim; j++) dot += (double)er.embedding[j] * (double)v[j];
                float sc = (float)dot;

                if (best.size() < (size_t)top_k) {
                    best.push_back({sc, idx});
                    if (best.size() == (size_t)top_k) {
                        std::nth_element(best.begin(), best.begin(), best.end(), [](const Cand& a, const Cand& b){return a.s > b.s;});
                    }
                } else {
                    // Maintain min of best[0] after partial ordering? Simpler: find worst.
                    size_t worst = 0;
                    for (size_t k = 1; k < best.size(); k++) {
                        if (best[k].s < best[worst].s) worst = k;
                    }
                    if (sc > best[worst].s) best[worst] = {sc, idx};
                }
            }
        }

        std::sort(best.begin(), best.end(), [](const Cand& a, const Cand& b){ return a.s > b.s; });

        // Map indices to meta lines (simple: scan meta.jsonl once, keep requested indices)
        std::vector<std::string> meta_lines;
        meta_lines.resize(best.size());
        {
            std::ifstream mi(meta_path(sd));
            if (mi.good()) {
                std::string line;
                while (std::getline(mi, line)) {
                    if (line.empty()) continue;
                    auto ii = json_mini::get_int(line, "i").value_or(-1);
                    if (ii < 0) continue;
                    for (size_t k = 0; k < best.size(); k++) {
                        if ((uint64_t)ii == best[k].i) { meta_lines[k] = line; }
                    }
                }
            }
        }

        std::ostringstream out;
        out << "{";
        out << "\"ok\":true,";
        out << "\"provider\":\"" << json_mini::json_escape(er.provider) << "\",";
        if (!er.error.empty()) out << "\"warn\":\"" << json_mini::json_escape(er.error) << "\",";
        out << "\"hits\":[";
        for (size_t k = 0; k < best.size(); k++) {
            if (k) out << ",";
            out << "{";
            out << "\"i\":" << best[k].i << ",";
            out << "\"score\":" << std::fixed << std::setprecision(6) << best[k].s;
            if (!meta_lines[k].empty()) {
                out << ",\"meta_line\":" << "\"" << json_mini::json_escape(meta_lines[k]) << "\"";
            }
            out << "}";
        }
        out << "]}";

        return {StepStatus::OK, out.str(), ""};
    } catch (const std::exception& e) {
        return {StepStatus::TOOL_ERROR, "{}", e.what()};
    }
}

} // namespace machina
