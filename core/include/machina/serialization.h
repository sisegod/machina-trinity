#pragma once

#include "state.h"
#include "types.h"

#include <json-c/json.h>

#include <string>
#include <vector>

namespace machina {

// --- JSON helpers (json-c wrappers) ---

std::string json_quote(const std::string& s);

bool json_get_string(json_object* o, const char* k, std::string* out);
bool json_get_bool(json_object* o, const char* k, bool* out);
std::vector<std::string> json_get_string_array(json_object* o, const char* k);

// --- Artifact serialization ---

json_object* artifact_to_json(const Artifact& a);
bool artifact_from_json(json_object* o, Artifact* out);

// --- DSState serialization ---

json_object* dsstate_to_json(const DSState& ds);
bool dsstate_from_json(json_object* o, DSState* out);

// --- DSState delta serialization ---
// Serializes only slots that differ from `base`. If base is nullptr, behaves
// like dsstate_to_json. The output JSON has the same shape as dsstate_to_json
// but includes an additional "delta":true flag and a "removed_slots" array for
// slots present in base but absent in current.

json_object* dsstate_to_json_delta(const DSState& current, const DSState* base);
bool dsstate_apply_delta(json_object* delta, DSState* state);
bool dsstate_apply_tx_patch(json_object* patch, DSState* state);

// --- StepStatus conversion ---

StepStatus stepstatus_from_str(const std::string& s);
const char* stepstatus_to_str(StepStatus st);

} // namespace machina
