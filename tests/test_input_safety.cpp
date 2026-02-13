#include "test_common.h"

// Include runner utilities (safe_merge_patch, filter_menu_by_capabilities)
#include "../runner/runner_utils.h"

// Stub for cmd_run (linked via runner_utils.cpp → process_queue_job, not tested here)
int cmd_run(int, char**) { return 1; }

#include <iostream>
#include <string>

using namespace machina;

// ---- safe_merge_patch tests ----

static void test_safe_merge_blocks_system_prefix() {
    std::string base = R"({"goal_id":"test"})";
    std::string patch = R"({"_system_compile_error":"bad","normal_key":"ok"})";
    std::string result = safe_merge_patch(base, patch);
    // _system_compile_error should be blocked, normal_key should pass
    expect_true(result.find("normal_key") != std::string::npos, "safe_merge: normal_key should pass");
    expect_true(result.find("_system_compile_error") == std::string::npos, "safe_merge: _system prefix should be blocked");
}

static void test_safe_merge_blocks_queue_prefix() {
    std::string base = R"({"a":"1"})";
    std::string patch = R"({"_queue_priority":"high","b":"2"})";
    std::string result = safe_merge_patch(base, patch);
    expect_true(result.find("_queue_priority") == std::string::npos, "safe_merge: _queue prefix should be blocked");
    expect_true(result.find("\"b\"") != std::string::npos, "safe_merge: b should pass");
}

static void test_safe_merge_blocks_meta_prefix() {
    std::string base = R"({})";
    std::string patch = R"({"_meta_internal":"x","input_path":"foo.txt"})";
    std::string result = safe_merge_patch(base, patch);
    expect_true(result.find("_meta_internal") == std::string::npos, "safe_merge: _meta prefix should be blocked");
    expect_true(result.find("input_path") != std::string::npos, "safe_merge: input_path should pass");
}

static void test_safe_merge_empty_patch() {
    std::string base = R"({"x":"1"})";
    std::string result = safe_merge_patch(base, "{}");
    expect_true(result.find("\"x\"") != std::string::npos, "safe_merge: empty patch preserves base");
}

static void test_safe_merge_invalid_patch() {
    std::string base = R"({"x":"1"})";
    std::string result = safe_merge_patch(base, "not json");
    expect_true(result.find("\"x\"") != std::string::npos, "safe_merge: invalid patch preserves base");
}

static void test_safe_merge_allowed_keys_whitelist() {
    std::string base = R"({"a":"1"})";
    std::string patch = R"({"b":"2","c":"3","d":"4"})";
    std::string result = safe_merge_patch(base, patch, {"_system"}, {"b", "d"});
    expect_true(result.find("\"b\"") != std::string::npos, "safe_merge: whitelisted key b should pass");
    expect_true(result.find("\"d\"") != std::string::npos, "safe_merge: whitelisted key d should pass");
    expect_true(result.find("\"c\"") == std::string::npos, "safe_merge: non-whitelisted key c should be blocked");
}

static void test_safe_merge_custom_blocked_prefixes() {
    std::string base = R"({})";
    std::string patch = R"({"danger_key":"bad","safe_key":"ok"})";
    std::string result = safe_merge_patch(base, patch, {"danger_"}, {});
    expect_true(result.find("danger_key") == std::string::npos, "safe_merge: custom prefix should be blocked");
    expect_true(result.find("safe_key") != std::string::npos, "safe_merge: non-matching key should pass");
}

// ---- filter_menu_by_capabilities tests ----

