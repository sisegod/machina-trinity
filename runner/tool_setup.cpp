#include "tool_setup.h"
#include "tools/tier0/genesis.h"

#include <cstdlib>
#include <iostream>
#include <string>
#include <unordered_set>

#ifndef _WIN32
#include <unistd.h>
#endif

namespace machina {

// Side-effect tool AIDs that should be routed through toolhost when
// isolation is enabled. These are Tier 1+ tools with write/exec/network
// side effects that benefit from process-level sandboxing.
static const std::unordered_set<std::string> ISOLATED_TOOL_AIDS = {
    // Tier 1: Write
    "AID.FILE.WRITE.v1",
    "AID.MEMORY.APPEND.v1",
    "AID.VECDB.UPSERT.v1",
    // Tier 2: System (shell, network)
    "AID.SHELL.EXEC.v1",
    "AID.NET.HTTP_GET.v1",
    "AID.QUEUE.ENQUEUE.v1",
    // Tier 3: Dangerous (genesis)
    "AID.GENESIS.WRITE_FILE.v1",
    "AID.GENESIS.COMPILE_SHARED.v1",
    "AID.GENESIS.LOAD_PLUGIN.v1",
};

void register_all_tier0_tools(ToolRunner& runner) {
    runner.registerTool("AID.ERROR_SCAN.v1", tool_error_scan_csv);
    runner.registerTool("AID.FILE.READ.v1", tool_file_read);
    runner.registerTool("AID.FILE.WRITE.v1", tool_file_write);
    runner.registerTool("AID.SHELL.EXEC.v1", tool_shell_exec);
    runner.registerTool("AID.NET.HTTP_GET.v1", tool_http_get);
    runner.registerTool("AID.QUEUE.ENQUEUE.v1", tool_queue_enqueue);
    runner.registerTool("AID.NOOP.v1", tool_noop);
    runner.registerTool("AID.ASK_SUP.v1", tool_ask_sup);
    runner.registerTool("AID.GPU_SMOKE.v1", tool_gpu_smoke);
    runner.registerTool("AID.GPU_METRICS.v1", tool_gpu_metrics);
    runner.registerTool("AID.PROC.SELF_METRICS.v1", tool_proc_self_metrics);
    runner.registerTool("AID.REPORT_SUMMARY.v1", tool_report_summary);
    runner.registerTool("AID.MEMORY.APPEND.v1", tool_memory_append);
    runner.registerTool("AID.MEMORY.SEARCH.v1", tool_memory_search);
    runner.registerTool("AID.MEMORY.QUERY.v1", tool_memory_query);
    runner.registerTool("AID.EMBED.TEXT.v1", tool_embed_text);
    runner.registerTool("AID.VECDB.UPSERT.v1", tool_vecdb_upsert);
    runner.registerTool("AID.VECDB.QUERY.v1", tool_vecdb_query);
    runner.registerTool("AID.RUN.LOG.SUMMARY.v1", tool_run_log_summary);

    // Genesis ToolPack
    runner.registerTool("AID.GENESIS.WRITE_FILE.v1", tool_genesis_write_file);
    runner.registerTool("AID.GENESIS.COMPILE_SHARED.v1", tool_genesis_compile_shared);
    runner.registerTool("AID.GENESIS.LOAD_PLUGIN.v1", tool_genesis_load_plugin);
}

void setup_runtime(Registry& reg,
                   ToolRunner& runner,
                   PluginManager& plugin_mgr,
                   RunnerRegistrar& registrar,
                   const std::filesystem::path& root) {
    // Load tier0 manifest
    reg.loadToolPackManifest((root / "toolpacks" / "tier0" / "manifest.json").string());

    // Register all tier0 tools
    register_all_tier0_tools(runner);

    // Enable toolhost isolation for side-effect tools (opt-in)
    // MACHINA_TOOLHOST_ISOLATE=1 routes Tier1+ tools through subprocess
    // Uses "machina_cli tool_exec <aid>" as the isolation subprocess.
    if (const char* e = std::getenv("MACHINA_TOOLHOST_ISOLATE")) {
        if (std::string(e) == "1") {
            // Use MACHINA_CLI_BIN for tool_exec subcommand.
            // Set by cmd_run/cmd_serve from argv[0], or via /proc/self/exe.
            std::string cli_bin;
            if (const char* cb = std::getenv("MACHINA_CLI_BIN")) {
                cli_bin = cb;
            }
#ifndef _WIN32
            if (cli_bin.empty()) {
                // Fallback: resolve via /proc/self/exe (Linux)
                char buf[4096];
                ssize_t len = readlink("/proc/self/exe", buf, sizeof(buf) - 1);
                if (len > 0) { buf[len] = '\0'; cli_bin = buf; }
            }
#endif
            if (!cli_bin.empty()) {
                runner.enableIsolation(cli_bin, ISOLATED_TOOL_AIDS);
                std::cerr << "[setup] toolhost isolation enabled: "
                          << ISOLATED_TOOL_AIDS.size() << " tools routed out-of-proc\n";
            }
        }
    }

    // Check env for allow_override
    if (const char* e = std::getenv("MACHINA_ALLOW_TOOL_OVERRIDE")) {
        registrar.allow_override = (std::string(e) == "1");
    }

    // Init genesis context
    genesis_set_context(&plugin_mgr, &registrar, &reg, &runner, registrar.allow_override, root);

    // Preload plugins from runtime_plugins directory
    std::string perr;
    plugin_mgr.load_new_from_dir(root / "toolpacks" / "runtime_plugins", &registrar, &perr);
    if (!perr.empty()) {
        std::cerr << "[warn] plugin preload: " << perr << "\n";
    }
}

} // namespace machina
