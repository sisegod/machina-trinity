#pragma once

#include <string>

namespace machina {

struct DSState;
struct ToolResult;

// Linux-only: returns process RSS/open_fds/threads to help spot leaks.
ToolResult tool_proc_self_metrics(const std::string& input_json, DSState& ds_tmp);

} // namespace machina
