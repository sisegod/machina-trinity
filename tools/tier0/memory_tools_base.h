#pragma once

#include "machina/json_mini.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <sstream>
#include <string>
#include <vector>

namespace machina::memtools {

inline std::mutex g_mem_mu;

inline std::string json_escape(const std::string& s) {
    return json_mini::json_escape(s);
}

inline int64_t now_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

inline std::filesystem::path memory_root() {
    namespace fs = std::filesystem;
    if (const char* e = std::getenv("MACHINA_MEMORY_ROOT")) {
        fs::path p = fs::path(e);
        if (p.is_absolute()) return p;
    }
    const char* root = std::getenv("MACHINA_ROOT");
    fs::path base = root ? fs::path(root) : fs::current_path();
    return base / "work" / "memory";
}

inline bool is_path_under(const std::filesystem::path& p, const std::filesystem::path& root) {
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

inline std::string sanitize_stream(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (char c : s) {
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c=='_' || c=='-' || c=='.') out.push_back(c);
        else out.push_back('_');
    }
    if (out.empty()) out = "default";
    return out;
}

inline size_t getenv_size_t(const char* k, size_t defv) {
    if (const char* e = std::getenv(k)) {
        try { return (size_t)std::stoull(e); } catch (...) {}
    }
    return defv;
}

inline void maybe_rotate(const std::filesystem::path& file) {
    namespace fs = std::filesystem;
    std::error_code ec;
    if (!fs::exists(file, ec)) return;
    auto sz = fs::file_size(file, ec);
    if (ec) return;

    size_t rotate_bytes = getenv_size_t("MACHINA_MEMORY_ROTATE_BYTES", 64ull * 1024ull * 1024ull);
    if (rotate_bytes == 0) return;
    if (sz < rotate_bytes) return;

    fs::path dir = file.parent_path();
    fs::create_directories(dir, ec);
    std::string stem = file.filename().string();
    int64_t ts = now_ms();
    fs::path rotated = dir / (stem + "." + std::to_string(ts) + ".rotated");
    fs::rename(file, rotated, ec);
}

inline std::vector<std::string> tail_lines_matching(const std::filesystem::path& file,
                                                    const std::string& needle,
                                                    size_t limit,
                                                    size_t max_bytes) {
    namespace fs = std::filesystem;
    std::vector<std::string> out;
    std::error_code ec;
    if (!fs::exists(file, ec)) return out;

    std::ifstream in(file, std::ios::binary);
    if (!in) return out;

    in.seekg(0, std::ios::end);
    std::streamoff end = in.tellg();
    std::streamoff start = end - (std::streamoff)max_bytes;
    if (start < 0) start = 0;
    in.seekg(start, std::ios::beg);

    std::string buf;
    buf.assign((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());

    std::vector<std::string> lines;
    std::string cur;
    for (char c : buf) {
        if (c == '\n') {
            lines.push_back(cur);
            cur.clear();
        } else if (c != '\r') {
            cur.push_back(c);
        }
    }
    if (!cur.empty()) lines.push_back(cur);

    for (auto it = lines.rbegin(); it != lines.rend(); ++it) {
        if (!needle.empty() && it->find(needle) == std::string::npos) continue;
        out.push_back(*it);
        if (out.size() >= limit) break;
    }

    return out;
}

inline std::vector<std::string> tokenize_lower(const std::string& s) {
    std::vector<std::string> out;
    std::string cur;
    cur.reserve(32);
    auto flush = [&]() {
        if (cur.size() >= 2) out.push_back(cur);
        cur.clear();
    };
    for (unsigned char uc : s) {
        char c = (char)uc;
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')) {
            cur.push_back((char)std::tolower(uc));
        } else if (c >= '0' && c <= '9') {
            cur.push_back(c);
        } else {
            flush();
        }
        if (cur.size() > 64) flush();
    }
    flush();
    return out;
}

inline std::string extract_text_best_effort(const std::string& line) {
    auto text = machina::json_mini::get_string(line, "text");
    if (text) return *text;
    auto event_raw = machina::json_mini::get_object_raw(line, "event");
    if (event_raw && !event_raw->empty()) return *event_raw;
    return line;
}

inline std::vector<std::filesystem::path> list_memory_files(const std::filesystem::path& mr,
                                                            const std::string& stream,
                                                            size_t max_files) {
    namespace fs = std::filesystem;
    std::vector<fs::path> files;
    std::error_code ec;
    fs::path main = mr / (stream + ".jsonl");
    if (fs::exists(main, ec)) files.push_back(main);

    std::string prefix = stream + ".jsonl.";
    std::string suffix = ".rotated";
    if (fs::exists(mr, ec) && fs::is_directory(mr, ec)) {
        for (auto& ent : fs::directory_iterator(mr, ec)) {
            if (ec) break;
            if (!ent.is_regular_file(ec)) continue;
            auto name = ent.path().filename().string();
            if (name.rfind(prefix, 0) == 0 && name.size() >= suffix.size() &&
                name.substr(name.size() - suffix.size()) == suffix) {
                files.push_back(ent.path());
            }
        }
    }

    auto mtime = [](const fs::path& p)->fs::file_time_type{
        std::error_code e;
        return fs::last_write_time(p, e);
    };

    if (!files.empty()) {
        std::vector<fs::path> rotated;
        for (size_t i=0;i<files.size();i++) if (files[i] != main) rotated.push_back(files[i]);
        std::sort(rotated.begin(), rotated.end(), [&](const fs::path& a, const fs::path& b){
            return mtime(a) > mtime(b);
        });
        files.clear();
        if (fs::exists(main, ec)) files.push_back(main);
        files.insert(files.end(), rotated.begin(), rotated.end());
    }

    if (max_files > 0 && files.size() > max_files) files.resize(max_files);
    return files;
}

} // namespace machina::memtools
