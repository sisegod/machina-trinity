#include "test_common.h"
#include "machina/goal_registry.h"
#include "machina/state.h"

#include <filesystem>
#include <iostream>

using namespace machina;

static void test_and_logic() {
    GoalRegistry reg;

    GoalDesc d;
    d.goal_id = "goal.TEST_AND.v1";
    d.required_slots = {0, 2};
    d.any_slot_sufficient = false;
    reg.registerGoal(d);

    DSState state;

    // Empty state: not complete
    expect_true(!reg.isGoalComplete("goal.TEST_AND.v1", state), "AND: empty state should not be complete");

    // Only DS0
    Artifact a; a.type = "test"; a.content_json = "{}"; a.provenance = "test"; a.size_bytes = 2;
    state.slots[0] = a;
    expect_true(!reg.isGoalComplete("goal.TEST_AND.v1", state), "AND: DS0 only should not be complete");

    // DS0 + DS2
    state.slots[2] = a;
    expect_true(reg.isGoalComplete("goal.TEST_AND.v1", state), "AND: DS0+DS2 should be complete");
}

static void test_or_logic() {
    GoalRegistry reg;

    GoalDesc d;
    d.goal_id = "goal.TEST_OR.v1";
    d.required_slots = {0, 2};
    d.any_slot_sufficient = true;
    reg.registerGoal(d);

    DSState state;

    expect_true(!reg.isGoalComplete("goal.TEST_OR.v1", state), "OR: empty state should not be complete");

    Artifact a; a.type = "test"; a.content_json = "{}"; a.provenance = "test"; a.size_bytes = 2;
    state.slots[0] = a;
    expect_true(reg.isGoalComplete("goal.TEST_OR.v1", state), "OR: DS0 alone should be complete");
}

static void test_prefix_matching() {
    GoalRegistry reg;

    GoalDesc d;
    d.goal_id = "goal.GENESIS";
    d.required_slots = {0, 7};
    d.any_slot_sufficient = false;
    reg.registerGoal(d);

    DSState state;
    Artifact a; a.type = "test"; a.content_json = "{}"; a.provenance = "test"; a.size_bytes = 2;
    state.slots[0] = a;
    state.slots[7] = a;

    // Exact match
    expect_true(reg.isGoalComplete("goal.GENESIS", state), "prefix: exact match should work");

    // Prefix match
    expect_true(reg.isGoalComplete("goal.GENESIS_DEMO_HELLO.v1", state), "prefix: should match goal.GENESIS prefix");

    // No match
    expect_true(!reg.isGoalComplete("goal.UNKNOWN.v1", state), "prefix: unknown goal should not match");
}

static void test_manifest_loading() {
    // Check if the goalpack manifests exist and can be loaded
    GoalRegistry reg;

    // Try loading the error_scan manifest relative to CWD or MACHINA_ROOT
    std::filesystem::path root;
    if (const char* e = std::getenv("MACHINA_ROOT")) {
        root = e;
    } else {
        // Try to find root from CWD
        root = std::filesystem::current_path();
        for (int i = 0; i < 8; i++) {
            if (std::filesystem::exists(root / "goalpacks")) break;
            if (!root.has_parent_path()) break;
            root = root.parent_path();
        }
    }

    auto error_scan = root / "goalpacks" / "error_scan" / "manifest.json";
    auto gpu_smoke = root / "goalpacks" / "gpu_smoke" / "manifest.json";

    if (std::filesystem::exists(error_scan)) {
        reg.loadGoalPackManifest(error_scan.string());
        const GoalDesc* gd = reg.getGoal("goal.ERROR_SCAN.v1");
        expect_true(gd != nullptr, "manifest: error_scan goal should be loaded");
        if (gd) {
            expect_true(gd->required_slots.size() == 1, "manifest: error_scan should have 1 completion slot");
            expect_true(gd->required_slots[0] == 0, "manifest: error_scan completion slot should be DS0");
        }
    } else {
        std::cerr << "[skip] error_scan manifest not found at " << error_scan << "\n";
    }

    if (std::filesystem::exists(gpu_smoke)) {
        reg.loadGoalPackManifest(gpu_smoke.string());
        const GoalDesc* gd = reg.getGoal("goal.GPU_SMOKE.v1");
        expect_true(gd != nullptr, "manifest: gpu_smoke goal should be loaded");
        if (gd) {
            expect_true(gd->required_slots.size() == 1, "manifest: gpu_smoke should have 1 completion slot");
            expect_true(gd->required_slots[0] == 0, "manifest: gpu_smoke completion slot should be DS0");
        }
    } else {
        std::cerr << "[skip] gpu_smoke manifest not found at " << gpu_smoke << "\n";
    }
}

static void test_duplicate_registration() {
    GoalRegistry reg;

    GoalDesc d;
    d.goal_id = "goal.DUP.v1";
    d.required_slots = {0};
    reg.registerGoal(d);

    // Duplicate without override should throw
    bool threw = false;
    try {
        reg.registerGoal(d, false);
    } catch (...) {
        threw = true;
    }
    expect_true(threw, "duplicate: should throw without allow_override");

    // With override should succeed
    d.required_slots = {1};
    reg.registerGoal(d, true);
    const GoalDesc* gd = reg.getGoal("goal.DUP.v1");
    expect_true(gd != nullptr, "duplicate: should find after override");
    if (gd) {
        expect_true(gd->required_slots.size() == 1 && gd->required_slots[0] == 1,
                     "duplicate: overridden slot should be DS1");
    }
}

static void test_empty_slots() {
    GoalRegistry reg;

    GoalDesc d;
    d.goal_id = "goal.EMPTY.v1";
    // No required_slots — should never be complete
    reg.registerGoal(d);

    DSState state;
    Artifact a; a.type = "test"; a.content_json = "{}"; a.provenance = "test"; a.size_bytes = 2;
    state.slots[0] = a;
    state.slots[1] = a;
    state.slots[2] = a;

    expect_true(!reg.isGoalComplete("goal.EMPTY.v1", state), "empty slots: should never be complete");
}

int main() {
    test_and_logic();
    test_or_logic();
    test_prefix_matching();
    test_manifest_loading();
    test_duplicate_registration();
    test_empty_slots();

    std::cout << "ALL GOAL_REGISTRY TESTS PASSED\n";
    return 0;
}
