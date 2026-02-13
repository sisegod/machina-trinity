#include "machina/wal.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <mutex>
#include <vector>

#if !defined(_WIN32)
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace machina {

// ---------- CRC32 (ISO 3309 / zlib polynomial) ----------
// Used for framed WAL format to detect torn writes.
static uint32_t g_crc32_table[256];
static std::once_flag g_crc32_once;

static void crc32_do_init() {
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t c = i;
        for (int j = 0; j < 8; j++) {
            if (c & 1) c = 0xEDB88320u ^ (c >> 1);
            else       c = c >> 1;
        }
        g_crc32_table[i] = c;
    }
}

static void crc32_init_table() {
    std::call_once(g_crc32_once, crc32_do_init);
}

static uint32_t crc32_compute(const void* data, size_t len) {
    crc32_init_table();
    const uint8_t* p = (const uint8_t*)data;
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < len; i++) {
        crc = g_crc32_table[(crc ^ p[i]) & 0xFF] ^ (crc >> 8);
    }
    return crc ^ 0xFFFFFFFFu;
}

static bool wal_framed_enabled() {
    static int cached = -1;
    if (cached < 0) {
        const char* e = std::getenv("MACHINA_WAL_FRAMED");
        cached = (e && std::string(e) == "1") ? 1 : 0;
    }
    return cached == 1;
}

static int64_t epoch_sec() {
    using namespace std::chrono;
    return duration_cast<seconds>(system_clock::now().time_since_epoch()).count();
}

static int64_t epoch_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

Wal::Wal(std::filesystem::path path) : path_(std::move(path)) {}

Wal::~Wal() {
#if !defined(_WIN32)
    std::lock_guard<std::mutex> lk(mu_);
    if (fd_ >= 0) {
        ::close(fd_);
        fd_ = -1;
    }
#endif
}

void Wal::set_fsync(bool enable) {
    std::lock_guard<std::mutex> lk(mu_);
    fsync_ = enable;
}

void Wal::set_policy(const WalPolicy& policy) {
    std::lock_guard<std::mutex> lk(mu_);
    policy_ = policy;
}

std::string Wal::open(bool truncate_file) {
#if defined(_WIN32)
    (void)truncate_file;
    return "WAL is not supported on Windows build in this snapshot";
#else
    std::lock_guard<std::mutex> lk(mu_);
    if (fd_ >= 0) return "";

    std::error_code ec;
    auto parent = path_.parent_path();
    if (!parent.empty()) {
        std::filesystem::create_directories(parent, ec);
        if (ec) return std::string("create_directories: ") + ec.message();
    }

    fd_ = ::open(path_.c_str(), O_CREAT | O_WRONLY | O_APPEND, 0644);
    if (fd_ < 0) {
        return std::string("open: ") + std::strerror(errno);
    }

    if (truncate_file) {
        if (::ftruncate(fd_, 0) != 0) {
            std::string err = std::string("ftruncate: ") + std::strerror(errno);
            ::close(fd_);
            fd_ = -1;
            return err;
        }
    }

    // Track segment metadata
    segment_open_time_ = epoch_sec();
    struct stat st{};
    if (::fstat(fd_, &st) == 0) {
        current_size_ = st.st_size;
    } else {
        current_size_ = 0;
    }

    return "";
#endif
}

bool Wal::is_open() const {
#if defined(_WIN32)
    return false;
#else
    std::lock_guard<std::mutex> lk(mu_);
    return fd_ >= 0;
#endif
}

