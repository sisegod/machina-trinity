#include "machina/tools.h"
#include "machina/json_mini.h"

#include <fstream>
#include <sstream>
#include <vector>
#include <string>
#include <filesystem>


namespace machina {

static std::string resolve_file_best_effort(const std::string& input_path) {
    namespace fs = std::filesystem;
    fs::path p = fs::path(input_path);
    if (p.is_absolute()) return p.string();

    // Prefer resolution relative to the request file directory (set by machina_cli).
    if (const char* rd = std::getenv("MACHINA_REQUEST_DIR")) {
        fs::path cand = fs::path(rd) / p;
        if (fs::exists(cand)) return cand.string();
    }
    // Fallback to repo root.
    if (const char* root = std::getenv("MACHINA_ROOT")) {
        fs::path cand = fs::path(root) / p;
        if (fs::exists(cand)) return cand.string();
    }
    // Last resort: current working dir.
    return fs::absolute(p).string();
}

// Tool: AID.ERROR_SCAN.v1
ToolResult tool_error_scan_csv(const std::string& input_json, DSState& ds_tmp) {
    auto path_raw = json_mini::get_string(input_json, "input_path").value_or("");
    auto pattern = json_mini::get_string(input_json, "pattern").value_or("");
    int max_rows = (int)json_mini::get_int(input_json, "max_rows").value_or(1000000);

    if (path_raw.empty() || pattern.empty()) {
        return {StepStatus::TOOL_ERROR, "{}", "missing input_path/pattern"};
    }

    std::string path = resolve_file_best_effort(path_raw);

    std::ifstream f(path);
    if (!f) return {StepStatus::TOOL_ERROR, "{}", "cannot open file: " + path_raw + " (resolved=" + path + ")"};

    std::string line;
    int row = 0;
    int matches = 0;
    struct Sample { int row_index; std::string row; };
    std::vector<Sample> samples;

    // naive CSV line scan for MVP (deterministic)
    while (std::getline(f, line)) {
        row++;
        if (row == 1) continue; // header
        if (row > max_rows) break;
        if (line.find(pattern) != std::string::npos) {
            matches++;
            if (samples.size() < 5) samples.push_back({row, line});
        }
    }

    // Write a summary artifact to DS0 (MVP)
    Artifact a;
    a.type = "table_summary";
    a.provenance = "scan:" + pattern;
    a.size_bytes = (size_t)matches;

    // Match toolpack schema: sample_rows is an array of objects.
    std::ostringstream payload;
    payload << "{";
    payload << "\"matches\":" << matches << ",";
    payload << "\"sample_rows\":[";
    for (size_t i = 0; i < samples.size(); i++) {
        if (i) payload << ",";
        payload << "{";
        payload << "\"row_index\":" << samples[i].row_index << ",";
        payload << "\"row\":\"" << json_mini::json_escape(samples[i].row) << "\"";
        payload << "}";
    }
    payload << "]}";

    a.content_json = payload.str();
    ds_tmp.slots[(uint8_t)DSSlot::DS0] = a;

    return {StepStatus::OK, a.content_json, ""};
}

} // namespace machina
