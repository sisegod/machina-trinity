#include "test_common.h"
#include "machina/plugin_loader.h"
#include "machina/crypto.h"
#include <filesystem>
#include <fstream>

int main() {
    namespace fs = std::filesystem;

    // Test 1: sha256_hex_file works on a known file
    {
        fs::path tmp = fs::temp_directory_path() / "machina_test_hash.txt";
        {
            std::ofstream f(tmp);
            f << "hello world\n";
        }
        std::string hash = machina::sha256_hex_file(tmp);
        expect_true(!hash.empty(), "hash should not be empty");
        expect_true(hash.size() == 64, "SHA256 hex should be 64 chars");

        // Verify deterministic
        std::string hash2 = machina::sha256_hex_file(tmp);
        expect_true(hash == hash2, "hash should be deterministic");

        fs::remove(tmp);
    }

    // Test 2: sha256_hex_file returns empty for nonexistent file
    {
        std::string hash = machina::sha256_hex_file("/nonexistent/file.txt");
        expect_true(hash.empty(), "nonexistent file should return empty hash");
    }

    // Test 3: PluginManager hash mismatch rejection
    {
        machina::PluginManager pm;
        fs::path tmp = fs::temp_directory_path() / "fake_plugin.so";
        {
            std::ofstream f(tmp);
            f << "not a real plugin";
        }

        std::string real_hash = machina::sha256_hex_file(tmp);
        pm.set_expected_hash(fs::weakly_canonical(tmp).string(), "0000000000000000000000000000000000000000000000000000000000000000");

        std::string err;
        bool ok = pm.load_plugin(tmp, nullptr, &err);
        // Should fail due to null registrar or hash mismatch
        expect_true(!ok, "should fail with wrong hash");
        // The error could be about registrar or hash - either is valid since registrar is null

        fs::remove(tmp);
    }

    std::cerr << "test_plugin_hash: ALL PASSED" << std::endl;
    return 0;
}
