#include "machina/tools.h"
#include "machina/json_mini.h"

#include <chrono>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <random>
#include <sstream>
#include <string>

namespace {

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

static int64_t now_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

static std::string pad4(int v) {
    if (v < 0) v = 0;
    if (v > 9999) v = 9999;
    std::ostringstream oss;
    oss.width(4);
    oss.fill('0');
    oss << v;
    return oss.str();
}

static std::string rand_hex8() {
    std::random_device rd;
    std::mt19937 rng(rd());
    std::uniform_int_distribution<uint32_t> dist(0, 0xffffffffu);
    uint32_t v = dist(rng);
    std::ostringstream oss;
    oss << std::hex;
    oss.width(8);
    oss.fill('0');
    oss << v;
    return oss.str();
}

static std::string patch_queue_meta(const std::string& req_json,
                                   int attempt,
                                   std::optional<int> max_attempts,
                                   std::optional<int64_t> next_run_at_ms) {
    // Append a minimal _queue block if one is not present.
    // If _queue exists, leave as-is (tool shouldn't rewrite user intent).
    if (req_json.find("\"_queue\"") != std::string::npos) return req_json;
    auto pos = req_json.rfind('}');
    if (pos == std::string::npos) return req_json;
    std::ostringstream q;
    q << "\"_queue\":{";
    q << "\"attempt\":" << attempt;
    if (max_attempts) q << ",\"max_attempts\":" << *max_attempts;
    if (next_run_at_ms) q << ",\"next_run_at_ms\":" << *next_run_at_ms;
    q << "}";
    bool has_other = req_json.find(':') != std::string::npos;
    std::string out = req_json;
    out.insert(pos, (has_other ? "," : "") + q.str());
    return out;
}

static std::filesystem::path default_queue_root() {
    std::filesystem::path root = std::filesystem::current_path();
    if (const char* e = std::getenv("MACHINA_ROOT")) root = std::filesystem::path(e);
    return root / "work" / "queue";
}

} // namespace

namespace machina {

// Tool: AID.QUEUE.ENQUEUE.v1
// Writes a run_request JSON file into <queue_dir>/inbox.
ToolResult tool_queue_enqueue(const std::string& input_json, DSState& ds_tmp) {
    std::filesystem::path root = std::filesystem::current_path();
    if (const char* e = std::getenv("MACHINA_ROOT")) root = std::filesystem::path(e);

    std::filesystem::path qroot = default_queue_root();
    if (auto q = json_mini::get_string(input_json, "queue_dir")) {
        std::filesystem::path qp(*q);
        if (!qp.is_absolute()) qp = root / qp;
        if (!is_path_under(qp, root)) {
            return {StepStatus::TOOL_ERROR, "{}", "queue_dir not allowed (must be under MACHINA_ROOT)"};
        }
        qroot = qp;
    }

    std::error_code ec;
    std::filesystem::path inbox = qroot / "inbox";
    std::filesystem::path retry = qroot / "retry";
    std::filesystem::create_directories(inbox, ec);
    std::filesystem::create_directories(retry, ec);

    // Accept either request_json (object) or request_str (string containing JSON).
    std::string req = json_mini::get_object_raw(input_json, "request_json").value_or("");
    if (req.empty()) {
        req = json_mini::get_string(input_json, "request_str").value_or("");
    }
    if (req.empty()) return {StepStatus::TOOL_ERROR, "{}", "missing request_json (object) or request_str (string)"};

    // Basic sanity: must contain goal_id key.
    if (!json_mini::has_key(req, "goal_id")) {
        return {StepStatus::TOOL_ERROR, "{}", "request missing goal_id"};
    }

    // Optional scheduling
    int64_t delay_ms = json_mini::get_int(input_json, "delay_ms").value_or(0);
    if (delay_ms < 0) delay_ms = 0;
    auto max_attempts = json_mini::get_int(input_json, "max_attempts");

    // Priority: lower number runs earlier (0..9999). Default 5000.
    int priority = (int)json_mini::get_int(input_json, "priority").value_or(-1);
    if (priority < 0) {
        // If request already has _queue.priority, honor it.
        auto qmeta = json_mini::get_object_raw(req, "_queue").value_or("");
        if (!qmeta.empty()) {
            priority = (int)json_mini::get_int(qmeta, "priority").value_or(5000);
        } else {
            priority = 5000;
        }
    }
    if (priority < 0) priority = 0;
    if (priority > 9999) priority = 9999;

    // Inject minimal queue meta if not present
    std::optional<int64_t> next_run_at_ms;
    if (delay_ms > 0) next_run_at_ms = now_ms() + delay_ms;
    req = patch_queue_meta(req, /*attempt=*/1, max_attempts ? std::optional<int>((int)*max_attempts) : std::nullopt, next_run_at_ms);

    std::string fname = json_mini::get_string(input_json, "filename").value_or("");
    if (fname.empty()) {
        fname = std::string("p") + pad4(priority) + "_rq_" + std::to_string(now_ms()) + "_" + rand_hex8() + ".json";
    }
    // Normalize filename: prevent path traversal.
    std::filesystem::path fp(fname);
    fname = fp.filename().string();
    if (fname.find("..") != std::string::npos) {
        return {StepStatus::TOOL_ERROR, "{}", "invalid filename"};
    }

    // Normalize to priority-prefixed filename unless caller already supplied one.
    if (!(fname.size() >= 6 && fname[0] == 'p' &&
          std::isdigit((unsigned char)fname[1]) && std::isdigit((unsigned char)fname[2]) &&
          std::isdigit((unsigned char)fname[3]) && std::isdigit((unsigned char)fname[4]) &&
          fname[5] == '_')) {
        fname = std::string("p") + pad4(priority) + "_" + fname;
    }

    std::filesystem::path outp = inbox / fname;
    if (delay_ms > 0) {
        std::string pf = std::string("retry_") + std::to_string(*next_run_at_ms) + "_" + fname;
        outp = retry / pf;
    }

    // Write atomically: write temp then rename.
    std::filesystem::path tmpp = outp;
    tmpp += ".tmp";
    {
        std::ofstream f(tmpp.string(), std::ios::binary);
        if (!f) return {StepStatus::TOOL_ERROR, "{}", "cannot write temp file"};
        f << req;
    }
    std::filesystem::rename(tmpp, outp, ec);
    if (ec) {
        std::filesystem::remove(tmpp, ec);
        return {StepStatus::TOOL_ERROR, "{}", "rename failed"};
    }

    Artifact a;
    a.type = "queue_enqueue";
    a.provenance = "queue:enqueue";
    std::ostringstream payload;
    payload << "{";
    payload << "\"ok\":true,";
    payload << "\"queue_dir\":\"" << json_mini::json_escape(qroot.string()) << "\",";
    payload << "\"path\":\"" << json_mini::json_escape(outp.string()) << "\"";
    payload << "}";
    a.content_json = payload.str();
    a.size_bytes = a.content_json.size();
    ds_tmp.slots[(uint8_t)DSSlot::DS0] = a;
    return {StepStatus::OK, a.content_json, ""};
}

} // namespace machina
