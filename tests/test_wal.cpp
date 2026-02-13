#include "test_common.h"

#include "machina/wal.h"

#include <filesystem>
#include <fstream>

using machina::Wal;

int main() {
    namespace fs = std::filesystem;
    fs::path dir = fs::temp_directory_path() / "machina_test_wal";
    std::error_code ec;
    fs::remove_all(dir, ec);
    fs::create_directories(dir, ec);

    fs::path p = dir / "a.jsonl";
    Wal wal(p);
    std::string err = wal.open(true);
    expect_true(err.empty(), "wal open should succeed: " + err);

    err = wal.append_json_line("{\"x\":1}");
    expect_true(err.empty(), "append 1 should succeed: " + err);
    err = wal.append_json_line("{\"x\":2}");
    expect_true(err.empty(), "append 2 should succeed: " + err);

    long long sz = wal.size_bytes();
    expect_true(sz > 0, "wal size should be >0");

    err = wal.truncate();
    expect_true(err.empty(), "truncate should succeed: " + err);

    long long sz2 = wal.size_bytes();
    expect_true(sz2 == 0, "wal size after truncate should be 0");

    // Clean
    fs::remove_all(dir, ec);
    return 0;
}
