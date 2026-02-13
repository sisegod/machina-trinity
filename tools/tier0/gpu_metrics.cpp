#include "machina/tools.h"
#include "machina/gpu_context.h"
#include "machina/hash.h"

#include <sstream>
#include <string>
#include <cstdio>

#ifndef _WIN32
  #include <dlfcn.h>
#endif

namespace machina {


static std::string jquote(const std::string& s) {
    std::ostringstream o;
    o << "\"";
    for (unsigned char c : s) {
        switch (c) {
            case '\"': o << "\\\""; break;
            case '\\': o << "\\\\"; break;
            case '\b': o << "\\b"; break;
            case '\f': o << "\\f"; break;
            case '\n': o << "\\n"; break;
            case '\r': o << "\\r"; break;
            case '\t': o << "\\t"; break;
            default:
                if (c < 0x20) {
                    char buf[7];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", (unsigned int)c);
                    o << buf;
                } else {
                    o << (char)c;
                }
        }
    }
    o << "\"";
    return o.str();
}


// Tool: AID.GPU_METRICS.v1
// Purpose: lightweight GPU health probe (NVML if available).
// Notes:
// - No external side effects except querying NVML.
// - If NVML is unavailable, returns available=false with backend="NVML_UNAVAILABLE".
ToolResult tool_gpu_metrics(const std::string&, DSState& ds_tmp) {
    Artifact a;
    a.type = "gpu_metrics";
    a.provenance = "gpu_metrics";
    a.size_bytes = 0;

    GpuContext ctx = GpuContext::create();

#ifdef _WIN32
    std::ostringstream payload;
    payload << "{"
            << "\"backend\":\"WIN_UNSUPPORTED\","
            << "\"available\":false,"
            << "\"device_count\":" << ctx.device_count() << ","
            << "\"device_index\":" << ctx.device_index()
            << "}";
    a.content_json = payload.str();
    ds_tmp.slots[(uint8_t)DSSlot::DS0] = a;
    return {StepStatus::OK, a.content_json, ""};
#else
    // Try dlopen NVML dynamically (so build doesn't require NVML headers/libs).
    void* h = dlopen("libnvidia-ml.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!h) h = dlopen("libnvidia-ml.so", RTLD_NOW | RTLD_LOCAL);

    if (!h) {
        std::ostringstream payload;
        payload << "{"
                << "\"backend\":\"NVML_UNAVAILABLE\","
                << "\"available\":false,"
                << "\"device_count\":" << ctx.device_count() << ","
                << "\"device_index\":" << ctx.device_index() << ","
                << "\"note\":\"dlopen(libnvidia-ml) failed\""
                << "}";
        a.content_json = payload.str();
        ds_tmp.slots[(uint8_t)DSSlot::DS0] = a;
        return {StepStatus::OK, a.content_json, ""};
    }

    // Minimal NVML ABI surface
    using nvmlReturn_t = int;
    using nvmlDevice_t = struct nvmlDevice_st*;

    struct nvmlMemory_t {
        unsigned long long total;
        unsigned long long free;
        unsigned long long used;
    };

    using nvmlInit_v2_t = nvmlReturn_t (*)();
    using nvmlShutdown_t = nvmlReturn_t (*)();
    using nvmlDeviceGetCount_v2_t = nvmlReturn_t (*)(unsigned int*);
    using nvmlDeviceGetHandleByIndex_v2_t = nvmlReturn_t (*)(unsigned int, nvmlDevice_t*);
    using nvmlDeviceGetName_t = nvmlReturn_t (*)(nvmlDevice_t, char*, unsigned int);
    using nvmlDeviceGetMemoryInfo_t = nvmlReturn_t (*)(nvmlDevice_t, nvmlMemory_t*);
    using nvmlDeviceGetTemperature_t = nvmlReturn_t (*)(nvmlDevice_t, unsigned int, unsigned int*);
    using nvmlDeviceGetPowerUsage_t = nvmlReturn_t (*)(nvmlDevice_t, unsigned int*);

    auto sym = [&](const char* n) -> void* { return dlsym(h, n); };

    auto nvmlInit_v2 = (nvmlInit_v2_t)sym("nvmlInit_v2");
    auto nvmlShutdown = (nvmlShutdown_t)sym("nvmlShutdown");
    auto nvmlDeviceGetCount_v2 = (nvmlDeviceGetCount_v2_t)sym("nvmlDeviceGetCount_v2");
    auto nvmlDeviceGetHandleByIndex_v2 = (nvmlDeviceGetHandleByIndex_v2_t)sym("nvmlDeviceGetHandleByIndex_v2");
    auto nvmlDeviceGetName = (nvmlDeviceGetName_t)sym("nvmlDeviceGetName");
    auto nvmlDeviceGetMemoryInfo = (nvmlDeviceGetMemoryInfo_t)sym("nvmlDeviceGetMemoryInfo");
    auto nvmlDeviceGetTemperature = (nvmlDeviceGetTemperature_t)sym("nvmlDeviceGetTemperature");
    auto nvmlDeviceGetPowerUsage = (nvmlDeviceGetPowerUsage_t)sym("nvmlDeviceGetPowerUsage");

    bool ok_api = nvmlInit_v2 && nvmlShutdown && nvmlDeviceGetCount_v2 && nvmlDeviceGetHandleByIndex_v2 &&
                  nvmlDeviceGetMemoryInfo && nvmlDeviceGetTemperature && nvmlDeviceGetPowerUsage;

    if (!ok_api) {
        dlclose(h);
        std::ostringstream payload;
        payload << "{"
                << "\"backend\":\"NVML_PARTIAL\","
                << "\"available\":false,"
                << "\"device_count\":" << ctx.device_count() << ","
                << "\"device_index\":" << ctx.device_index() << ","
                << "\"note\":\"missing NVML symbols\""
                << "}";
        a.content_json = payload.str();
        ds_tmp.slots[(uint8_t)DSSlot::DS0] = a;
        return {StepStatus::OK, a.content_json, ""};
    }

    nvmlReturn_t rc = nvmlInit_v2();
    if (rc != 0) {
        dlclose(h);
        std::ostringstream payload;
        payload << "{"
                << "\"backend\":\"NVML_INIT_FAIL\","
                << "\"available\":false,"
                << "\"device_count\":" << ctx.device_count() << ","
                << "\"device_index\":" << ctx.device_index() << ","
                << "\"rc\":" << rc
                << "}";
        a.content_json = payload.str();
        ds_tmp.slots[(uint8_t)DSSlot::DS0] = a;
        return {StepStatus::OK, a.content_json, ""};
    }

    unsigned int count = 0;
    rc = nvmlDeviceGetCount_v2(&count);

    unsigned int idx = 0;
    if (ctx.device_index() >= 0) idx = (unsigned int)ctx.device_index();

    nvmlDevice_t dev = nullptr;
    if (rc == 0) rc = nvmlDeviceGetHandleByIndex_v2(idx, &dev);

    nvmlMemory_t mem{};
    unsigned int temp = 0;
    unsigned int power_mw = 0;
    char name[96]; name[0] = '\0';

    if (rc == 0 && dev) {
        (void)nvmlDeviceGetMemoryInfo(dev, &mem);
        (void)nvmlDeviceGetTemperature(dev, 0u /*NVML_TEMPERATURE_GPU*/, &temp);
        (void)nvmlDeviceGetPowerUsage(dev, &power_mw);
        if (nvmlDeviceGetName) (void)nvmlDeviceGetName(dev, name, sizeof(name));
    }

    (void)nvmlShutdown();
    dlclose(h);

    auto to_mb = [](unsigned long long b) -> unsigned long long { return b / (1024ULL * 1024ULL); };

    std::ostringstream payload;
    payload << "{"
            << "\"backend\":\"NVML_DLOPEN\","
            << "\"available\":" << ((rc == 0 && dev) ? "true" : "false") << ","
            << "\"device_count\":" << (unsigned int)count << ","
            << "\"device_index\":" << idx << ","
            << "\"name\":" << jquote(std::string(name)) << ","
            << "\"memory_total_mb\":" << to_mb(mem.total) << ","
            << "\"memory_used_mb\":" << to_mb(mem.used) << ","
            << "\"memory_free_mb\":" << to_mb(mem.free) << ","
            << "\"temp_c\":" << temp << ","
            << "\"power_w\":" << (power_mw / 1000.0)
            << "}";

    a.content_json = payload.str();
    a.size_bytes = a.content_json.size();
    ds_tmp.slots[(uint8_t)DSSlot::DS0] = a;
    return {StepStatus::OK, a.content_json, ""};
#endif
}

} // namespace machina
