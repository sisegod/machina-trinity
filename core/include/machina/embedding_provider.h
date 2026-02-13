#pragma once

#include <cstddef>
#include <string>
#include <vector>

namespace machina {

struct EmbedResult {
    std::vector<float> embedding;
    std::string provider; // "hash" or "cmd" (or "cmd_fallback_hash")
    std::string error;    // non-empty on failure
};

// Embedding provider abstraction.
//
// Providers:
// - hash (default): deterministic placeholder embedding (no model deps)
// - cmd: external command that returns JSON {"embedding":[...]} via stdout
//   Config:
//     MACHINA_EMBED_CMD="python3 tools/embed/embed_cmd_stub.py"
//     MACHINA_EMBED_TIMEOUT_MS=5000
//
// Contract for cmd provider:
// - stdin: JSON {"text":"...","dim":384}
// - stdout: JSON {"embedding":[float,...],"provider":"e5-small"} (provider field optional)
EmbedResult embed_text_best_effort(const std::string& text, size_t dim);

// Batch embedding: embeds multiple texts in a single subprocess call.
// Returns one EmbedResult per input text. Falls back to per-text calls on failure.
// Contract for cmd provider batch mode:
// - stdin:  {"texts":["a","b",...],"dim":384}
// - stdout: {"embeddings":[[...],[...],...],"provider":"..."}
std::vector<EmbedResult> embed_texts_batch(const std::vector<std::string>& texts, size_t dim);

// Normalize vector in-place (L2). If zero norm, leaves vector unchanged.
void l2_normalize(std::vector<float>& v);

} // namespace machina
