#include "test_common.h"

#include "machina/tools.h"

#include <json-c/json.h>
#include <filesystem>
#include <cstdlib>

using machina::DSState;
using machina::ToolResult;

// from memory_tools.cpp
namespace machina {
ToolResult tool_memory_append(const std::string& input_json, DSState& ds_tmp);
ToolResult tool_memory_query(const std::string& input_json, DSState& ds_tmp);
}

static void setenv_str(const char* k, const std::string& v) {
#ifdef _WIN32
    (void)k; (void)v;
#else
    setenv(k, v.c_str(), 1);
#endif
}

int main() {
    namespace fs = std::filesystem;
    fs::path root = fs::temp_directory_path() / "machina_test_memory";
    fs::create_directories(root);

    setenv_str("MACHINA_ROOT", root.string());
    setenv_str("MACHINA_MEMORY_ROOT", root.string());
    setenv_str("MACHINA_MEMORY_RECENCY_TAU_SEC", "100000000");

    DSState tmp;

    // Insert docs: two near-duplicates and one different
    machina::tool_memory_append("{\"stream\":\"t\",\"text\":\"alpha beta beta\"}", tmp);
    machina::tool_memory_append("{\"stream\":\"t\",\"text\":\"alpha beta beta (dup)\"}", tmp);
    machina::tool_memory_append("{\"stream\":\"t\",\"text\":\"alpha gamma different\"}", tmp);

    ToolResult r = machina::tool_memory_query(
        "{\"stream\":\"t\",\"query\":\"alpha\",\"top_k\":2,\"candidate_k\":10,\"rerank\":\"mmr\",\"mmr_lambda\":0.4,\"debug\":true}",
        tmp);

    expect_true(r.status == machina::StepStatus::OK, "memory_query should succeed");

    json_object* rootj = json_tokener_parse(r.output_json.c_str());
    expect_true(rootj != nullptr, "output json parse failed");

    json_object* matches = nullptr;
    expect_true(json_object_object_get_ex(rootj, "matches", &matches), "missing matches");
    expect_true(json_object_is_type(matches, json_type_array), "matches must be array");
    expect_eq_ll((long long)json_object_array_length(matches), 2, "matches length");

    // Ensure the two lines are different (MMR should avoid selecting the exact same thing)
    json_object* m0 = json_object_array_get_idx(matches, 0);
    json_object* m1 = json_object_array_get_idx(matches, 1);
    json_object* l0 = nullptr;
    json_object* l1 = nullptr;
    expect_true(json_object_object_get_ex(m0, "raw", &l0), "match0 missing raw");
    expect_true(json_object_object_get_ex(m1, "raw", &l1), "match1 missing raw");
    std::string s0 = json_object_get_string(l0);
    std::string s1 = json_object_get_string(l1);
    expect_true(s0 != s1, "MMR should return two different lines");

    json_object_put(rootj);

    // cleanup best-effort
    std::error_code ec;
    fs::remove_all(root, ec);

    return 0;
}
