#include "machina/serialization.h"

#include <sstream>
#include <stdexcept>

namespace machina {

// --- JSON helpers ---

std::string json_quote(const std::string& s) {
    json_object* o = json_object_new_string_len(s.c_str(), (int)s.size());
    if (!o) return "\"\"";
    std::string out = json_object_to_json_string_ext(o, JSON_C_TO_STRING_PLAIN);
    json_object_put(o);
    return out;
}

bool json_get_string(json_object* o, const char* k, std::string* out) {
    if (!o || !out) return false;
    json_object* v = nullptr;
    if (!json_object_object_get_ex(o, k, &v) || !v || !json_object_is_type(v, json_type_string)) return false;
    *out = json_object_get_string(v);
    return true;
}

bool json_get_bool(json_object* o, const char* k, bool* out) {
    if (!o || !out) return false;
    json_object* v = nullptr;
    if (!json_object_object_get_ex(o, k, &v) || !v) return false;
    if (json_object_is_type(v, json_type_boolean)) { *out = (json_object_get_boolean(v) != 0); return true; }
    return false;
}

std::vector<std::string> json_get_string_array(json_object* o, const char* k) {
    std::vector<std::string> out;
    json_object* v = nullptr;
    if (!o || !json_object_object_get_ex(o, k, &v) || !v || !json_object_is_type(v, json_type_array)) return out;
    const int n = json_object_array_length(v);
    out.reserve((size_t)n);
    for (int i = 0; i < n; i++) {
        json_object* it = json_object_array_get_idx(v, i);
        if (it && json_object_is_type(it, json_type_string)) out.push_back(json_object_get_string(it));
    }
    return out;
}

// --- Artifact serialization ---

json_object* artifact_to_json(const Artifact& a) {
    json_object* o = json_object_new_object();
    json_object_object_add(o, "type", json_object_new_string(a.type.c_str()));
    json_object_object_add(o, "provenance", json_object_new_string(a.provenance.c_str()));
    json_object_object_add(o, "content_json", json_object_new_string_len(a.content_json.c_str(), (int)a.content_json.size()));
    json_object_object_add(o, "size_bytes", json_object_new_int64((int64_t)a.size_bytes));
    return o;
}

bool artifact_from_json(json_object* o, Artifact* out) {
    if (!o || !json_object_is_type(o, json_type_object) || !out) return false;
    Artifact a;
    json_object* v = nullptr;
    if (json_object_object_get_ex(o, "type", &v) && json_object_is_type(v, json_type_string)) a.type = json_object_get_string(v);
    if (json_object_object_get_ex(o, "provenance", &v) && json_object_is_type(v, json_type_string)) a.provenance = json_object_get_string(v);
    if (json_object_object_get_ex(o, "content_json", &v) && json_object_is_type(v, json_type_string)) a.content_json = json_object_get_string(v);
    if (json_object_object_get_ex(o, "size_bytes", &v) && (json_object_is_type(v, json_type_int) || json_object_is_type(v, json_type_double))) a.size_bytes = (size_t)json_object_get_int64(v);
    *out = std::move(a);
    return true;
}

// --- DSState serialization ---

json_object* dsstate_to_json(const DSState& ds) {
    json_object* root = json_object_new_object();
    json_object* slots = json_object_new_object();
    for (const auto& kv : ds.slots) {
        std::string key = std::to_string((int)kv.first);
        json_object_object_add(slots, key.c_str(), artifact_to_json(kv.second));
    }
    json_object_object_add(root, "slots", slots);
    return root;
}

bool dsstate_from_json(json_object* o, DSState* out) {
    if (!out) return false;
    DSState ds;
    if (!o || !json_object_is_type(o, json_type_object)) {
        *out = ds;
        return true;
    }
    json_object* slots = nullptr;
    if (!json_object_object_get_ex(o, "slots", &slots) || !slots || !json_object_is_type(slots, json_type_object)) {
        *out = ds;
        return true;
    }
    json_object_object_foreach(slots, key, val) {
        if (!key || !val) continue;
        int k = 0;
        try { k = std::stoi(key); } catch (...) {
            continue;
        }
        if (k < 0 || k > 255) continue;
        Artifact a;
        if (!artifact_from_json(val, &a)) continue;
        ds.slots[(uint8_t)k] = std::move(a);
    }
    *out = std::move(ds);
    return true;
}

// --- DSState delta serialization ---

json_object* dsstate_to_json_delta(const DSState& current, const DSState* base) {
    if (!base) return dsstate_to_json(current);

    json_object* root = json_object_new_object();
    json_object_object_add(root, "delta", json_object_new_boolean(1));

    json_object* slots = json_object_new_object();
    // Emit slots that are new or changed
    for (const auto& kv : current.slots) {
        auto it = base->slots.find(kv.first);
        bool changed = (it == base->slots.end());
        if (!changed) {
            // Compare content to detect changes
            const Artifact& ba = it->second;
            const Artifact& ca = kv.second;
            changed = (ca.type != ba.type ||
                       ca.provenance != ba.provenance ||
                       ca.content_json != ba.content_json ||
                       ca.size_bytes != ba.size_bytes);
        }
        if (changed) {
            std::string key = std::to_string((int)kv.first);
            json_object_object_add(slots, key.c_str(), artifact_to_json(kv.second));
        }
    }
    json_object_object_add(root, "slots", slots);

    // Emit removed slots
    json_object* removed = json_object_new_array();
    for (const auto& kv : base->slots) {
        if (current.slots.find(kv.first) == current.slots.end()) {
            json_object_array_add(removed, json_object_new_int((int)kv.first));
        }
    }
    json_object_object_add(root, "removed_slots", removed);

    return root;
}

bool dsstate_apply_delta(json_object* delta, DSState* state) {
    if (!state || !delta || !json_object_is_type(delta, json_type_object)) return false;

    // Check if this is actually a delta or a full state
    json_object* is_delta = nullptr;
    bool delta_mode = false;
    if (json_object_object_get_ex(delta, "delta", &is_delta) && is_delta) {
        delta_mode = json_object_get_boolean(is_delta) != 0;
    }

    if (!delta_mode) {
        // Full state replacement
        return dsstate_from_json(delta, state);
    }

    // Apply changed/new slots
    json_object* slots = nullptr;
    if (json_object_object_get_ex(delta, "slots", &slots) && slots && json_object_is_type(slots, json_type_object)) {
        json_object_object_foreach(slots, key, val) {
            if (!key || !val) continue;
            int k = 0;
            try { k = std::stoi(key); } catch (...) { continue; }
            if (k < 0 || k > 255) continue;
            Artifact a;
            if (!artifact_from_json(val, &a)) continue;
            state->slots[(uint8_t)k] = std::move(a);
        }
    }

    // Remove deleted slots
    json_object* removed = nullptr;
    if (json_object_object_get_ex(delta, "removed_slots", &removed) && removed && json_object_is_type(removed, json_type_array)) {
        const int n = json_object_array_length(removed);
        for (int i = 0; i < n; i++) {
            json_object* it = json_object_array_get_idx(removed, i);
            if (!it) continue;
            int k = json_object_get_int(it);
            if (k >= 0 && k <= 255) {
                state->slots.erase((uint8_t)k);
            }
        }
    }

    return true;
}

bool dsstate_apply_tx_patch(json_object* patch, DSState* state) {
    if (!state || !patch || !json_object_is_type(patch, json_type_array)) return false;

    const size_t n = json_object_array_length(patch);
    for (size_t i = 0; i < n; i++) {
        json_object* op_obj = json_object_array_get_idx(patch, i);
        if (!op_obj || !json_object_is_type(op_obj, json_type_object)) return false;

        json_object* op_j = nullptr;
        json_object* path_j = nullptr;
        if (!json_object_object_get_ex(op_obj, "op", &op_j) || !json_object_is_type(op_j, json_type_string)) return false;
        if (!json_object_object_get_ex(op_obj, "path", &path_j) || !json_object_is_type(path_j, json_type_string)) return false;

        std::string op = json_object_get_string(op_j);
        std::string path = json_object_get_string(path_j);
        constexpr const char* kPrefix = "/slots/";
        if (path.rfind(kPrefix, 0) != 0) return false;

        int slot = -1;
        try {
            slot = std::stoi(path.substr(7));
        } catch (...) {
            return false;
        }
        if (slot < 0 || slot > 7) return false;
        const auto k = static_cast<uint8_t>(slot);

        if (op == "remove") {
            state->slots.erase(k);
            continue;
        }

        if (op == "add" || op == "replace") {
            json_object* value_j = nullptr;
            if (!json_object_object_get_ex(op_obj, "value", &value_j) || !value_j || !json_object_is_type(value_j, json_type_object)) {
                return false;
            }
            Artifact a;
            if (!artifact_from_json(value_j, &a)) return false;
            state->slots[k] = std::move(a);
            continue;
        }

        return false;
    }
    return true;
}

// --- StepStatus conversion ---

StepStatus stepstatus_from_str(const std::string& s) {
    if (s == "OK") return StepStatus::OK;
    if (s == "INVALID_PICK") return StepStatus::INVALID_PICK;
    if (s == "BREAKER_TRIP") return StepStatus::BREAKER_TRIP;
    return StepStatus::TOOL_ERROR;
}

const char* stepstatus_to_str(StepStatus st) {
    switch (st) {
        case StepStatus::OK: return "OK";
        case StepStatus::INVALID_PICK: return "INVALID_PICK";
        case StepStatus::BREAKER_TRIP: return "BREAKER_TRIP";
        default: return "TOOL_ERROR";
    }
}

} // namespace machina
