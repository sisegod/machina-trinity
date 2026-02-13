#pragma once

#include <cstdint>
#include <filesystem>
#include <mutex>
#include <string>
#include <vector>

namespace machina {

// WAL lifecycle policy for automatic segment rotation and retention.
struct WalPolicy {
    int64_t max_segment_bytes{16 * 1024 * 1024};  // 16 MB per segment
    int max_segment_age_sec{3600};                  // 1 hour max age
    int max_segments{10};                            // keep last N segments
    int64_t max_total_bytes{256 * 1024 * 1024};    // 256 MB total cap
};

// Wal: append-only JSONL log with automatic segment rotation.
//
// Each append writes a single line: <json>\n
// When a segment exceeds the size or age limit, it is rotated to
// <basename>.<timestamp>.jsonl and a fresh segment is opened.
//
// Thread-safe, with optional fsync per append.
class Wal {
public:
    explicit Wal(std::filesystem::path path);
    ~Wal();

    Wal(const Wal&) = delete;
    Wal& operator=(const Wal&) = delete;

    void set_fsync(bool enable);

    // Set lifecycle policy. Call before open() or between rotations.
    void set_policy(const WalPolicy& policy);

    // Opens the WAL file (creates parent dirs if needed).
    // If truncate=true, truncates the existing file to empty.
    // Returns empty string on success.
    std::string open(bool truncate=false);

    bool is_open() const;

    // Appends one JSON record line (json + '\n').
    // Automatically rotates the segment if policy limits are exceeded.
    // Returns empty string on success.
    std::string append_json_line(const std::string& json);

    // Truncates the file to empty (keeps it open).
    std::string truncate();

    // Returns current file size in bytes.
    long long size_bytes() const;

    // Force rotation of the current segment. The current file is renamed
    // to <basename>.<epoch_ms>.jsonl and a new empty segment is opened.
    // Returns empty string on success.
    std::string rotate_now();

    // Enforce retention: delete old segments beyond max_segments / max_total_bytes.
    // Returns number of segments deleted.
    int enforce_retention();

    // List all segment files (including current) sorted oldest-first.
    std::vector<std::filesystem::path> list_segments() const;

private:
    std::filesystem::path path_;       // base path (active segment)
    int fd_ = -1;
    bool fsync_ = false;
    mutable std::mutex mu_;
    WalPolicy policy_;
    int64_t segment_open_time_{0};     // epoch seconds when current segment opened
    int64_t current_size_{0};          // tracked to avoid frequent stat() calls

    // Internal: rotate under lock
    std::string rotate_locked();
    // Internal: check if rotation is needed
    bool needs_rotation_locked() const;
};

} // namespace machina
