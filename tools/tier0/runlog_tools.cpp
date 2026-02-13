#include "machina/tools.h"
#include "machina/json_mini.h"

#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>
#include <algorithm>

namespace {

static std::string resolve_file_best_effort(const std::string& input_path) {
    namespace fs = std::filesystem;
    fs::path p = fs::path(input_path);
    if (p.is_absolute()) return p.string();

    if (const char* rd = std::getenv("MACHINA_REQUEST_DIR")) {
        fs::path cand = fs::path(rd) / p;
        if (fs::exists(cand)) return cand.string();
    }
    if (const char* root = std::getenv("MACHINA_ROOT")) {
        fs::path cand = fs::path(root) / p;
        if (fs::exists(cand)) return cand.string();
    }
    return fs::absolute(p).string();
}

static bool is_path_under(const std::filesystem::path& p, const std::filesystem::path& root) {
    std::error_code ec;
    auto rp = std::filesystem::weakly_canonical(p, ec);
    if (ec) return false;
    auto rr = std::filesystem::weakly_canonical(root, ec);
    if (ec) return false;
    auto ps = rp.generic_string();
    auto rs = rr.generic_string();
    if (ps == rs) return true;
    if (!rs.empty() && rs.back() != '/') rs.push_back('/');
    return ps.rfind(rs, 0) == 0;
}

struct ToolAgg {
    int ok{0};
    int err{0};
    long long total_ms{0};
    int max_ms{0};
};

} // namespace

