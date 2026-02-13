#include "machina/gpu_context.h"
#include <cstdlib>

#ifdef MACHINA_USE_CUDA
// Only included when MACHINA_USE_CUDA is defined (CMake option).
#include <cuda_runtime.h>
#endif

namespace machina {

GpuContext GpuContext::create() {
    GpuContext ctx;

#ifdef MACHINA_USE_CUDA
    int count = 0;
    if (cudaGetDeviceCount(&count) != cudaSuccess) {
        ctx.available_ = false;
        ctx.device_count_ = 0;
        ctx.backend_ = "CUDA";
        return ctx;
    }
    ctx.device_count_ = count;
    ctx.backend_ = "CUDA";
    if (count <= 0) {
        ctx.available_ = false;
        return ctx;
    }
    int idx = 0;
    if (const char* e = std::getenv("MACHINA_CUDA_DEVICE")) {
        try { idx = std::stoi(e); } catch (...) { idx = 0; }
    }
    if (idx < 0 || idx >= count) idx = 0;
    if (cudaSetDevice(idx) != cudaSuccess) {
        ctx.available_ = false;
        return ctx;
    }
    ctx.available_ = true;
    ctx.device_index_ = idx;
#else
    ctx.available_ = false;
    ctx.device_index_ = -1;
    ctx.device_count_ = 0;
    ctx.backend_ = "CPU_STUB";
#endif
    return ctx;
}

} // namespace machina
