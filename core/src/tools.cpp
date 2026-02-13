#include "machina/tools.h"
#include "machina/proc.h"
#include "machina/json_mini.h"
#include "machina/serialization.h"

#include <json-c/json.h>

#include <cstdlib>
#include <sstream>
#include <stdexcept>

namespace machina {

void ToolRunner::registerTool(const AID& aid, ToolFn fn) {
    fns_[aid] = std::move(fn);
}

ToolResult ToolRunner::run(const AID& aid, const std::string& input_json, DSState& ds_tmp) const {
    auto it = fns_.find(aid);
    if (it == fns_.end()) {
        // Distinguish a missing tool from a tool runtime failure.
        // This enables optional "Genesis auto-trigger" flows in the runner.
        return {StepStatus::TOOL_ERROR, "{}", "MISSING_TOOL: " + aid};
    }

    // Route isolated tools through toolhost subprocess
    if (isolation_enabled_ && isolated_.count(aid) > 0) {
        return runViaToolhost(aid, input_json, ds_tmp);
    }

    return it->second(input_json, ds_tmp);
}

void ToolRunner::enableIsolation(const std::string& toolhost_bin,
                                  std::unordered_set<std::string> isolated_aids) {
    toolhost_bin_ = toolhost_bin;
    isolated_ = std::move(isolated_aids);
    isolation_enabled_ = !toolhost_bin_.empty() && !isolated_.empty();
}

// Execute a tool via toolhost --run <plugin_path> <aid> subprocess.
// For tier0 built-in tools, we use the main binary as "plugin" (toolhost
// links against the same tool implementations). The request is passed via
// stdin as JSON, and the response is captured from stdout.
ToolResult ToolRunner::runViaToolhost(const AID& aid,
                                      const std::string& input_json,
                                      DSState& ds_tmp) const {
    // Build the request JSON for toolhost --run
    json_object* reqj = json_object_new_object();
    json_object_object_add(reqj, "input_json",
        json_object_new_string_len(input_json.c_str(), (int)input_json.size()));

    // Serialize current DSState for the subprocess
    json_object* dsj = dsstate_to_json(ds_tmp);
    json_object_object_add(reqj, "ds_state", dsj);

    std::string req_str = json_object_to_json_string_ext(reqj, JSON_C_TO_STRING_PLAIN);
    json_object_put(reqj);

    // Build argv: machina_cli tool_exec <aid>
    // The tool_exec subcommand registers all tier0 tools and executes one
    // in a subprocess with rlimit sandboxing applied by proc_run_capture_sandboxed_stdin.
    std::vector<std::string> argv = {
        toolhost_bin_,
        "tool_exec",
        aid,
    };

    // Subprocess limits — generous for tool execution
    ProcLimits lim;
    lim.timeout_ms = 30000; // 30s default
    if (const char* v = std::getenv("MACHINA_TOOLHOST_TIMEOUT_MS")) {
        try { lim.timeout_ms = std::stoi(v); } catch (...) {}
    }
    lim.stdout_max_bytes = 1024 * 1024;  // 1MB
    lim.rlimit_as_mb = 1024;             // 1GB virtual memory
    lim.rlimit_cpu_sec = 30;
    lim.rlimit_fsize_mb = 10;
    lim.rlimit_nofile = 64;
    lim.rlimit_nproc = 32;

    // Enable seccomp if configured
    if (const char* v = std::getenv("MACHINA_SECCOMP_ENABLE")) {
        if (std::string(v) == "1") lim.enable_seccomp = true;
    }

    ProcResult pr;
    bool started = proc_run_capture_sandboxed_stdin(argv, ".", req_str, lim, &pr);

    if (!started) {
        return {StepStatus::TOOL_ERROR, "{}",
                "toolhost launch failed: " + pr.error};
    }

    if (pr.timed_out) {
        return {StepStatus::TOOL_ERROR, "{}",
                "toolhost timed out for " + aid};
    }

    if (pr.exit_code != 0) {
        return {StepStatus::TOOL_ERROR, "{}",
                "toolhost exit " + std::to_string(pr.exit_code) + ": " + pr.output};
    }

    // Parse response JSON from toolhost stdout
    json_object* resp = json_tokener_parse(pr.output.c_str());
    if (!resp) {
        return {StepStatus::TOOL_ERROR, "{}",
                "toolhost invalid response JSON"};
    }

    // Extract result fields
    ToolResult result;
    result.status = StepStatus::TOOL_ERROR; // default to error; overwritten if status is present

    json_object* v = nullptr;
    if (json_object_object_get_ex(resp, "status", &v) && json_object_is_type(v, json_type_string)) {
        result.status = stepstatus_from_str(json_object_get_string(v));
    }
    if (json_object_object_get_ex(resp, "output_json", &v) && json_object_is_type(v, json_type_string)) {
        result.output_json = json_object_get_string(v);
    }
    if (json_object_object_get_ex(resp, "error", &v) && json_object_is_type(v, json_type_string)) {
        result.error = json_object_get_string(v);
    }

    // Apply returned DSState back to our ds_tmp
    if (json_object_object_get_ex(resp, "ds_state", &v)) {
        (void)dsstate_from_json(v, &ds_tmp);
    }

    json_object_put(resp);
    return result;
}

} // namespace machina
