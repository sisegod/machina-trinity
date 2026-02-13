#include "machina/tools.h"
#include "machina/json_mini.h"

#include <cerrno>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <random>
#include <sstream>
#include <string>
#include <vector>

#ifndef _WIN32
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace {

static const char* b64_table = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static std::string b64_encode(const std::string& in) {
    std::string out;
    out.reserve(((in.size() + 2) / 3) * 4);
    size_t i = 0;
    while (i < in.size()) {
        uint32_t a = (uint8_t)in[i++];
        uint32_t b = (i < in.size()) ? (uint8_t)in[i++] : 0;
        uint32_t c = (i < in.size()) ? (uint8_t)in[i++] : 0;
        uint32_t triple = (a << 16) | (b << 8) | c;
        out.push_back(b64_table[(triple >> 18) & 0x3F]);
        out.push_back(b64_table[(triple >> 12) & 0x3F]);
        out.push_back((i - 1 <= in.size()) ? b64_table[(triple >> 6) & 0x3F] : '=');
        out.push_back((i <= in.size()) ? b64_table[triple & 0x3F] : '=');
    }
    // Fix padding for exact boundaries
    size_t mod = in.size() % 3;
    if (mod == 1) {
        out[out.size() - 1] = '=';
        out[out.size() - 2] = '=';
    } else if (mod == 2) {
        out[out.size() - 1] = '=';
    }
    return out;
}

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

} // namespace

