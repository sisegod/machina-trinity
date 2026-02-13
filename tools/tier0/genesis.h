#pragma once

#include <filesystem>

#include "machina/plugin_api.h"
#include "machina/plugin_loader.h"
#include "machina/tools.h"

namespace machina {

// Runner must call this once at startup.
void genesis_set_context(PluginManager* pm,
                         IToolRegistrar* registrar,
                         Registry* reg,
                         ToolRunner* runner,
                         bool allow_override,
                         const std::filesystem::path& root);

ToolResult tool_genesis_write_file(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_genesis_compile_shared(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_genesis_load_plugin(const std::string& input_json, DSState& ds_tmp);

} // namespace machina
