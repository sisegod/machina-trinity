#include "cmd_run.h"
#include "cmd_replay.h"
#include "cmd_autopilot.h"
#include "cmd_serve.h"
#include "cmd_chat.h"
#include "runner_utils.h"
#include "tool_setup.h"

#include "machina/cts.h"
#include "machina/json_mini.h"
#include "machina/serialization.h"

#include <json-c/json.h>

#include <iostream>
#include <sstream>
#include <string>

static int cmd_cts(int argc, char** argv) {
    if (argc < 4) {
        std::cerr << "usage: machina_cli cts <toolpack_manifest> <goalpack_manifest>\n";
        return 2;
    }
    auto t_issues = machina::cts_check_toolpack(argv[2]);
    auto g_issues = machina::cts_check_goalpack(argv[3]);
    if (t_issues.empty() && g_issues.empty()) {
        std::cout << "CTS: OK\n";
        return 0;
    }
    for (auto& i : t_issues) std::cout << "CTS toolpack issue " << i.code << ": " << i.message << "\n";
    for (auto& i : g_issues) std::cout << "CTS goalpack issue " << i.code << ": " << i.message << "\n";
    return 1;
}

// Execute a single built-in tool with process isolation.
// Usage: machina_cli tool_exec <AID>
// Reads JSON request from stdin: {"input_json":"...", "ds_state":{...}}
// Writes JSON result to stdout: {"ok":true, "status":"OK", "output_json":"...", "error":"...", "ds_state":{...}}
static int cmd_tool_exec(int argc, char** argv) {
    using namespace machina;
    if (argc < 3) {
        std::cerr << "usage: machina_cli tool_exec <AID>\n";
        return 2;
    }
    const std::string aid = argv[2];
    const auto root = resolve_root(argv[0]);

    // Register all tier0 tools (without isolation — we ARE the isolated process)
    Registry reg;
    ToolRunner runner;
    PluginManager plugin_mgr;
    RunnerRegistrar registrar{reg, runner, false};
    reg.loadToolPackManifest((root / "toolpacks" / "tier0" / "manifest.json").string());
    register_all_tier0_tools(runner);

    // Read request from stdin (capped at 10MB to prevent OOM)
    std::string req;
    req.reserve(4096);
    constexpr size_t MAX_STDIN_BYTES = 10ULL * 1024 * 1024;
    {
        char rbuf[8192];
        while (std::cin.read(rbuf, sizeof(rbuf)) || std::cin.gcount()) {
            req.append(rbuf, (size_t)std::cin.gcount());
            if (req.size() > MAX_STDIN_BYTES) {
                std::cout << "{\"ok\":false,\"error\":\"stdin exceeds 10MB limit\"}";
                return 5;
            }
        }
    }

    json_object* reqj = json_tokener_parse(req.c_str());
    if (!reqj) {
        std::cout << "{\"ok\":false,\"error\":\"invalid JSON on stdin\"}";
        return 5;
    }

    std::string input_json = "{}";
    DSState ds;

    json_object* v = nullptr;
    if (json_object_object_get_ex(reqj, "input_json", &v) && json_object_is_type(v, json_type_string)) {
        input_json = json_object_get_string(v);
    }
    if (json_object_object_get_ex(reqj, "ds_state", &v)) {
        (void)dsstate_from_json(v, &ds);
    }

    auto r = runner.run(aid, input_json, ds);

    json_object* out = json_object_new_object();
    json_object_object_add(out, "ok", json_object_new_boolean(1));
    json_object_object_add(out, "status",
        json_object_new_string(stepstatus_to_str(r.status)));
    json_object_object_add(out, "output_json",
        json_object_new_string_len(r.output_json.c_str(), (int)r.output_json.size()));
    json_object_object_add(out, "error",
        json_object_new_string_len(r.error.c_str(), (int)r.error.size()));

    json_object* dsj = dsstate_to_json(ds);
    json_object_object_add(out, "ds_state", dsj);

    std::cout << json_object_to_json_string_ext(out, JSON_C_TO_STRING_PLAIN);

    json_object_put(out);
    json_object_put(reqj);
    return 0;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr << "machina_cli <run|chat|replay|replay_strict|cts|autopilot|serve|tool_exec> ...\n";
        return 2;
    }
    std::string cmd = argv[1];
    if (cmd == "run") return cmd_run(argc, argv);
    if (cmd == "chat") return cmd_chat(argc, argv);
    if (cmd == "replay") return cmd_replay(argc, argv);
    if (cmd == "replay_strict") return cmd_replay_strict(argc, argv);
    if (cmd == "cts") return cmd_cts(argc, argv);
    if (cmd == "autopilot") return cmd_autopilot(argc, argv);
    if (cmd == "serve") return cmd_serve(argc, argv);
    if (cmd == "tool_exec") return cmd_tool_exec(argc, argv);
    std::cerr << "unknown command: " << cmd << "\n";
    return 2;
}