std::string Wal::append_json_line(const std::string& json) {
#if defined(_WIN32)
    (void)json;
    return "WAL is not supported on Windows build in this snapshot";
#else
    std::lock_guard<std::mutex> lk(mu_);
    if (fd_ < 0) {
        std::error_code ec;
        auto parent = path_.parent_path();
        if (!parent.empty()) std::filesystem::create_directories(parent, ec);
        fd_ = ::open(path_.c_str(), O_CREAT | O_WRONLY | O_APPEND, 0644);
        if (fd_ < 0) return std::string("open: ") + std::strerror(errno);
        segment_open_time_ = epoch_sec();
        current_size_ = 0;
    }

    // Check if rotation is needed before writing
    if (needs_rotation_locked()) {
        std::string err = rotate_locked();
        if (!err.empty()) {
            // Non-fatal: log rotation failed, continue writing to current segment
        }
    }

    std::string line = json;
    if (line.empty() || line.back() != '\n') line.push_back('\n');

    auto write_all = [&](const void* data, size_t len) -> std::string {
        const char* p = (const char*)data;
        ssize_t remaining = (ssize_t)len;
        ssize_t off = 0;
        while (off < remaining) {
            ssize_t w = ::write(fd_, p + off, (size_t)(remaining - off));
            if (w < 0) {
                if (errno == EINTR) continue;
                return std::string("write: ") + std::strerror(errno);
            }
            off += w;
        }
        return "";
    };

    ssize_t bytes_written = 0;

    if (wal_framed_enabled()) {
        // Framed format: [uint32_t len][line bytes][uint32_t crc32]
        // Allows crash detection via length prefix + CRC integrity check.
        uint32_t len = (uint32_t)line.size();
        uint32_t crc = crc32_compute(line.data(), line.size());

        std::string err = write_all(&len, sizeof(len));
        if (!err.empty()) return err;
        err = write_all(line.data(), line.size());
        if (!err.empty()) return err;
        err = write_all(&crc, sizeof(crc));
        if (!err.empty()) return err;

        bytes_written = (ssize_t)(sizeof(len) + line.size() + sizeof(crc));
    } else {
        // Legacy plain-text format (newline-delimited JSON)
        std::string err = write_all(line.data(), line.size());
        if (!err.empty()) return err;
        bytes_written = (ssize_t)line.size();
    }

    current_size_ += bytes_written;

    if (fsync_) {
        if (::fsync(fd_) != 0) {
            return std::string("fsync: ") + std::strerror(errno);
        }
    }

    return "";
#endif
}

std::string Wal::truncate() {
#if defined(_WIN32)
    return "WAL is not supported on Windows build in this snapshot";
#else
    std::lock_guard<std::mutex> lk(mu_);
    if (fd_ < 0) {
        std::error_code ec;
        auto parent = path_.parent_path();
        if (!parent.empty()) std::filesystem::create_directories(parent, ec);
        fd_ = ::open(path_.c_str(), O_CREAT | O_WRONLY | O_APPEND, 0644);
        if (fd_ < 0) return std::string("open: ") + std::strerror(errno);
    }
    if (::ftruncate(fd_, 0) != 0) return std::string("ftruncate: ") + std::strerror(errno);
    current_size_ = 0;
    return "";
#endif
}

long long Wal::size_bytes() const {
#if defined(_WIN32)
    return 0;
#else
    std::lock_guard<std::mutex> lk(mu_);
    if (fd_ < 0) {
        std::error_code ec;
        if (!std::filesystem::exists(path_, ec)) return 0;
        return (long long)std::filesystem::file_size(path_, ec);
    }
    struct stat st{};
    if (::fstat(fd_, &st) != 0) return -1;
    return (long long)st.st_size;
#endif
}

// --- Segment Rotation ---

bool Wal::needs_rotation_locked() const {
    if (policy_.max_segment_bytes > 0 && current_size_ >= policy_.max_segment_bytes) {
        return true;
    }
    if (policy_.max_segment_age_sec > 0 && segment_open_time_ > 0) {
        int64_t age = epoch_sec() - segment_open_time_;
        if (age >= policy_.max_segment_age_sec) {
            return true;
        }
    }
    return false;
}

