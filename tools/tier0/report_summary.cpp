#include "machina/tools.h"
#include "machina/json_mini.h"

#include <sstream>
#include <string>


namespace machina {

ToolResult tool_report_summary(const std::string& input_json, DSState& ds_tmp) {
    (void)input_json;
    auto it = ds_tmp.slots.find((uint8_t)DSSlot::DS0);
    if (it == ds_tmp.slots.end()) {
        return {StepStatus::TOOL_ERROR, "{}", "missing DS0 (expected scan result)"};
    }
    const std::string& scan = it->second.content_json;
    int matches = (int)json_mini::get_int(scan, "matches").value_or(0);

    std::ostringstream report;
    report << "Error Scan Report\n";
    report << "matches: " << matches << "\n";

    auto sample_rows_raw = json_mini::get_array_raw(scan, "sample_rows").value_or("[]");
    auto sample_objs = json_mini::parse_array_objects_raw(sample_rows_raw);
    report << "samples:\n";
    for (size_t i = 0; i < sample_objs.size() && i < 5; i++) {
        int row_index = (int)json_mini::get_int(sample_objs[i], "row_index").value_or(0);
        std::string row = json_mini::get_string(sample_objs[i], "row").value_or("");
        report << "- [" << row_index << "] " << row << "\n";
    }

    Artifact a;
    a.type = "report";
    a.provenance = "report_summary";

    std::ostringstream payload;
    payload << "{";
    payload << "\"title\":\"Error Scan Report\",";
    payload << "\"matches\":" << matches << ",";
    payload << "\"text\":\"" << json_mini::json_escape(report.str()) << "\"";
    payload << "}";

    a.content_json = payload.str();
    a.size_bytes = a.content_json.size();
    ds_tmp.slots[(uint8_t)DSSlot::DS2] = a;

    return {StepStatus::OK, a.content_json, ""};
}

} // namespace machina
