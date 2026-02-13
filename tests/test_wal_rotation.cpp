#include "test_common.h"
#include "machina/wal.h"

#include <filesystem>
#include <fstream>
#include <string>

using machina::Wal;
using machina::WalPolicy;

int main() {
    namespace fs = std::filesystem;
    fs::path dir = fs::temp_directory_path() / "machina_test_wal_rot";
    std::error_code ec;
    fs::remove_all(dir, ec);
    fs::create_directories(dir, ec);

    // Test 1: Basic rotation trigger by size
    {
        fs::path p = dir / "rot_size.jsonl";
        Wal wal(p);
        WalPolicy pol;
        pol.max_segment_bytes = 200; // very small for testing
        pol.max_segment_age_sec = 0; // disable time-based
        pol.max_segments = 5;
        wal.set_policy(pol);
        wal.open(true);

        // Write enough to trigger rotation
        std::string line = "{\"i\":0,\"padding\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}";
        for (int i = 0; i < 20; i++) {
            std::string err = wal.append_json_line(line);
            expect_true(err.empty(), "append should succeed: " + err);
        }

        auto segments = wal.list_segments();
        expect_true(segments.size() > 1, "should have multiple segments after size-based rotation");
    }

    // Test 2: Retention enforcement
    {
        fs::path p = dir / "rot_retain.jsonl";
        Wal wal(p);
        WalPolicy pol;
        pol.max_segment_bytes = 100;
        pol.max_segment_age_sec = 0;
        pol.max_segments = 3;
        wal.set_policy(pol);
        wal.open(true);

        std::string line = "{\"padding\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}";
        for (int i = 0; i < 30; i++) {
            wal.append_json_line(line);
        }

        int deleted = wal.enforce_retention();
        auto segments = wal.list_segments();
        expect_true((int)segments.size() <= pol.max_segments,
                    "retention should cap segments at " + std::to_string(pol.max_segments));
        (void)deleted;
    }

    // Test 3: Manual rotate_now()
    {
        fs::path p = dir / "rot_manual.jsonl";
        Wal wal(p);
        wal.open(true);

        wal.append_json_line("{\"a\":1}");
        expect_true(wal.size_bytes() > 0, "should have data");

        std::string err = wal.rotate_now();
        expect_true(err.empty(), "rotate_now should succeed: " + err);

        // Active segment should be fresh (empty or very small)
        long long sz = wal.size_bytes();
        expect_true(sz == 0, "active segment should be empty after rotation");

        // Should have 2 segments now (rotated + new active)
        auto segments = wal.list_segments();
        expect_true(segments.size() == 2, "should have 2 segments after manual rotation");
    }

    // Test 4: set_policy + set_fsync work without crash
    {
        fs::path p = dir / "rot_policy.jsonl";
        Wal wal(p);
        WalPolicy pol;
        pol.max_segment_bytes = 1024 * 1024;
        pol.max_total_bytes = 10 * 1024 * 1024;
        wal.set_policy(pol);
        wal.set_fsync(false);
        wal.open(true);
        wal.append_json_line("{\"test\":true}");
        expect_true(wal.size_bytes() > 0, "policy wal should have data");
    }

    // Test 5: Backward compat — original API still works
    {
        fs::path p = dir / "compat.jsonl";
        Wal wal(p);
        std::string err = wal.open(true);
        expect_true(err.empty(), "open should work");
        expect_true(wal.is_open(), "should be open");

        err = wal.append_json_line("{\"x\":1}");
        expect_true(err.empty(), "append should work");

        long long sz = wal.size_bytes();
        expect_true(sz > 0, "size should be >0");

        err = wal.truncate();
        expect_true(err.empty(), "truncate should work");
        expect_true(wal.size_bytes() == 0, "size after truncate should be 0");
    }

    // Cleanup
    fs::remove_all(dir, ec);
    std::cerr << "test_wal_rotation: ALL PASSED" << std::endl;
    return 0;
}
