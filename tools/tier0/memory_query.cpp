#include "machina/tools.h"
#include "machina/json_mini.h"
#include "machina/embedding.h"
#include "machina/embedding_provider.h"
#include "memory_tools_base.h"

#include <algorithm>
#include <cmath>
#include <unordered_map>
#include <unordered_set>

namespace machina {

// Optional semantic backend (vectordb). Implemented in vectordb_tools.cpp.
ToolResult tool_vecdb_query(const std::string& input_json, DSState& ds_tmp);

struct MemDoc {
    std::string file;
    std::string line;
    std::string text;
    int64_t ts_ms{0};
    float overlap{0.0f};
    std::vector<float> emb;
    float mmr_max_sim{0.0f};
    int doc_len{0};
    std::unordered_map<std::string, int> tfq;
    float score_bm25{0.0f};
    float score_emb{0.0f};
    float score{0.0f};
};

static double getenv_double(const char* k, double defv) {
    if (const char* e = std::getenv(k)) {
        try { return std::stod(e); } catch (...) {}
    }
    return defv;
}

ToolResult tool_memory_query(const std::string& input_json, DSState& /*ds_tmp*/) {
    auto stream = memtools::sanitize_stream(json_mini::get_string(input_json, "stream").value_or("default"));
    auto query = json_mini::get_string(input_json, "query").value_or("");
    auto mode = json_mini::get_string(input_json, "mode").value_or("hybrid");
    auto rerank = json_mini::get_string(input_json, "rerank").value_or("mmr");

    int top_k = (int)json_mini::get_int(input_json, "top_k").value_or(10);
    int candidate_k = (int)json_mini::get_int(input_json, "candidate_k").value_or(0);
    int max_entries = (int)json_mini::get_int(input_json, "max_entries").value_or(2000);
    size_t max_bytes = (size_t)json_mini::get_int(input_json, "max_bytes").value_or((int64_t)(2 * 1024 * 1024));
    size_t max_files = (size_t)json_mini::get_int(input_json, "max_files").value_or(4);
    bool debug = json_mini::get_bool(input_json, "debug").value_or(false);

    double mmr_lambda = 0.72;
    if (auto mv = json_mini::get_double(input_json, "mmr_lambda")) {
        mmr_lambda = *mv;
    }

    if (top_k < 1) top_k = 1;
    if (top_k > 50) top_k = 50;
    if (max_entries < 10) max_entries = 10;
    if (max_entries > 20000) max_entries = 20000;
    if (max_bytes < 4096) max_bytes = 4096;
    if (max_bytes > 64ull * 1024ull * 1024ull) max_bytes = 64ull * 1024ull * 1024ull;
    if (max_files < 1) max_files = 1;
    if (max_files > 16) max_files = 16;

    // candidate_k default: give reranker room
    if (candidate_k <= 0) {
        if (rerank == "mmr") candidate_k = std::min(200, std::max(40, top_k * 10));
        else candidate_k = std::min(200, std::max(20, top_k * 5));
    }
    if (candidate_k < top_k) candidate_k = top_k;
    if (candidate_k > 500) candidate_k = 500;

    // Tokenize query for overlap/BM25-ish scoring
    auto q_tokens = memtools::tokenize_lower(query);
    std::unordered_set<std::string> q_set(q_tokens.begin(), q_tokens.end());

    // Load docs from recent memory files (hold lock only for file I/O)
    std::vector<MemDoc> docs;
    {
        std::lock_guard<std::mutex> lk(memtools::g_mem_mu);
        namespace fs = std::filesystem;
        fs::path mr = memtools::memory_root();
        auto files = memtools::list_memory_files(mr, stream, max_files);

        docs.reserve((size_t)max_entries);

        for (const auto& file : files) {
            auto lines = memtools::tail_lines_matching(file, "\"stream\":\"" + stream + "\"", (size_t)max_entries, max_bytes);
            for (const auto& line : lines) {
                if ((int)docs.size() >= max_entries) break;
                MemDoc d;
                d.file = file.string();
                d.line = line;
                d.text = memtools::extract_text_best_effort(line);
                d.ts_ms = json_mini::get_int(line, "ts_ms").value_or(0);

                // Compute overlap
                auto dtoks = memtools::tokenize_lower(d.text);
                d.doc_len = (int)dtoks.size();
                int hit = 0;
                for (const auto& t : dtoks) {
                    if (!q_set.empty() && q_set.count(t)) hit++;
                    d.tfq[t]++;
                }
                if (!q_tokens.empty()) d.overlap = (float)hit / (float)q_tokens.size();
                docs.push_back(std::move(d));
            }
            if ((int)docs.size() >= max_entries) break;
        }
    } // lock released — remaining computation is on local copies

    if (docs.empty()) {
        std::ostringstream j;
        j << "{\"ok\":true,\"stream\":\"" << memtools::json_escape(stream) << "\",\"matches\":[],\"count\":0}";
        return {StepStatus::OK, j.str(), ""};
    }

    // Optional semantic candidate set via vectordb
    std::unordered_set<std::string> sem_lines;
    if ((mode == "semantic" || mode == "hybrid") && !query.empty()) {
        // Call vecdb tool by crafting a minimal input
        std::ostringstream in;
        in << "{\"stream\":\"" << memtools::json_escape(stream) << "\",\"query\":\"" << memtools::json_escape(query) << "\",\"top_k\":" << candidate_k << "}";
        DSState dummy;
        auto vr = tool_vecdb_query(in.str(), dummy);
        if (vr.status == StepStatus::OK) {
            auto arr = json_mini::get_array_raw(vr.output_json, "matches");
            if (arr) {
                auto objs = json_mini::parse_array_objects_raw(*arr);
                for (const auto& o : objs) {
                    auto raw = json_mini::get_string(o, "raw");
                    if (raw) sem_lines.insert(*raw);
                }
            }
        }
    }

    // Embedding for query (hash for now)
    size_t dim = (size_t)json_mini::get_int(input_json, "embed_dim").value_or(256);
    if (dim < 16) dim = 16;
    if (dim > 1024) dim = 1024;

    auto qemb = hash_embedding(query, dim);
    l2_normalize(qemb);

    // Scoring: combine overlap, semantic embedding, BM25-ish and recency
    double w_overlap = getenv_double("MACHINA_MEMORY_W_OVERLAP", 0.55);
    double w_emb = getenv_double("MACHINA_MEMORY_W_EMB", 0.35);
    double w_bm25 = getenv_double("MACHINA_MEMORY_W_BM25", 0.20);
    double w_recency = getenv_double("MACHINA_MEMORY_W_RECENCY", 0.10);
    double tau_sec = getenv_double("MACHINA_MEMORY_RECENCY_TAU_SEC", 6 * 3600.0);

    // Build IDF from local corpus
    std::unordered_map<std::string, int> df;
    df.reserve(1024);
    for (const auto& d : docs) {
        std::unordered_set<std::string> seen;
        for (const auto& kv : d.tfq) seen.insert(kv.first);
        for (const auto& t : seen) df[t]++;
    }
    auto idf = [&](const std::string& t)->double{
        auto it = df.find(t);
        int dfi = (it==df.end()) ? 0 : it->second;
        double N = (double)docs.size();
        // +1 smoothing
        return std::log((N + 1.0) / (dfi + 1.0)) + 1.0;
    };

    // Precompute avgdl for BM25
    double avgdl = 0.0;
    for (auto& d : docs) avgdl += (double)std::max(1, d.doc_len);
    avgdl = avgdl / std::max(1.0, (double)docs.size());
    double k1 = 1.2;
    double b = 0.75;

    int64_t tnow = memtools::now_ms();

    for (auto& d : docs) {
        // Emb score
        d.emb = hash_embedding(d.text, dim);
        l2_normalize(d.emb);
        d.score_emb = (float)dot(qemb, d.emb);

        // BM25-ish over query tokens
        double bm25 = 0.0;
        for (const auto& qt : q_tokens) {
            int tf = 0;
            auto it = d.tfq.find(qt);
            if (it != d.tfq.end()) tf = it->second;
            if (tf == 0) continue;
            double denom = tf + k1 * (1.0 - b + b * ((double)std::max(1, d.doc_len) / avgdl));
            bm25 += idf(qt) * (tf * (k1 + 1.0)) / denom;
        }
        d.score_bm25 = (float)bm25;

        // Recency in [0,1]
        double rec = 0.0;
        if (d.ts_ms > 0) {
            double age_sec = std::max(0.0, (double)(tnow - d.ts_ms) / 1000.0);
            rec = std::exp(-age_sec / std::max(1.0, tau_sec));
        }

        // Optional semantic filter: if semantic mode and no line match, down-weight
        double sem_gate = 1.0;
        if ((mode == "semantic" || mode == "hybrid") && !sem_lines.empty()) {
            if (sem_lines.count(d.line) == 0) sem_gate = 0.5;
        }

        double base = (w_overlap * d.overlap) + (w_emb * d.score_emb) + (w_bm25 * d.score_bm25) + (w_recency * rec);
        d.score = (float)(base * sem_gate);
    }

    // Candidate set by score
    std::sort(docs.begin(), docs.end(), [](const MemDoc& a, const MemDoc& b){
        return a.score > b.score;
    });
    if ((int)docs.size() > candidate_k) docs.resize((size_t)candidate_k);

    // Rerank
    std::vector<MemDoc> sel;
    sel.reserve((size_t)top_k);

    if (rerank == "mmr") {
        while ((int)sel.size() < top_k && !docs.empty()) {
            int best = -1;
            double best_score = -1e18;

            for (int i = 0; i < (int)docs.size(); i++) {
                double redundancy = 0.0;
                double max_sim = 0.0;
                for (const auto& s : sel) {
                    double sim = dot(docs[i].emb, s.emb);
                    if (sim > max_sim) max_sim = sim;
                }
                redundancy = max_sim;

                double mmr = mmr_lambda * docs[i].score - (1.0 - mmr_lambda) * redundancy;
                if (mmr > best_score) {
                    best_score = mmr;
                    best = i;
                }
            }

            if (best < 0) break;
            // annotate max_sim for debug
            double max_sim = 0.0;
            for (const auto& s : sel) {
                double sim = dot(docs[best].emb, s.emb);
                if (sim > max_sim) max_sim = sim;
            }
            docs[best].mmr_max_sim = (float)max_sim;

            sel.push_back(std::move(docs[best]));
            docs.erase(docs.begin() + best);
        }
    } else {
        if ((int)docs.size() > top_k) docs.resize((size_t)top_k);
        sel = std::move(docs);
    }

    std::ostringstream out;
    out << "{\"ok\":true,\"stream\":\"" << memtools::json_escape(stream) << "\",\"count\":" << sel.size() << ",\"matches\":[";
    for (size_t i = 0; i < sel.size(); i++) {
        if (i) out << ",";
        out << "{";
        out << "\"file\":\"" << memtools::json_escape(sel[i].file) << "\",";
        out << "\"ts_ms\":" << sel[i].ts_ms << ",";
        out << "\"score\":" << sel[i].score << ",";
        out << "\"overlap\":" << sel[i].overlap << ",";
        out << "\"emb\":" << sel[i].score_emb << ",";
        out << "\"bm25\":" << sel[i].score_bm25 << ",";
        if (debug) {
            out << "\"mmr_max_sim\":" << sel[i].mmr_max_sim << ",";
            out << "\"raw\":\"" << memtools::json_escape(sel[i].line) << "\",";
        }
        // always include text for downstream use
        out << "\"text\":\"" << memtools::json_escape(sel[i].text) << "\"";
        out << "}";
    }
    out << "]}";

    return {StepStatus::OK, out.str(), ""};
}

} // namespace machina
