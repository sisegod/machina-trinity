#include "test_common.h"

#include "machina/tx.h"

#include <json-c/json.h>

using machina::Artifact;
using machina::DSState;
using machina::Tx;

static int count_ops(const std::string& patch, const std::string& op) {
    json_object* root = json_tokener_parse(patch.c_str());
    if (!root || !json_object_is_type(root, json_type_array)) {
        if (root) json_object_put(root);
        return -1;
    }
    int n = json_object_array_length(root);
    int cnt = 0;
    for (int i = 0; i < n; i++) {
        json_object* o = json_object_array_get_idx(root, i);
        json_object* v = nullptr;
        if (json_object_object_get_ex(o, "op", &v)) {
            const char* s = json_object_get_string(v);
            if (s && op == s) cnt++;
        }
    }
    json_object_put(root);
    return cnt;
}

int main() {
    DSState base;
    Artifact a0;
    a0.type = "text";
    a0.provenance = "base";
    a0.content_json = "{\"t\":\"hello\"}";
    a0.size_bytes = a0.content_json.size();
    base.slots[0] = a0;

    Tx tx(base);
    // Replace slot0
    tx.tmp().slots[0].content_json = "{\"t\":\"world\"}";
    tx.tmp().slots[0].size_bytes = tx.tmp().slots[0].content_json.size();

    // Add slot1
    Artifact a1;
    a1.type = "text";
    a1.provenance = "new";
    a1.content_json = "{\"x\":1}";
    a1.size_bytes = a1.content_json.size();
    tx.tmp().slots[1] = a1;

    DSState target;
    tx.commit(target);

    const std::string& patch = tx.patch_json();
    int adds = count_ops(patch, "add");
    int repl = count_ops(patch, "replace");
    expect_true(adds >= 1, "patch should contain add op");
    expect_true(repl >= 1, "patch should contain replace op");

    // Target state should match tmp
    expect_true(target.slots.count(0) == 1, "target should have slot0");
    expect_true(target.slots.count(1) == 1, "target should have slot1");
    expect_true(target.slots.at(0).content_json.find("world") != std::string::npos, "slot0 should be updated");

    return 0;
}
