#include "machina/embedding_provider.h"
#include "machina/embedding.h"
#include "machina/json_mini.h"
#include "machina/proc.h"

#include <cmath>
#include <cstdlib>
#include <sstream>
#include <string>
#include <vector>

namespace machina {

static int getenv_int(const char* k, int defv) {
    if (const char* e = std::getenv(k)) {
        try { return std::stoi(e); } catch (...) {}
    }
    return defv;
}

static std::string getenv_str(const char* k, const std::string& defv="") {
    if (const char* e = std::getenv(k)) return std::string(e);
    return defv;
}

void l2_normalize(std::vector<float>& v) {
    double ss = 0.0;
    for (float x : v) ss += (double)x * (double)x;
    double n = std::sqrt(ss);
    if (n <= 0.0) return;
    float inv = (float)(1.0 / n);
    for (float& x : v) x *= inv;
}

static std::string extract_json_object_best_effort(const std::string& s) {
    // proc_run_capture_sandboxed* merges stdout+stderr.
    // If the embedding provider prints logs, try to salvage the last JSON object.
    // Strategy: scan lines in reverse; pick the last line containing both '{' and '}'.
    // This avoids matching stray '{' in tracebacks or log messages.
    size_t end = s.size();
    while (end > 0) {
        size_t line_end = end;
        size_t line_start = s.rfind('\n', end - 1);
        line_start = (line_start == std::string::npos) ? 0 : line_start + 1;

        auto lb = s.find('{', line_start);
        if (lb < line_end) {
            auto rb = s.rfind('}', line_end - 1);
            if (rb != std::string::npos && rb > lb && rb >= line_start) {
                return s.substr(lb, rb - lb + 1);
            }
        }

        end = (line_start > 0) ? line_start - 1 : 0;
        if (line_start == 0) break;
    }
    // Fallback: original greedy approach
    auto lb = s.rfind('{');
    if (lb == std::string::npos) return "";
    auto rb = s.rfind('}');
    if (rb == std::string::npos || rb <= lb) return "";
    return s.substr(lb, rb - lb + 1);
}

static ProcLimits make_embed_limits() {
    ProcLimits lim;
    lim.timeout_ms = getenv_int("MACHINA_EMBED_TIMEOUT_MS", 5000);
    lim.stdout_max_bytes = (size_t)getenv_int("MACHINA_EMBED_STDOUT_MAX", 2 * 1024 * 1024);

    int cpu_ms = getenv_int("MACHINA_EMBED_CPU_MS", 4000);
    lim.rlimit_cpu_sec = std::max(1, (cpu_ms + 999) / 1000);

    // MACHINA_EMBED_AS_MB: >0 = hard limit, 0 = no AS limit (needed for CUDA).
    int as_mb_env = getenv_int("MACHINA_EMBED_AS_MB", -1);
    if (as_mb_env == 0) {
        lim.rlimit_as_mb = 0;
    } else if (as_mb_env > 0) {
        lim.rlimit_as_mb = (size_t)as_mb_env;
    } else {
        long long mem_bytes = 512LL * 1024 * 1024;
        if (const char* e = std::getenv("MACHINA_EMBED_MEM_BYTES")) {
            try { mem_bytes = std::stoll(e); } catch (...) {}
        }
        lim.rlimit_as_mb = (size_t)std::max(64ll, mem_bytes / (1024ll * 1024ll));
    }

    lim.rlimit_nofile = getenv_int("MACHINA_EMBED_NOFILE", 256);
    // MACHINA_EMBED_NPROC: 0 = no limit (needed for PyTorch/CUDA/OpenBLAS).
    lim.rlimit_nproc = getenv_int("MACHINA_EMBED_NPROC", 0);
    lim.no_new_privs = true;
    return lim;
}

EmbedResult embed_text_best_effort(const std::string& text, size_t dim) {
    EmbedResult r;
    if (dim == 0) {
        r.provider = "hash";
        r.embedding = {};
        return r;
    }
    if (dim > 2048) dim = 2048;

    std::string provider = getenv_str("MACHINA_EMBED_PROVIDER", "hash");
    if (provider == "cmd") {
        std::string cmd = getenv_str("MACHINA_EMBED_CMD", "");
        if (!cmd.empty()) {
            ProcLimits lim = make_embed_limits();

            std::vector<std::string> argv = split_argv_quoted(cmd);
            if (!argv.empty()) {
                std::ostringstream in;
                in << "{";
                in << "\"text\":\"" << json_mini::json_escape(text) << "\",";
                in << "\"dim\":" << dim;
                in << "}";

                ProcResult pr;
                bool ok = proc_run_capture_sandboxed_stdin(argv, "", in.str(), lim, &pr);

                if (ok && pr.exit_code == 0) {
                    std::string js = extract_json_object_best_effort(pr.output);
                    if (js.empty()) js = pr.output;

                    auto emb_raw = json_mini::get_array_raw(js, "embedding");
                    if (emb_raw) {
                        auto vals = json_mini::parse_array_numbers(*emb_raw);
                        if (!vals.empty()) {
                            r.embedding.resize(dim, 0.0f);
                            size_t n = vals.size();
                            if (n > dim) n = dim;
                            for (size_t i = 0; i < n; i++) r.embedding[i] = (float)vals[i];
                            r.provider = json_mini::get_string(js, "provider").value_or("cmd");
                            return r;
                        }
                    }
                    r.error = "embed cmd returned no/invalid embedding";
                } else {
                    r.error = ok ? ("embed cmd exit_code=" + std::to_string(pr.exit_code)) : "embed cmd failed to start";
                    if (!pr.output.empty()) {
                        std::string o = pr.output;
                        if (o.size() > 512) o.resize(512);
                        r.error += "; output=" + o;
                    }
                }
            } else {
                r.error = "MACHINA_EMBED_CMD parsed to empty argv";
            }
        } else {
            r.error = "MACHINA_EMBED_CMD is empty";
        }

        // Fallback to hash on any cmd failure.
        r.provider = "cmd_fallback_hash";
        r.embedding = hash_embedding(text, dim);
        return r;
    }

    // Default: deterministic hash embedding
    r.provider = "hash";
    r.embedding = hash_embedding(text, dim);
    return r;
}

std::vector<EmbedResult> embed_texts_batch(const std::vector<std::string>& texts, size_t dim) {
    std::vector<EmbedResult> results(texts.size());
    if (texts.empty() || dim == 0) return results;
    if (dim > 2048) dim = 2048;

    std::string provider = getenv_str("MACHINA_EMBED_PROVIDER", "hash");
    if (provider != "cmd") {
        // Hash fallback: no subprocess needed.
        for (size_t i = 0; i < texts.size(); i++) {
            results[i].provider = "hash";
            results[i].embedding = hash_embedding(texts[i], dim);
        }
        return results;
    }

    std::string cmd = getenv_str("MACHINA_EMBED_CMD", "");
    if (cmd.empty()) {
        for (size_t i = 0; i < texts.size(); i++) {
            results[i].provider = "hash";
            results[i].embedding = hash_embedding(texts[i], dim);
        }
        return results;
    }

    std::vector<std::string> argv = split_argv_quoted(cmd);
    if (argv.empty()) {
        for (size_t i = 0; i < texts.size(); i++) {
            results[i].provider = "hash";
            results[i].embedding = hash_embedding(texts[i], dim);
        }
        return results;
    }

    ProcLimits lim = make_embed_limits();
    // Batch needs more stdout space: ~10KB per embedding × N.
    lim.stdout_max_bytes = std::max(lim.stdout_max_bytes, texts.size() * 16 * 1024);

    // Build batch JSON: {"texts":["a","b",...], "dim":384}
    std::ostringstream in;
    in << "{\"texts\":[";
    for (size_t i = 0; i < texts.size(); i++) {
        if (i > 0) in << ",";
        in << "\"" << json_mini::json_escape(texts[i]) << "\"";
    }
    in << "],\"dim\":" << dim << "}";

    ProcResult pr;
    bool ok = proc_run_capture_sandboxed_stdin(argv, "", in.str(), lim, &pr);

    if (ok && pr.exit_code == 0) {
        std::string js = extract_json_object_best_effort(pr.output);
        if (js.empty()) js = pr.output;

        // Parse: {"embeddings":[[...],[...],...], "provider":"..."}
        auto embs_raw = json_mini::get_array_raw(js, "embeddings");
        std::string prov = json_mini::get_string(js, "provider").value_or("cmd");

        if (embs_raw) {
            // Parse array of arrays: get_array_raw returns "[[...],[...],...]"
            // (outer brackets included). Track bracket depth; inner arrays
            // start/end at depth 1→2→1.
            const std::string& arr = *embs_raw;
            std::vector<std::string> sub_arrays;
            int depth = 0;
            size_t start = 0;
            for (size_t p = 0; p < arr.size(); p++) {
                char c = arr[p];
                if (c == '[') {
                    if (depth == 1) start = p;  // inner array start
                    depth++;
                } else if (c == ']') {
                    depth--;
                    if (depth == 1) {  // inner array end
                        // Keep brackets — parse_array_numbers expects "[...]".
                        sub_arrays.push_back(arr.substr(start, p - start + 1));
                    }
                }
            }

            for (size_t i = 0; i < std::min(sub_arrays.size(), texts.size()); i++) {
                auto vals = json_mini::parse_array_numbers(sub_arrays[i]);
                if (!vals.empty()) {
                    results[i].embedding.resize(dim, 0.0f);
                    size_t n = std::min(vals.size(), dim);
                    for (size_t j = 0; j < n; j++) results[i].embedding[j] = (float)vals[j];
                    results[i].provider = prov;
                } else {
                    results[i].provider = "cmd_fallback_hash";
                    results[i].embedding = hash_embedding(texts[i], dim);
                }
            }
            // Fill remaining (if subprocess returned fewer than requested).
            for (size_t i = sub_arrays.size(); i < texts.size(); i++) {
                results[i].provider = "cmd_fallback_hash";
                results[i].embedding = hash_embedding(texts[i], dim);
            }
            return results;
        }
    }

    // Batch failed — fall back to hash for all.
    for (size_t i = 0; i < texts.size(); i++) {
        results[i].provider = "cmd_fallback_hash";
        results[i].embedding = hash_embedding(texts[i], dim);
    }
    return results;
}

} // namespace machina
