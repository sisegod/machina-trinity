#pragma once
#include <string>
#include <vector>

namespace machina {

// Deterministic "placeholder" embedding for MVP-Core / Single-GPU profile.
// - No model dependency.
// - Stable across runs and platforms.
// - Produces a fixed-dim vector suitable for centroid scoring.
std::vector<float> hash_embedding(const std::string& text, size_t dim);

float dot(const std::vector<float>& a, const std::vector<float>& b);

} // namespace machina
