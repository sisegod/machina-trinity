#include "test_common.h"

#include "machina/serialization.h"
#include "machina/state.h"

#include <json-c/json.h>

using machina::Artifact;
using machina::DSState;

static Artifact mk_art(const std::string& content) {
    Artifact a;
    a.type = "text";
    a.provenance = "test";
    a.content_json = content;
    a.size_bytes = content.size();
    return a;
}

int main() {
    DSState s;
    s.slots[0] = mk_art("{\"v\":1}");
    s.slots[6] = mk_art("{\"stage\":\"INIT\"}");

    // add slot 2, replace slot 6, remove slot 0
    const std::string patch_json =
        "["
        "{\"op\":\"add\",\"path\":\"/slots/2\",\"value\":{\"type\":\"text\",\"provenance\":\"t\",\"content_json\":\"{\\\"x\\\":2}\",\"size_bytes\":7}},"
        "{\"op\":\"replace\",\"path\":\"/slots/6\",\"value\":{\"type\":\"text\",\"provenance\":\"t\",\"content_json\":\"{\\\"stage\\\":\\\"DONE\\\"}\",\"size_bytes\":16}},"
        "{\"op\":\"remove\",\"path\":\"/slots/0\"}"
        "]";

    json_object* p = json_tokener_parse(patch_json.c_str());
    expect_true(p != nullptr, "patch parse failed");
    expect_true(machina::dsstate_apply_tx_patch(p, &s), "dsstate_apply_tx_patch should succeed");
    json_object_put(p);

    expect_true(s.slots.find(0) == s.slots.end(), "slot 0 should be removed");
    expect_true(s.slots.find(2) != s.slots.end(), "slot 2 should be added");
    expect_true(s.slots.find(6) != s.slots.end(), "slot 6 should exist");
    expect_true(s.slots[2].content_json == "{\"x\":2}", "slot 2 content mismatch");
    expect_true(s.slots[6].content_json == "{\"stage\":\"DONE\"}", "slot 6 content mismatch");

    // invalid op should fail and not crash
    json_object* bad = json_tokener_parse("[{\"op\":\"move\",\"path\":\"/slots/1\"}]");
    expect_true(bad != nullptr, "bad patch parse failed");
    expect_true(!machina::dsstate_apply_tx_patch(bad, &s), "invalid op should fail");
    json_object_put(bad);

    return 0;
}