namespace machina {

// AID.FILE.READ.v1
ToolResult tool_file_read(const std::string& input_json, DSState& ds_tmp) {
    auto path_raw = json_mini::get_string(input_json, "path").value_or("");
    size_t max_bytes = (size_t)json_mini::get_int(input_json, "max_bytes").value_or(262144);
    constexpr size_t MAX_BYTES_CAP = 10ULL * 1024 * 1024; // 10MB hard upper limit
    if (max_bytes > MAX_BYTES_CAP) max_bytes = MAX_BYTES_CAP;
    if (path_raw.empty()) return {StepStatus::TOOL_ERROR, "{}", "missing path"};

    std::string resolved = resolve_file_best_effort(path_raw);
    namespace fs = std::filesystem;
    fs::path rp(resolved);

    // Default allow: under MACHINA_ROOT only.
    fs::path root = fs::path(std::getenv("MACHINA_ROOT") ? std::getenv("MACHINA_ROOT") : ".");
    if (!is_path_under(rp, root)) {
        return {StepStatus::TOOL_ERROR, "{}", "read path not allowed (outside MACHINA_ROOT): " + path_raw};
    }

    // TOCTOU hardening: open with O_NOFOLLOW, then fstat to verify path didn't change
    int fd = ::open(resolved.c_str(), O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return {StepStatus::TOOL_ERROR, "{}", "cannot open file: " + path_raw};

    // Post-open verification: fstat + re-check canonical path matches
    struct stat st;
    if (::fstat(fd, &st) != 0 || S_ISLNK(st.st_mode)) {
        ::close(fd);
        return {StepStatus::TOOL_ERROR, "{}", "fstat failed or symlink detected: " + path_raw};
    }
    // Verify the opened file is still under MACHINA_ROOT by checking /proc/self/fd
    {
        char proc_path[64];
        snprintf(proc_path, sizeof(proc_path), "/proc/self/fd/%d", fd);
        char real_target[4096];
        ssize_t len = ::readlink(proc_path, real_target, sizeof(real_target) - 1);
        if (len > 0) {
            real_target[len] = '\0';
            fs::path actual_path(real_target);
            if (!is_path_under(actual_path, root)) {
                ::close(fd);
                return {StepStatus::TOOL_ERROR, "{}", "TOCTOU: file escaped sandbox after open: " + path_raw};
            }
        }
    }

    std::string buf;
    buf.resize(max_bytes);
    ssize_t n = ::read(fd, buf.data(), max_bytes);
    if (n < 0) { ::close(fd); return {StepStatus::TOOL_ERROR, "{}", "read error: " + path_raw}; }
    bool truncated = ((size_t)n == max_bytes && st.st_size > (off_t)max_bytes);
    buf.resize((size_t)n);
    ::close(fd);

    Artifact a;
    a.type = "file_read";
    a.provenance = "fs:read";
    a.size_bytes = buf.size();

    std::string b64 = b64_encode(buf);
    std::ostringstream payload;
    payload << "{";
    payload << "\"path\":\"" << json_mini::json_escape(path_raw) << "\",";
    payload << "\"resolved\":\"" << json_mini::json_escape(resolved) << "\",";
    payload << "\"bytes\":" << buf.size() << ",";
    payload << "\"truncated\":" << (truncated ? "true" : "false") << ",";
    payload << "\"content_b64\":\"" << b64 << "\"";
    payload << "}";

    a.content_json = payload.str();
    ds_tmp.slots[(uint8_t)DSSlot::DS0] = a;
    return {StepStatus::OK, a.content_json, ""};
}

// AID.FILE.WRITE.v1
ToolResult tool_file_write(const std::string& input_json, DSState& ds_tmp) {
    auto rel = json_mini::get_string(input_json, "path").value_or("");
    auto content = json_mini::get_string(input_json, "content").value_or("");
    bool overwrite = json_mini::get_bool(input_json, "overwrite").value_or(false);
    bool mkdirs = json_mini::get_bool(input_json, "mkdirs").value_or(true);

    if (rel.empty()) return {StepStatus::TOOL_ERROR, "{}", "missing path"};

    // Default write root: <repo>/work
    namespace fs = std::filesystem;
    fs::path repo = fs::path(std::getenv("MACHINA_ROOT") ? std::getenv("MACHINA_ROOT") : ".");
    fs::path write_root = repo / "work";
    if (const char* wr = std::getenv("MACHINA_FS_WRITE_ROOT")) {
        write_root = fs::path(wr);
    }
    if (mkdirs) {
        std::error_code ec;
        fs::create_directories(write_root, ec);
    }

    fs::path p(rel);
    fs::path target = p.is_absolute() ? p : (write_root / p);

    if (!is_path_under(target, write_root)) {
        return {StepStatus::TOOL_ERROR, "{}", "write path not allowed (outside write root): " + rel};
    }

    if (content.size() > 1024ull * 1024ull) {
        return {StepStatus::TOOL_ERROR, "{}", "content too large (>1MB)"};
    }

    if (mkdirs) {
        std::error_code ec;
        fs::create_directories(target.parent_path(), ec);
    }

    if (!overwrite && fs::exists(target)) {
        return {StepStatus::TOOL_ERROR, "{}", "file exists (overwrite=false): " + rel};
    }

    // 2-phase atomic write: tmp → fsync → rename
    // Prevents partial writes on crash and enables rollback.
    std::string tmp_path = target.string() + ".tmp."
        + std::to_string(std::random_device{}());

    std::ofstream f(tmp_path, std::ios::binary);
    if (!f) return {StepStatus::TOOL_ERROR, "{}", "cannot write file: " + rel};
    f.write(content.data(), (std::streamsize)content.size());
    if (!f.good()) {
        f.close();
        std::error_code ec;
        fs::remove(tmp_path, ec);
        return {StepStatus::TOOL_ERROR, "{}", "write failed (I/O error): " + rel};
    }
    f.close();

#ifndef _WIN32
    // fsync temp file for durability
    int fd = ::open(tmp_path.c_str(), O_RDONLY);
    if (fd >= 0) { ::fsync(fd); ::close(fd); }
#endif

    // Backup existing file if overwriting
    std::string bak_path = target.string() + ".bak";
    bool had_backup = false;
    if (overwrite && fs::exists(target)) {
        std::error_code ec;
        fs::copy_file(target, bak_path, fs::copy_options::overwrite_existing, ec);
        had_backup = !ec;
    }

    // Atomic rename (POSIX guarantees atomicity on same filesystem)
    std::error_code rename_ec;
    fs::rename(tmp_path, target, rename_ec);
    if (rename_ec) {
        std::error_code ec;
        fs::remove(tmp_path, ec);  // cleanup temp
        if (had_backup) fs::rename(bak_path, target, ec);  // restore backup
        return {StepStatus::TOOL_ERROR, "{}", "rename failed: " + rename_ec.message()};
    }

#ifndef _WIN32
    // fsync parent directory for metadata durability
    int dfd = ::open(target.parent_path().c_str(), O_RDONLY | O_DIRECTORY);
    if (dfd >= 0) { ::fsync(dfd); ::close(dfd); }
#endif

    Artifact a;
    a.type = "file_write";
    a.provenance = "fs:write";
    a.size_bytes = content.size();

    std::ostringstream payload;
    payload << "{";
    payload << "\"ok\":true,";
    payload << "\"path\":\"" << json_mini::json_escape(rel) << "\",";
    payload << "\"written\":\"" << json_mini::json_escape(target.string()) << "\",";
    payload << "\"bytes\":" << content.size();
    payload << "}";
    a.content_json = payload.str();
    ds_tmp.slots[(uint8_t)DSSlot::DS0] = a;

    return {StepStatus::OK, a.content_json, ""};
}

} // namespace machina
