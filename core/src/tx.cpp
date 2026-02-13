#include "machina/tx.h"
#include "machina/hash.h"
#include "machina/serialization.h"

#include <json-c/json.h>

#include <algorithm>
#include <stdexcept>
#include <string>

namespace machina {

static std::string slot_fingerprint(const Artifact& a) {
    // Stable-ish fingerprint for diff decisions.
    // Note: content_json could be large; include it for correctness (RC profile).
    std::string s = a.type + "|" + a.provenance + "|" + std::to_string(a.size_bytes) + "|" + a.content_json;
    return hash::sha256_hex(s);
}

static std::string compute_patch_json(const DSState& from, const DSState& to) {
    json_object* arr = json_object_new_array();

    auto add_op = [&](const char* op, uint8_t slot, json_object* value_or_null) {
        json_object* o = json_object_new_object();
        json_object_object_add(o, "op", json_object_new_string(op));
        std::string path = std::string("/slots/") + std::to_string(slot);
        json_object_object_add(o, "path", json_object_new_string(path.c_str()));
        if (value_or_null) {
            json_object_object_add(o, "value", value_or_null);
        }
        json_object_array_add(arr, o);
    };

    for (uint8_t slot = 0; slot < 8; slot++) {
        auto it_from = from.slots.find(slot);
        auto it_to = to.slots.find(slot);
        const bool has_from = (it_from != from.slots.end());
        const bool has_to = (it_to != to.slots.end());

        if (!has_from && has_to) {
            add_op("add", slot, artifact_to_json(it_to->second));
        } else if (has_from && !has_to) {
            add_op("remove", slot, nullptr);
        } else if (has_from && has_to) {
            const auto f1 = slot_fingerprint(it_from->second);
            const auto f2 = slot_fingerprint(it_to->second);
            if (f1 != f2) {
                add_op("replace", slot, artifact_to_json(it_to->second));
            }
        }
    }

    std::string out = json_object_to_json_string_ext(arr, JSON_C_TO_STRING_PLAIN);
    json_object_put(arr);
    return out;
}

Tx::Tx(const DSState& base) : base_(base), tmp_(base), active_(true) {}

DSState& Tx::tmp() {
    if (!active_) throw std::runtime_error("tx not active");
    return tmp_;
}

const DSState& Tx::base() const { return base_; }

void Tx::commit(DSState& target) {
    if (!active_) throw std::runtime_error("tx not active");
    patch_json_ = compute_patch_json(base_, tmp_);
    target = tmp_;
    active_ = false;
}

void Tx::rollback() {
    active_ = false;
}

} // namespace machina
