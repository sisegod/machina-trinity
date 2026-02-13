#pragma once

#include "machina/registry.h"
#include "machina/tools.h"
#include "machina/plugin_api.h"
#include "machina/plugin_loader.h"

#include <filesystem>
#include <string>

namespace machina {

// Forward declarations for tier0 tool functions.
ToolResult tool_error_scan_csv(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_file_read(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_file_write(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_shell_exec(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_http_get(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_queue_enqueue(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_noop(const std::string&, DSState&);
ToolResult tool_ask_sup(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_gpu_smoke(const std::string&, DSState& ds_tmp);
ToolResult tool_gpu_metrics(const std::string&, DSState& ds_tmp);
ToolResult tool_proc_self_metrics(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_report_summary(const std::string&, DSState& ds_tmp);
ToolResult tool_memory_append(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_memory_search(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_memory_query(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_embed_text(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_vecdb_upsert(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_vecdb_query(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_run_log_summary(const std::string& input_json, DSState& ds_tmp);

// Genesis tools (from tools/tier0/genesis.h)
ToolResult tool_genesis_write_file(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_genesis_compile_shared(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_genesis_load_plugin(const std::string& input_json, DSState& ds_tmp);

// Concrete IToolRegistrar that bridges PluginManager to Registry + ToolRunner.
struct RunnerRegistrar : public IToolRegistrar {
    Registry& reg;
    ToolRunner& runner;
    bool allow_override{false};

    RunnerRegistrar(Registry& r, ToolRunner& tr, bool allow = false)
        : reg(r), runner(tr), allow_override(allow) {}

    void register_tool(const ToolDesc& desc, ToolFnPtr fn) override {
        reg.registerToolDesc(desc, allow_override);
        runner.registerTool(desc.aid, [fn](const std::string& in, DSState& ds) { return fn(in, ds); });
    }
};

// Register all tier0 tools + genesis tools on the given runner.
void register_all_tier0_tools(ToolRunner& runner);

// Full setup: load manifests, register tools, init genesis context, preload plugins.
// Returns the registrar (caller owns lifetime).
void setup_runtime(Registry& reg,
                   ToolRunner& runner,
                   PluginManager& plugin_mgr,
                   RunnerRegistrar& registrar,
                   const std::filesystem::path& root);

} // namespace machina