static Menu make_test_menu() {
    Menu menu;
    auto add = [&](uint16_t sid, const std::string& aid, const std::string& name) {
        MenuItem mi;
        mi.sid.value = sid;
        mi.aid = aid;
        mi.name = name;
        menu.items.push_back(mi);
    };
    add(1, "AID.ERROR_SCAN.v1", "error_scan");
    add(2, "AID.FS.READ_FILE.v1", "file_read");
    add(3, "AID.GENESIS.WRITE_FILE.v1", "genesis_write");
    add(4, "AID.GENESIS.COMPILE_SHARED.v1", "genesis_compile");
    add(5, "AID.GENESIS.LOAD_PLUGIN.v1", "genesis_load");
    add(6, "AID.NOOP.v1", "noop");
    menu.buildIndex();
    return menu;
}

static void test_filter_allowed_exact() {
    Menu menu = make_test_menu();
    std::vector<std::string> allowed = {"AID.ERROR_SCAN.v1", "AID.FS.READ_FILE.v1"};
    std::vector<std::string> blocked = {};
    Menu filtered = filter_menu_by_capabilities(menu, allowed, blocked);
    expect_true(filtered.items.size() == 2, "filter: allowed exact should leave 2 items");
    expect_true(filtered.items[0].aid == "AID.ERROR_SCAN.v1", "filter: first should be error_scan");
    expect_true(filtered.items[1].aid == "AID.FS.READ_FILE.v1", "filter: second should be file_read");
}

static void test_filter_blocked_glob() {
    Menu menu = make_test_menu();
    std::vector<std::string> allowed = {};
    std::vector<std::string> blocked = {"AID.GENESIS.*"};
    Menu filtered = filter_menu_by_capabilities(menu, allowed, blocked);
    expect_true(filtered.items.size() == 3, "filter: blocked glob should leave 3 items");
    for (const auto& mi : filtered.items) {
        expect_true(mi.aid.find("AID.GENESIS.") == std::string::npos, "filter: no genesis items should remain");
    }
}

static void test_filter_allowed_glob() {
    Menu menu = make_test_menu();
    std::vector<std::string> allowed = {"AID.GENESIS.*"};
    std::vector<std::string> blocked = {};
    Menu filtered = filter_menu_by_capabilities(menu, allowed, blocked);
    expect_true(filtered.items.size() == 3, "filter: allowed glob should leave 3 genesis items");
}

static void test_filter_empty_passthrough() {
    Menu menu = make_test_menu();
    std::vector<std::string> allowed = {};
    std::vector<std::string> blocked = {};
    Menu filtered = filter_menu_by_capabilities(menu, allowed, blocked);
    expect_true(filtered.items.size() == menu.items.size(), "filter: empty lists should pass all through");
}

static void test_filter_blocked_takes_priority() {
    Menu menu = make_test_menu();
    std::vector<std::string> allowed = {"AID.GENESIS.*", "AID.ERROR_SCAN.v1"};
    std::vector<std::string> blocked = {"AID.GENESIS.LOAD_PLUGIN.v1"};
    Menu filtered = filter_menu_by_capabilities(menu, allowed, blocked);
    // Should include genesis_write, genesis_compile, error_scan (3 items)
    // genesis_load is blocked
    expect_true(filtered.items.size() == 3, "filter: blocked should override allowed");
    for (const auto& mi : filtered.items) {
        expect_true(mi.aid != "AID.GENESIS.LOAD_PLUGIN.v1", "filter: genesis_load should be blocked");
    }
}

int main() {
    std::cerr << "test_input_safety: safe_merge_patch...\n";
    test_safe_merge_blocks_system_prefix();
    test_safe_merge_blocks_queue_prefix();
    test_safe_merge_blocks_meta_prefix();
    test_safe_merge_empty_patch();
    test_safe_merge_invalid_patch();
    test_safe_merge_allowed_keys_whitelist();
    test_safe_merge_custom_blocked_prefixes();

    std::cerr << "test_input_safety: filter_menu_by_capabilities...\n";
    test_filter_allowed_exact();
    test_filter_blocked_glob();
    test_filter_allowed_glob();
    test_filter_empty_passthrough();
    test_filter_blocked_takes_priority();

    std::cerr << "test_input_safety: ALL PASSED\n";
    return 0;
}
