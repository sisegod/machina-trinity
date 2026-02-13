#pragma once
#include "types.h"
#include <string>
#include <fstream>

namespace machina {

class JsonlLogger {
public:
    JsonlLogger(const RunHeader& hdr, const std::string& path);
    void event(int step, const std::string& name, const std::string& payload_json);
    const std::string& path() const { return path_; }

private:
    RunHeader hdr_;
    std::string path_;
    std::ofstream out_;
    std::string chain_prev_;
};

} // namespace machina