std::string Wal::rotate_locked() {
#if defined(_WIN32)
    return "WAL rotation not supported on Windows";
#else
    if (fd_ < 0) return "";

    // Close current segment
    ::close(fd_);
    fd_ = -1;

    // Rename current file to <stem>.<epoch_ms>.jsonl
    auto parent = path_.parent_path();
    auto stem = path_.stem().string();
    auto rotated = parent / (stem + "." + std::to_string(epoch_ms()) + ".jsonl");

    std::error_code ec;
    std::filesystem::rename(path_, rotated, ec);
    if (ec) {
        // If rename fails, try to reopen the original
        fd_ = ::open(path_.c_str(), O_CREAT | O_WRONLY | O_APPEND, 0644);
        return std::string("rotate rename: ") + ec.message();
    }

    // fsync parent directory for rename durability
    int dir_fd = ::open(parent.c_str(), O_RDONLY | O_DIRECTORY);
    if (dir_fd >= 0) { ::fsync(dir_fd); ::close(dir_fd); }

    // Open fresh segment
    fd_ = ::open(path_.c_str(), O_CREAT | O_WRONLY | O_APPEND, 0644);
    if (fd_ < 0) {
        return std::string("rotate reopen: ") + std::strerror(errno);
    }

    segment_open_time_ = epoch_sec();
    current_size_ = 0;

    return "";
#endif
}

std::string Wal::rotate_now() {
    std::lock_guard<std::mutex> lk(mu_);
    return rotate_locked();
}

std::vector<std::filesystem::path> Wal::list_segments() const {
    std::lock_guard<std::mutex> lk(mu_);
    std::vector<std::filesystem::path> segments;

    auto parent = path_.parent_path();
    if (parent.empty()) parent = ".";
    auto stem = path_.stem().string();

    std::error_code ec;
    if (!std::filesystem::exists(parent, ec)) return segments;

    for (const auto& entry : std::filesystem::directory_iterator(parent, ec)) {
        if (!entry.is_regular_file()) continue;
        auto fname = entry.path().filename().string();
        // Match: <stem>.jsonl (active) or <stem>.<digits>.jsonl (rotated)
        if (fname == path_.filename().string()) {
            segments.push_back(entry.path());
        } else if (fname.starts_with(stem + ".") && fname.ends_with(".jsonl")) {
            segments.push_back(entry.path());
        }
    }

    // Sort by filename (timestamp in name = chronological order)
    std::sort(segments.begin(), segments.end());

    return segments;
}

int Wal::enforce_retention() {
    std::lock_guard<std::mutex> lk(mu_);
    int deleted = 0;

    auto parent = path_.parent_path();
    if (parent.empty()) parent = ".";
    auto stem = path_.stem().string();

    std::error_code ec;
    if (!std::filesystem::exists(parent, ec)) return 0;

    // Collect rotated segments (NOT the active file)
    struct SegInfo {
        std::filesystem::path path;
        int64_t size;
    };
    std::vector<SegInfo> rotated;

    for (const auto& entry : std::filesystem::directory_iterator(parent, ec)) {
        if (!entry.is_regular_file()) continue;
        auto fname = entry.path().filename().string();
        // Only rotated segments: <stem>.<digits>.jsonl
        if (fname == path_.filename().string()) continue; // skip active
        if (fname.starts_with(stem + ".") && fname.ends_with(".jsonl")) {
            rotated.push_back({entry.path(), (int64_t)entry.file_size(ec)});
        }
    }

    // Sort oldest first (by filename which contains timestamp)
    std::sort(rotated.begin(), rotated.end(),
              [](const SegInfo& a, const SegInfo& b) { return a.path < b.path; });

    // Enforce max_segments (count includes active segment)
    while (policy_.max_segments > 0 &&
           (int)(rotated.size() + 1) > policy_.max_segments &&
           !rotated.empty()) {
        std::filesystem::remove(rotated.front().path, ec);
        rotated.erase(rotated.begin());
        deleted++;
    }

    // Enforce max_total_bytes
    if (policy_.max_total_bytes > 0) {
        int64_t total = current_size_;
        for (const auto& s : rotated) total += s.size;

        while (total > policy_.max_total_bytes && !rotated.empty()) {
            total -= rotated.front().size;
            std::filesystem::remove(rotated.front().path, ec);
            rotated.erase(rotated.begin());
            deleted++;
        }
    }

    return deleted;
}

} // namespace machina
