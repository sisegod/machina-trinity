#pragma once
#include <string>
#include <cstdint>

namespace machina {

// Single-GPU context abstraction.
// - CPU builds: always available()==false (stub)
// - CUDA builds: provides basic device selection + availability probe
class GpuContext {
public:
    static GpuContext create();

    bool available() const { return available_; }
    int device_index() const { return device_index_; }
    int device_count() const { return device_count_; }
    std::string backend() const { return backend_; }

private:
    bool available_{false};
    int device_index_{-1};
    int device_count_{0};
    std::string backend_{"CPU_STUB"};
};

} // namespace machina
