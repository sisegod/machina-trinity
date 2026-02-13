#include "machina/tools.h"
#include "machina/json_mini.h"
#include "vectordb_tools.h"
#include "memory_tools_base.h"

#include <cstdlib>
#include <fstream>

#if !defined(_WIN32)
#include <fcntl.h>
#include <unistd.h>
#endif

namespace machina {

ToolResult tool_memory_append(const std::string& input_json, DSState& /*ds_tmp*/) {
    std::lock_guard<std::mutex> lk(memtools::g_mem_mu);

    auto stream = memtools::sanitize_stream(json_mini::get_string(input_json, "stream").value_or("default"));
    auto event_raw = json_mini::get_object_raw(input_json, "event").value_or("");
    auto text = json_mini::get_string(input_json, "text").value_or("");

    if (event_raw.empty() && text.empty()) {
        return {StepStatus::TOOL_ERROR, "{}", "missing event or text"};
    }

    namespace fs = std::filesystem;
    fs::path mr = memtools::memory_root();
    if (const char* root = std::getenv("MACHINA_ROOT")) {
        fs::path r = fs::path(root);
        if (!memtools::is_path_under(mr, r)) {
            return {StepStatus::TOOL_ERROR, "{}", "MACHINA_MEMORY_ROOT must be under MACHINA_ROOT"};
        }
    }

    std::error_code ec;
    fs::create_directories(mr, ec);

    fs::path file = mr / (stream + ".jsonl");
    memtools::maybe_rotate(file);

    std::ofstream out(file, std::ios::app);
    if (!out) {
        return {StepStatus::TOOL_ERROR, "{}", "open failed"};
    }

    int64_t ts = memtools::now_ms();
    out << "{\"ts_ms\":" << ts << ",\"stream\":\"" << memtools::json_escape(stream) << "\",";
    if (!event_raw.empty()) {
        out << "\"event\":" << event_raw;
    } else {
        out << "\"text\":\"" << memtools::json_escape(text) << "\"";
    }
    out << "}\n";
    out.flush();

#if !defined(_WIN32)
    // Optional durability: fsync the file to ensure data reaches disk.
    // Opt-in via MACHINA_WAL_FSYNC=1 to avoid performance penalty by default.
    if (const char* fs_env = std::getenv("MACHINA_WAL_FSYNC")) {
        if (std::string(fs_env) == "1") {
            int sync_fd = ::open(file.c_str(), O_WRONLY | O_APPEND);
            if (sync_fd >= 0) {
                ::fdatasync(sync_fd);
                ::close(sync_fd);
            }
        }
    }
#endif

    // Optional: mirror entries into a persistent semantic vectordb.
    // Opt-in: MACHINA_MEMORY_VECDB_ENABLE=1
    if (const char* ev = std::getenv("MACHINA_MEMORY_VECDB_ENABLE")) {
        std::string sev(ev);
        if (sev == "1" || sev == "true" || sev == "TRUE" || sev == "yes" || sev == "YES") {
            std::string meta = std::string("{")
                + "\"memory_file\":\"" + memtools::json_escape(file.string()) + "\"," 
                + "\"ts_ms\":" + std::to_string(ts) + "," 
                + "\"stream\":\"" + memtools::json_escape(stream) + "\"" 
                + "}";
            std::string up_text = !text.empty() ? text : event_raw;
            std::string verr;
            (void)vectordb_upsert_text(stream, up_text, meta, &verr);
        }
    }

    std::ostringstream j;
    j << "{\"ok\":true,\"stream\":\"" << memtools::json_escape(stream) << "\",\"file\":\"" << memtools::json_escape(file.string()) << "\",\"ts_ms\":" << ts << "}";
    return {StepStatus::OK, j.str(), ""};
}

ToolResult tool_memory_search(const std::string& input_json, DSState& /*ds_tmp*/) {
    std::lock_guard<std::mutex> lk(memtools::g_mem_mu);

    auto stream = memtools::sanitize_stream(json_mini::get_string(input_json, "stream").value_or("default"));
    auto needle = json_mini::get_string(input_json, "contains").value_or("");
    int limit = (int)json_mini::get_int(input_json, "limit").value_or(20);
    if (limit < 1) limit = 1;
    if (limit > 200) limit = 200;

    size_t max_bytes = (size_t)json_mini::get_int(input_json, "max_bytes").value_or((int64_t)(2 * 1024 * 1024));
    if (max_bytes < 4096) max_bytes = 4096;

    namespace fs = std::filesystem;
    fs::path mr = memtools::memory_root();
    fs::path file = mr / (stream + ".jsonl");

    auto lines = memtools::tail_lines_matching(file, needle, (size_t)limit, max_bytes);

    std::ostringstream j;
    j << "{\"ok\":true,\"stream\":\"" << memtools::json_escape(stream) << "\",\"matches\":[";
    for (size_t i = 0; i < lines.size(); i++) {
        if (i) j << ",";
        j << "\"" << memtools::json_escape(lines[i]) << "\"";
    }
    j << "],\"count\":" << lines.size() << "}";
    return {StepStatus::OK, j.str(), ""};
}

} // namespace machina
