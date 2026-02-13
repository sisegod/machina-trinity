#include "machina/plugin_api.h"

using namespace machina;

static ToolResult test_echo_tool(const std::string& input_json, DSState& ds_tmp) {
    // Write DS6 with a simple payload that embeds the input_json.
    Artifact a;
    a.type = "text";
    a.provenance = "test_plugin";
    a.content_json = std::string("{\"echo\":") + input_json + "}";
    a.size_bytes = a.content_json.size();
    ds_tmp.slots[(uint8_t)DSSlot::DS6] = a;

    return {StepStatus::OK, "{\"ok\":true}", ""};
}

extern "C" void machina_plugin_init(IToolRegistrar* host) {
    ToolDesc d;
    d.aid = "AID.TEST.ECHO.v1";
    d.name = "Test Echo Tool";
    d.deterministic = true;
    d.tags = {"tag.test"};
    d.side_effects = {"none"};
    d.replay_inputs = {};
    host->register_tool(d, &test_echo_tool);
}
