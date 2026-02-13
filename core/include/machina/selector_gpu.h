#pragma once
#include "machina/selector.h"
#include "machina/gpu_context.h"

namespace machina {

// Single-GPU centroid selector (MVP-ready):
// - CPU fallback always available.
// - If built with CUDA and MACHINA_USE_GPU=1, uses GPU for batched dot products (optional).
class GpuCentroidSelector final : public ISelector {
public:
    Selection select(const Menu& menu,
                     const std::string& goal_digest,
                     const std::string& state_digest,
                     ControlMode mode,
                     const std::string& inputs_json) override;

    // Human-readable backend used for the last selection (for logging).
    std::string last_backend() const { return last_backend_; }

private:
    std::string last_backend_{"CPU"};
};

} // namespace machina
