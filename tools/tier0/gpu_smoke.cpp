#include "machina/tools.h"
#include "machina/gpu_context.h"

#include <sstream>
#include <string>
#include <cstdio>

#ifndef _WIN32
  #include <dlfcn.h>
#endif

namespace machina {

// Tool: AID.GPU_SMOKE.v1
// Lightweight GPU presence check via NVML dlopen.
// Falls back to GpuContext (compile-time CUDA) if NVML unavailable.
ToolResult tool_gpu_smoke(const std::string&, DSState& ds_tmp) {
    Artifact a;
    a.type = "gpu_smoke";
    a.provenance = "gpu_smoke";
    a.size_bytes = 0;

#ifndef _WIN32
    // Try NVML dlopen first — works without compile-time CUDA
    void* h = dlopen("libnvidia-ml.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!h) h = dlopen("libnvidia-ml.so", RTLD_NOW | RTLD_LOCAL);

    if (h) {
        using nvmlReturn_t = int;
        using nvmlDevice_t = struct nvmlDevice_st*;
        struct nvmlMemory_t {
            unsigned long long total, free, used;
        };

        auto sym = [&](const char* n) -> void* { return dlsym(h, n); };
        auto nvmlInit = (nvmlReturn_t(*)())sym("nvmlInit_v2");
        auto nvmlShutdown = (nvmlReturn_t(*)())sym("nvmlShutdown");
        auto nvmlGetCount = (nvmlReturn_t(*)(unsigned int*))sym("nvmlDeviceGetCount_v2");
        auto nvmlGetHandle = (nvmlReturn_t(*)(unsigned int, nvmlDevice_t*))sym("nvmlDeviceGetHandleByIndex_v2");
        auto nvmlGetName = (nvmlReturn_t(*)(nvmlDevice_t, char*, unsigned int))sym("nvmlDeviceGetName");
        auto nvmlGetMem = (nvmlReturn_t(*)(nvmlDevice_t, nvmlMemory_t*))sym("nvmlDeviceGetMemoryInfo");
        auto nvmlGetTemp = (nvmlReturn_t(*)(nvmlDevice_t, unsigned int, unsigned int*))sym("nvmlDeviceGetTemperature");
        auto nvmlGetPower = (nvmlReturn_t(*)(nvmlDevice_t, unsigned int*))sym("nvmlDeviceGetPowerUsage");

        bool ok = nvmlInit && nvmlShutdown && nvmlGetCount && nvmlGetHandle && nvmlGetMem;

        if (ok && nvmlInit() == 0) {
            unsigned int count = 0;
            nvmlGetCount(&count);

            unsigned int idx = 0;
            nvmlDevice_t dev = nullptr;
            if (count > 0) nvmlGetHandle(idx, &dev);

            char name[96] = {};
            nvmlMemory_t mem{};
            unsigned int temp = 0, power_mw = 0;

            if (dev) {
                if (nvmlGetName) nvmlGetName(dev, name, sizeof(name));
                nvmlGetMem(dev, &mem);
                if (nvmlGetTemp) nvmlGetTemp(dev, 0u, &temp);
                if (nvmlGetPower) nvmlGetPower(dev, &power_mw);
            }

            nvmlShutdown();
            dlclose(h);

            auto mb = [](unsigned long long b) { return b / (1024ULL * 1024ULL); };

            std::ostringstream payload;
            payload << "{"
                    << "\"backend\":\"NVML_DLOPEN\","
                    << "\"available\":" << (dev ? "true" : "false") << ","
                    << "\"device_count\":" << count << ","
                    << "\"device_index\":" << idx << ","
                    << "\"name\":\"" << name << "\","
                    << "\"memory_total_mb\":" << mb(mem.total) << ","
                    << "\"memory_used_mb\":" << mb(mem.used) << ","
                    << "\"memory_free_mb\":" << mb(mem.free) << ","
                    << "\"temp_c\":" << temp << ","
                    << "\"power_w\":" << (power_mw / 1000.0)
                    << "}";

            a.content_json = payload.str();
            a.size_bytes = a.content_json.size();
            ds_tmp.slots[(uint8_t)DSSlot::DS0] = a;
            return {StepStatus::OK, a.content_json, ""};
        }

        dlclose(h);
    }
#endif

    // Fallback: compile-time CUDA path
    GpuContext ctx = GpuContext::create();

    std::ostringstream payload;
    payload << "{";
    payload << "\"backend\":\"" << ctx.backend() << "\",";
    payload << "\"available\":" << (ctx.available() ? "true" : "false") << ",";
    payload << "\"device_count\":" << ctx.device_count() << ",";
    payload << "\"device_index\":" << ctx.device_index();
    payload << "}";

    a.content_json = payload.str();
    ds_tmp.slots[(uint8_t)DSSlot::DS0] = a;
    return {StepStatus::OK, a.content_json, ""};
}

} // namespace machina