namespace machina {

// AID.RUN.LOG.SUMMARY.v1
ToolResult tool_run_log_summary(const std::string& input_json, DSState& ds_tmp) {
    auto path_raw = json_mini::get_string(input_json, "path").value_or("");
    int max_lines = (int)json_mini::get_int(input_json, "max_lines").value_or(200000);
    long long max_bytes = (long long)json_mini::get_int(input_json, "max_bytes").value_or(8ll * 1024ll * 1024ll);

    if (path_raw.empty()) return {StepStatus::TOOL_ERROR, "{}", "missing path"};
    if (max_lines < 1) max_lines = 1;
    if (max_lines > 500000) max_lines = 500000;
    if (max_bytes < 1024) max_bytes = 1024;
    if (max_bytes > 64ll * 1024ll * 1024ll) max_bytes = 64ll * 1024ll * 1024ll;

    std::string resolved = resolve_file_best_effort(path_raw);
    namespace fs = std::filesystem;
    fs::path rp(resolved);

    // Default allow: under MACHINA_ROOT only.
    fs::path root = fs::path(std::getenv("MACHINA_ROOT") ? std::getenv("MACHINA_ROOT") : ".");
    if (!is_path_under(rp, root)) {
        return {StepStatus::TOOL_ERROR, "{}", "read path not allowed (outside MACHINA_ROOT): " + path_raw};
    }

    std::ifstream f(resolved);
    if (!f) return {StepStatus::TOOL_ERROR, "{}", "cannot open log file: " + path_raw};

    std::unordered_map<std::string, int> event_counts;
    std::unordered_map<std::string, ToolAgg> tool_agg;

    std::string run_id;
    std::string spec_version;
    std::string first_ts, last_ts;

    std::string prev_chain_hash;
    bool chain_links_ok = true;
    int chain_link_errors = 0;

    std::string line;
    int lines = 0;
    long long bytes = 0;

    while (std::getline(f, line)) {
        lines++;
        bytes += (long long)line.size() + 1;
        if (lines > max_lines) break;
        if (bytes > max_bytes) break;
        if (line.empty()) continue;

        auto ev = json_mini::get_string(line, "event").value_or("");
        if (!ev.empty()) event_counts[ev]++;

        auto rid = json_mini::get_string(line, "run_id");
        if (rid && run_id.empty()) run_id = *rid;

        auto sv = json_mini::get_string(line, "spec_version");
        if (sv && spec_version.empty()) spec_version = *sv;

        auto ts = json_mini::get_string(line, "ts");
        if (ts) {
            if (first_ts.empty()) first_ts = *ts;
            last_ts = *ts;
        }

        auto chain_prev = json_mini::get_string(line, "chain_prev").value_or("");
        auto chain_hash = json_mini::get_string(line, "chain_hash").value_or("");
        if (!chain_hash.empty()) {
            if (!prev_chain_hash.empty() && chain_prev != prev_chain_hash) {
                chain_links_ok = false;
                chain_link_errors++;
            }
            prev_chain_hash = chain_hash;
        }

        if (ev == "tool_ok" || ev == "tool_error") {
            auto payload_raw = json_mini::get_object_raw(line, "payload").value_or("{}");
            auto aid = json_mini::get_string(payload_raw, "aid").value_or("");
            int dur = (int)json_mini::get_int(payload_raw, "duration_ms").value_or(0);
            if (!aid.empty()) {
                auto& a = tool_agg[aid];
                if (ev == "tool_ok") a.ok++; else a.err++;
                a.total_ms += dur;
                if (dur > a.max_ms) a.max_ms = dur;
            }
        }
    }

    // Render event counts as array (stable-ish order)
    std::vector<std::pair<std::string,int>> evs;
    evs.reserve(event_counts.size());
    for (const auto& kv : event_counts) evs.push_back(kv);
    std::sort(evs.begin(), evs.end(), [](auto& a, auto& b){
        if (a.second != b.second) return a.second > b.second;
        return a.first < b.first;
    });

    struct ToolRow { std::string aid; ToolAgg agg; };
    std::vector<ToolRow> tools;
    tools.reserve(tool_agg.size());
    for (const auto& kv : tool_agg) tools.push_back({kv.first, kv.second});
    std::sort(tools.begin(), tools.end(), [](const ToolRow& a, const ToolRow& b){
        if (a.agg.total_ms != b.agg.total_ms) return a.agg.total_ms > b.agg.total_ms;
        if (a.agg.err != b.agg.err) return a.agg.err > b.agg.err;
        return a.aid < b.aid;
    });

    std::ostringstream payload;
    payload << "{";
    payload << "\"ok\":true,";
    payload << "\"path\":\"" << json_mini::json_escape(path_raw) << "\",";
    payload << "\"resolved\":\"" << json_mini::json_escape(resolved) << "\",";
    payload << "\"run_id\":\"" << json_mini::json_escape(run_id) << "\",";
    payload << "\"spec_version\":\"" << json_mini::json_escape(spec_version) << "\",";
    payload << "\"first_ts\":\"" << json_mini::json_escape(first_ts) << "\",";
    payload << "\"last_ts\":\"" << json_mini::json_escape(last_ts) << "\",";
    payload << "\"lines\":" << lines << ",";
    payload << "\"bytes\":" << bytes << ",";
    payload << "\"chain_links_ok\":" << (chain_links_ok ? "true" : "false") << ",";
    payload << "\"chain_link_errors\":" << chain_link_errors << ",";
    payload << "\"events\":[";
    for (size_t i=0;i<evs.size();i++){
        if (i) payload << ",";
        payload << "{"
                << "\"event\":\"" << json_mini::json_escape(evs[i].first) << "\","
                << "\"count\":" << evs[i].second
                << "}";
    }
    payload << "],";
    payload << "\"tools\":[";
    for (size_t i=0;i<tools.size();i++){
        if (i) payload << ",";
        payload << "{"
                << "\"aid\":\"" << json_mini::json_escape(tools[i].aid) << "\","
                << "\"ok\":" << tools[i].agg.ok << ","
                << "\"error\":" << tools[i].agg.err << ","
                << "\"total_ms\":" << tools[i].agg.total_ms << ","
                << "\"max_ms\":" << tools[i].agg.max_ms
                << "}";
    }
    payload << "]";
    payload << "}";

    Artifact a;
    a.type = "run_log_summary";
    a.provenance = "debug:run_log_summary";
    a.content_json = payload.str();
    a.size_bytes = a.content_json.size();

    ds_tmp.slots[(uint8_t)DSSlot::DS0] = a;
    return {StepStatus::OK, a.content_json, ""};
}

} // namespace machina
