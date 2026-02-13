#pragma once

// Plugin ABI (v1) for in-proc tool hot-loading.
//
// Design constraints (Snapshot 6 / RC1):
// - Runner/ToolRunner executes tools in-process (ToolFn signature).
// - Registry provides tool metadata for menu building.
// - We want hot-swap of tools without requiring the plugin to link against
//   machina_core symbols. We achieve this by letting plugins call back into the
//   host via a pure-virtual registrar interface.

#include "machina/registry.h"
#include "machina/tools.h"

// ABI version constant. Plugins compiled for prod mode must export
// machina_plugin_abi_version() returning this value.
#define MACHINA_ABI_VERSION 1

namespace machina {

// ---------------------------------------------------------------------------
// Plugin Capability Flags — declare what resources a plugin needs.
// Host can enforce minimum-privilege: reject plugins requesting more than allowed.
// ---------------------------------------------------------------------------
enum PluginCap : uint32_t {
    CAP_NONE       = 0,
    CAP_FILE_READ  = 1u << 0,   // read files under MACHINA_ROOT
    CAP_FILE_WRITE = 1u << 1,   // write files under MACHINA_ROOT/work/
    CAP_SHELL      = 1u << 2,   // execute shell commands
    CAP_NETWORK    = 1u << 3,   // outbound HTTP/DNS
    CAP_MEMORY     = 1u << 4,   // append/query memory streams
    CAP_GENESIS    = 1u << 5,   // create/compile/load new plugins
    CAP_GPU        = 1u << 6,   // GPU resource access
    CAP_ALL        = 0xFFFFFFFFu,
};

// Function pointer form of ToolFn. The host wraps this into std::function.
using ToolFnPtr = ToolResult (*)(const std::string& input_json, DSState& ds_tmp);

// Host callback interface implemented by the runner.
// Plugins call register_tool(...) from their exported init function.
struct IToolRegistrar {
    virtual ~IToolRegistrar() = default;
    virtual void register_tool(const ToolDesc& desc, ToolFnPtr fn) = 0;
};

} // namespace machina

// Plugin entry point name. A plugin must export a function with C linkage:
//   extern "C" void machina_plugin_init(machina::IToolRegistrar* host);
//
// Optional ABI version export (required in MACHINA_GENESIS_PROD_MODE):
//   extern "C" int machina_plugin_abi_version();
//
// Optional capability declaration (recommended):
//   extern "C" uint32_t machina_plugin_capabilities();
// Returns bitwise OR of machina::PluginCap flags.
// If not exported, host assumes CAP_ALL (backwards-compatible).
extern "C" {
    typedef void (*machina_plugin_init_fn)(machina::IToolRegistrar* host);
    typedef int (*machina_plugin_abi_version_fn)();
    typedef uint32_t (*machina_plugin_capabilities_fn)();
}
