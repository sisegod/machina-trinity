#include "machina/plugin_api.h"
#include "machina/json_mini.h"
#include "machina/state.h"

#include <sstream>

namespace {

machina::ToolResult hello_tool(const std::string& input_json, machina::DSState& ds_tmp) {
    (void)input_json;

    machina::Artifact a;
    a.type = "text";
    a.provenance = "runtime_plugin";
    a.content_json = "{\"text\":\"hello from hot-loaded plugin\"}";
    a.size_bytes = a.content_json.size();

    ds_tmp.slots[(uint8_t)machina::DSSlot::DS0] = a;

    return {machina::StepStatus::OK, a.content_json, ""};
}

} // namespace

extern "C" void machina_plugin_init(machina::IToolRegistrar* host) {
    machina::ToolDesc d;
    d.aid = "AID.RUNTIME.HELLO.v1";
    d.name = "runtime_hello";
    d.deterministic = true;
    d.tags = {"tag.demo", "tag.runtime", "tag.meta"};
    d.side_effects = {"none"};
    d.replay_inputs = {};

    host->register_tool(d, &hello_tool);
}
