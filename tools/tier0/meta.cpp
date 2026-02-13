#include "machina/tools.h"

namespace machina {

ToolResult tool_noop(const std::string&, DSState&) {
    return {StepStatus::OK, "{}", ""};
}

ToolResult tool_ask_sup(const std::string& input_json, DSState& ds_tmp) {
    // MVP: we do not actually ask a user; we store the question as an artifact.
    Artifact a;
    a.type = "ask_sup";
    a.provenance = "ask_sup";
    a.content_json = input_json;
    a.size_bytes = input_json.size();
    ds_tmp.slots[(uint8_t)DSSlot::DS1] = a;
    return {StepStatus::OK, input_json, ""};
}

} // namespace machina
