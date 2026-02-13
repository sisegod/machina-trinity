#include "test_common.h"

#include "machina/tools.h"

#include <json-c/json.h>

#include <filesystem>
#include <cstdlib>

using machina::ToolResult;
using machina::DSState;
using machina::StepStatus;

// Forward decl (defined in tools/tier0/memory_tools.cpp)
namespace machina {
ToolResult tool_memory_append(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_memory_query(const std::string& input_json, DSState& ds_tmp);
}

static int array_len(json_object* obj, const char* key) {
    json_object* a = nullptr;
    if (!json_object_object_get_ex(obj, key, &a)) return -1;
    if (!json_object_is_type(a, json_type_array)) return -1;
    return json_object_array_length(a);
}

int main() {
    namespace fs = std::filesystem;
    fs::path dir = fs::temp_directory_path() / "machina_test_memory";
    std::error_code ec;
    fs::remove_all(dir, ec);
    fs::create_directories(dir, ec);

    // Set memory root to absolute temp dir
    setenv("MACHINA_ROOT", dir.string().c_str(), 1);
    setenv("MACHINA_MEMORY_ROOT", dir.string().c_str(), 1);
    setenv("MACHINA_MEMORY_ROTATE_BYTES", "0", 1);
    setenv("MACHINA_MEMORY_VECDB_ENABLE", "0", 1);

    DSState ds;

    // Append a few near-duplicate memories
    auto append = [&](const std::string& text) {
        std::string in = std::string("{\"stream\":\"test\",\"text\":\"") + text + "\"}";
        ToolResult r = machina::tool_memory_append(in, ds);
        expect_true(r.status == StepStatus::OK, "memory_append failed: " + r.error);
    };

    append("alpha beta same same");
    append("alpha beta same same again");
    append("alpha gamma different");
    append("alpha delta another");

    std::string q =
        "{\"stream\":\"test\",\"query\":\"alpha\",\"top_k\":2,\"candidate_k\":20,\"mode\":\"hybrid\",\"rerank\":\"mmr\",\"mmr_lambda\":0.3,\"debug\":true}";

    ToolResult qr = machina::tool_memory_query(q, ds);
    expect_true(qr.status == StepStatus::OK, "memory_query failed: " + qr.error);

    json_object* root = json_tokener_parse(qr.output_json.c_str());
    expect_true(root != nullptr, "output_json not valid JSON");

    int n = array_len(root, "matches");
    expect_eq_ll(n, 2, "expected 2 matches");

    json_object* matches = nullptr;
    json_object_object_get_ex(root, "matches", &matches);
    json_object* m0 = json_object_array_get_idx(matches, 0);
    json_object* m1 = json_object_array_get_idx(matches, 1);

    const char* line0 = nullptr;
    const char* line1 = nullptr;
    json_object* v = nullptr;
    if (json_object_object_get_ex(m0, "raw", &v)) line0 = json_object_get_string(v);
    if (json_object_object_get_ex(m1, "raw", &v)) line1 = json_object_get_string(v);

    expect_true(line0 && line1, "matches should contain 'raw'");
    expect_true(std::string(line0) != std::string(line1), "MMR should diversify: rows should differ");

    // Second item's mmr_max_sim should be < 1.0 in most cases
    double mmr_max_sim = 1.0;
    if (json_object_object_get_ex(m1, "mmr_max_sim", &v)) mmr_max_sim = json_object_get_double(v);
    expect_true(mmr_max_sim < 0.9999, "mmr_max_sim should be < 1.0");

    json_object_put(root);

    fs::remove_all(dir, ec);
    return 0;
}
