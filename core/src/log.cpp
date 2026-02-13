#include "machina/log.h"
#include "machina/hash.h"

#include <json-c/json.h>

#include <algorithm>
#include <chrono>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

namespace machina {

static std::string iso_now() {
    using namespace std::chrono;
    auto now = system_clock::now();
    std::time_t t = system_clock::to_time_t(now);
    std::tm tm{};
#ifdef _WIN32
    gmtime_s(&tm, &t);
#else
    gmtime_r(&t, &tm);
#endif
    std::ostringstream oss;
    oss << std::put_time(&tm, "%Y-%m-%dT%H:%M:%SZ");
    return oss.str();
}

// Recursively serialize JSON with sorted keys (RFC 8785 JCS subset).
// Produces deterministic output for tamper-evident hash chains.
static void canonical_serialize(json_object* obj, std::ostringstream& out) {
    if (!obj) { out << "null"; return; }

    switch (json_object_get_type(obj)) {
    case json_type_object: {
        // Collect keys, sort, emit in order
        std::vector<std::string> keys;
        json_object_object_foreach(obj, k, v) {
            (void)v;
            keys.emplace_back(k);
        }
        std::sort(keys.begin(), keys.end());

        out << "{";
        for (size_t i = 0; i < keys.size(); i++) {
            if (i > 0) out << ",";
            // Key: escape via json-c
            json_object* ks = json_object_new_string(keys[i].c_str());
            out << json_object_to_json_string(ks);
            json_object_put(ks);
            out << ":";
            json_object* val = nullptr;
            json_object_object_get_ex(obj, keys[i].c_str(), &val);
            canonical_serialize(val, out);
        }
        out << "}";
        break;
    }
    case json_type_array: {
        out << "[";
        int len = json_object_array_length(obj);
        for (int i = 0; i < len; i++) {
            if (i > 0) out << ",";
            canonical_serialize(json_object_array_get_idx(obj, i), out);
        }
        out << "]";
        break;
    }
    default:
        // Primitives: json-c serialization is already canonical for
        // strings (proper escaping), numbers, booleans, and null.
        out << json_object_to_json_string_ext(obj, JSON_C_TO_STRING_PLAIN);
        break;
    }
}

// Canonical JSON: parse then re-serialize with sorted keys.
// Returns input unchanged if parsing fails (best-effort).
static std::string canonicalize_json(const std::string& raw) {
    json_object* obj = json_tokener_parse(raw.c_str());
    if (!obj) return raw;
    std::ostringstream out;
    canonical_serialize(obj, out);
    json_object_put(obj);
    return out.str();
}

JsonlLogger::JsonlLogger(const RunHeader& hdr, const std::string& path)
    : hdr_(hdr), path_(path), out_(path, std::ios::out | std::ios::trunc), chain_prev_(std::string(64, '0')) {}

void JsonlLogger::event(int step, const std::string& name, const std::string& payload_json) {
    std::string ts = iso_now();

    // Canonicalize payload for deterministic hashing
    std::string canonical_payload = canonicalize_json(payload_json);

    // Build record as json-c object, then canonicalize for hash chain
    json_object* rec = json_object_new_object();
    json_object_object_add(rec, "event", json_object_new_string(name.c_str()));

    json_object* pobj = json_tokener_parse(canonical_payload.c_str());
    json_object_object_add(rec, "payload", pobj ? pobj : json_object_new_string(canonical_payload.c_str()));

    json_object_object_add(rec, "profile_id", json_object_new_string(hdr_.profile_id.c_str()));
    if (!hdr_.request_id.empty())
        json_object_object_add(rec, "request_id", json_object_new_string(hdr_.request_id.c_str()));
    json_object_object_add(rec, "run_id", json_object_new_string(hdr_.run_id.c_str()));
    json_object_object_add(rec, "spec_version", json_object_new_string(hdr_.spec_version.c_str()));
    json_object_object_add(rec, "step", json_object_new_int(step));
    json_object_object_add(rec, "ts", json_object_new_string(ts.c_str()));

    // Canonical serialization for deterministic hash
    std::ostringstream rec_out;
    canonical_serialize(rec, rec_out);
    std::string record = rec_out.str();
    json_object_put(rec);

    // Tamper-evident hash chain: chain_hash = SHA256(chain_prev || record)
    std::string chain_hash = machina::hash::sha256_hex(chain_prev_ + record);

    // Final output line: chain fields + canonical record fields
    json_object* line = json_object_new_object();
    json_object_object_add(line, "chain_hash", json_object_new_string(chain_hash.c_str()));
    json_object_object_add(line, "chain_prev", json_object_new_string(chain_prev_.c_str()));
    json_object_object_add(line, "event", json_object_new_string(name.c_str()));

    json_object* pobj2 = json_tokener_parse(canonical_payload.c_str());
    json_object_object_add(line, "payload", pobj2 ? pobj2 : json_object_new_string(canonical_payload.c_str()));

    json_object_object_add(line, "profile_id", json_object_new_string(hdr_.profile_id.c_str()));
    if (!hdr_.request_id.empty())
        json_object_object_add(line, "request_id", json_object_new_string(hdr_.request_id.c_str()));
    json_object_object_add(line, "run_id", json_object_new_string(hdr_.run_id.c_str()));
    json_object_object_add(line, "spec_version", json_object_new_string(hdr_.spec_version.c_str()));
    json_object_object_add(line, "step", json_object_new_int(step));
    json_object_object_add(line, "ts", json_object_new_string(ts.c_str()));

    // Output line also canonical
    std::ostringstream line_out;
    canonical_serialize(line, line_out);
    out_ << line_out.str() << "\n";
    out_.flush();

    json_object_put(line);
    chain_prev_ = chain_hash;
}

} // namespace machina
