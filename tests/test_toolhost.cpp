#include "machina/proc.h"

#include <json-c/json.h>

#include <cassert>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <string>
#include <vector>
#include <cstdlib>

using namespace machina;

static void require(bool cond, const std::string& msg) {
    if (!cond) {
        std::cerr << "[FAIL] " << msg << "\n";
        std::exit(2);
    }
}

static json_object* parse_json(const std::string& s) {
    json_object* o = json_tokener_parse(s.c_str());
    require(o != nullptr, "invalid JSON: " + s);
    return o;
}

int main(int argc, char** argv) {
    require(argc >= 3, "usage: test_toolhost <toolhost_path> <plugin_path>");
    setenv("MACHINA_PLUGIN_ABI_LAX", "1", 1);

    const std::filesystem::path toolhost = argv[1];
    const std::filesystem::path plugin = argv[2];
    require(std::filesystem::exists(toolhost), "toolhost binary not found: " + toolhost.string());
    require(std::filesystem::exists(plugin), "plugin not found: " + plugin.string());

    ProcLimits lim;
    lim.timeout_ms = 8000;
    lim.stdout_max_bytes = 256 * 1024;
    lim.rlimit_cpu_sec = 4;
    lim.rlimit_as_mb = 2048;
    lim.rlimit_fsize_mb = 8;
    lim.rlimit_nofile = 64;
    lim.rlimit_nproc = 16;
    lim.no_new_privs = true;

    // 1) LIST
    {
        ProcResult pr;
        std::vector<std::string> av = {toolhost.string(), "--list", plugin.string()};
        require(proc_run_capture_sandboxed(av, "", lim, &pr), "proc_run_capture_sandboxed failed");
        require(pr.exit_code == 0, "toolhost --list exit_code=" + std::to_string(pr.exit_code) + ": " + pr.output);

        json_object* root = parse_json(pr.output);
        json_object* okv = nullptr;
        require(json_object_object_get_ex(root, "ok", &okv) && json_object_get_boolean(okv), "list ok=false");

        json_object* tools = nullptr;
        require(json_object_object_get_ex(root, "tools", &tools) && json_object_is_type(tools, json_type_array), "list missing tools array");

        bool found = false;
        const int n = json_object_array_length(tools);
        for (int i = 0; i < n; i++) {
            json_object* t = json_object_array_get_idx(tools, i);
            if (!t || !json_object_is_type(t, json_type_object)) continue;
            json_object* aidv = nullptr;
            if (json_object_object_get_ex(t, "aid", &aidv) && json_object_is_type(aidv, json_type_string)) {
                const char* a = json_object_get_string(aidv);
                if (a && std::string(a) == "AID.TEST.ECHO.v1") {
                    found = true;
                    break;
                }
            }
        }
        json_object_put(root);
        require(found, "AID.TEST.ECHO.v1 not found in list");
    }

    // 2) RUN
    {
        const std::string stdin_req = "{\"input_json\":\"{\\\"x\\\":1}\",\"ds_state\":{\"slots\":{}}}";

        ProcResult pr;
        std::vector<std::string> av = {toolhost.string(), "--run", plugin.string(), "AID.TEST.ECHO.v1"};
        require(proc_run_capture_sandboxed_stdin(av, "", stdin_req, lim, &pr), "proc_run_capture_sandboxed_stdin failed");
        require(pr.exit_code == 0, "toolhost --run exit_code=" + std::to_string(pr.exit_code) + ": " + pr.output);

        json_object* root = parse_json(pr.output);
        json_object* okv = nullptr;
        require(json_object_object_get_ex(root, "ok", &okv) && json_object_get_boolean(okv), "run ok=false");

        json_object* stv = nullptr;
        require(json_object_object_get_ex(root, "status", &stv) && json_object_is_type(stv, json_type_string), "run missing status");
        require(std::string(json_object_get_string(stv)) == "OK", "run status not OK");

        json_object* dsv = nullptr;
        require(json_object_object_get_ex(root, "ds_state", &dsv) && json_object_is_type(dsv, json_type_object), "run missing ds_state");
        json_object* slots = nullptr;
        require(json_object_object_get_ex(dsv, "slots", &slots) && json_object_is_type(slots, json_type_object), "ds_state missing slots");

        json_object* ds6 = nullptr;
        require(json_object_object_get_ex(slots, "6", &ds6), "expected DS6 (key \"6\")");

        json_object* cv = nullptr;
        require(json_object_object_get_ex(ds6, "content_json", &cv) && json_object_is_type(cv, json_type_string), "DS6 missing content_json");
        std::string cj = json_object_get_string(cv);
        require(cj.find("\"echo\"") != std::string::npos, std::string("DS6 content_json missing echo key: ")+cj);

        json_object_put(root);
    }

    std::cout << "[PASS] toolhost list+run\n";
    return 0;
}
