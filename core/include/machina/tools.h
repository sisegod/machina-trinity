#pragma once
#include "types.h"
#include "state.h"
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <functional>

namespace machina {

struct ToolResult {
    StepStatus status{StepStatus::OK};
    std::string output_json; // for DS_TMP write or ViewSpec
    std::string error;
};

using ToolFn = std::function<ToolResult(const std::string& input_json, DSState& ds_tmp)>;

class ToolRunner {
public:
    void registerTool(const AID& aid, ToolFn fn);
    ToolResult run(const AID& aid, const std::string& input_json, DSState& ds_tmp) const;

    // Enable out-of-proc isolation for side-effect tools.
    // Tools in `isolated_aids` will be routed through toolhost subprocess
    // instead of being called in-proc. `toolhost_bin` is the path to the
    // machina_toolhost binary.
    void enableIsolation(const std::string& toolhost_bin,
                         std::unordered_set<std::string> isolated_aids);

    bool isIsolated(const AID& aid) const { return isolated_.count(aid) > 0; }

private:
    std::unordered_map<std::string, ToolFn> fns_;

    // Isolation routing state
    bool isolation_enabled_{false};
    std::string toolhost_bin_;
    std::unordered_set<std::string> isolated_;

    ToolResult runViaToolhost(const AID& aid, const std::string& input_json, DSState& ds_tmp) const;
};

} // namespace machina
