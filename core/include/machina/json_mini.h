#pragma once

// json_mini.h (Release 3)
//
// Production JSON helper using json-c underneath.
// Same API surface as RC1 but backed by a well-tested C library.

#include <json-c/json.h>

#include <climits>
#include <cstdint>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

namespace machina::json_mini {

struct Doc {
    json_object* root{nullptr};

    Doc() = default;
    explicit Doc(json_object* r) : root(r) {}
    Doc(const Doc&) = delete;
    Doc& operator=(const Doc&) = delete;

    Doc(Doc&& other) noexcept : root(other.root) { other.root = nullptr; }
    Doc& operator=(Doc&& other) noexcept {
        if (this != &other) {
            if (root) json_object_put(root);
            root = other.root;
            other.root = nullptr;
        }
        return *this;
    }

    ~Doc() {
        if (root) json_object_put(root);
    }

    explicit operator bool() const { return root != nullptr; }
};

inline Doc parse(const std::string& json) {
    json_tokener* tok = json_tokener_new();
    if (!tok) return Doc{};
    json_object* obj = json_tokener_parse_ex(tok, json.c_str(),
        static_cast<int>(std::min(json.size(), static_cast<size_t>(INT_MAX))));
    json_tokener_error jerr = json_tokener_get_error(tok);
    json_tokener_free(tok);
    if (jerr != json_tokener_success) {
        if (obj) json_object_put(obj);
        return Doc{};
    }
    return Doc{obj};
}

inline bool has_key(const std::string& json, const std::string& key) {
    Doc d = parse(json);
    if (!d) return false;
    if (!json_object_is_type(d.root, json_type_object)) return false;
    json_object* v = nullptr;
    return json_object_object_get_ex(d.root, key.c_str(), &v);
}

inline std::optional<std::string> get_string(const std::string& json, const std::string& key) {
    Doc d = parse(json);
    if (!d) return std::nullopt;
    if (!json_object_is_type(d.root, json_type_object)) return std::nullopt;
    json_object* v = nullptr;
    if (!json_object_object_get_ex(d.root, key.c_str(), &v)) return std::nullopt;
    if (!json_object_is_type(v, json_type_string)) return std::nullopt;
    return std::string(json_object_get_string(v));
}

inline std::optional<int64_t> get_int(const std::string& json, const std::string& key) {
    Doc d = parse(json);
    if (!d) return std::nullopt;
    if (!json_object_is_type(d.root, json_type_object)) return std::nullopt;
    json_object* v = nullptr;
    if (!json_object_object_get_ex(d.root, key.c_str(), &v)) return std::nullopt;
    if (!json_object_is_type(v, json_type_int)) return std::nullopt;
    return static_cast<int64_t>(json_object_get_int64(v));
}

inline std::optional<double> get_double(const std::string& json, const std::string& key) {
    Doc d = parse(json);
    if (!d) return std::nullopt;
    if (!json_object_is_type(d.root, json_type_object)) return std::nullopt;
    json_object* v = nullptr;
    if (!json_object_object_get_ex(d.root, key.c_str(), &v)) return std::nullopt;
    if (!v) return std::nullopt;
    if (!(json_object_is_type(v, json_type_double) || json_object_is_type(v, json_type_int))) return std::nullopt;
    return json_object_get_double(v);
}

inline std::optional<bool> get_bool(const std::string& json, const std::string& key) {
    Doc d = parse(json);
    if (!d) return std::nullopt;
    if (!json_object_is_type(d.root, json_type_object)) return std::nullopt;
    json_object* v = nullptr;
    if (!json_object_object_get_ex(d.root, key.c_str(), &v)) return std::nullopt;
    if (!json_object_is_type(v, json_type_boolean)) return std::nullopt;
    return json_object_get_boolean(v) != 0;
}

inline std::vector<std::string> get_array_strings(const std::string& json, const std::string& key) {
    std::vector<std::string> out;
    Doc d = parse(json);
    if (!d) return out;
    if (!json_object_is_type(d.root, json_type_object)) return out;
    json_object* arr = nullptr;
    if (!json_object_object_get_ex(d.root, key.c_str(), &arr)) return out;
    if (!json_object_is_type(arr, json_type_array)) return out;
    const size_t n = json_object_array_length(arr);
    out.reserve(n);
    for (size_t i = 0; i < n; i++) {
        json_object* el = json_object_array_get_idx(arr, static_cast<int>(i));
        if (el && json_object_is_type(el, json_type_string)) {
            out.emplace_back(json_object_get_string(el));
        }
    }
    return out;
}

inline std::optional<std::string> get_object_raw(const std::string& json, const std::string& key) {
    Doc d = parse(json);
    if (!d) return std::nullopt;
    if (!json_object_is_type(d.root, json_type_object)) return std::nullopt;
    json_object* v = nullptr;
    if (!json_object_object_get_ex(d.root, key.c_str(), &v)) return std::nullopt;
    if (!v) return std::nullopt;
    return std::string(json_object_to_json_string_ext(v, JSON_C_TO_STRING_PLAIN));
}

inline std::optional<std::string> get_array_raw(const std::string& json, const std::string& key) {
    Doc d = parse(json);
    if (!d) return std::nullopt;
    if (!json_object_is_type(d.root, json_type_object)) return std::nullopt;
    json_object* v = nullptr;
    if (!json_object_object_get_ex(d.root, key.c_str(), &v)) return std::nullopt;
    if (!json_object_is_type(v, json_type_array)) return std::nullopt;
    return std::string(json_object_to_json_string_ext(v, JSON_C_TO_STRING_PLAIN));
}

inline std::vector<std::string> parse_array_objects_raw(const std::string& array_json_raw) {
    std::vector<std::string> out;
    Doc d = parse(array_json_raw);
    if (!d) return out;
    if (!json_object_is_type(d.root, json_type_array)) return out;
    const size_t n = json_object_array_length(d.root);
    out.reserve(n);
    for (size_t i = 0; i < n; i++) {
        json_object* el = json_object_array_get_idx(d.root, static_cast<int>(i));
        if (el && json_object_is_type(el, json_type_object)) {
            out.emplace_back(json_object_to_json_string_ext(el, JSON_C_TO_STRING_PLAIN));
        }
    }
    return out;
}

// Parse a JSON array (raw string) into numeric values. Non-numbers are skipped.
inline std::vector<double> parse_array_numbers(const std::string& array_json_raw) {
    std::vector<double> out;
    Doc d = parse(array_json_raw);
    if (!d) return out;
    if (!json_object_is_type(d.root, json_type_array)) return out;
    const size_t n = json_object_array_length(d.root);
    out.reserve(n);
    for (size_t i = 0; i < n; i++) {
        json_object* el = json_object_array_get_idx(d.root, static_cast<int>(i));
        if (!el) continue;
        if (json_object_is_type(el, json_type_double) || json_object_is_type(el, json_type_int)) {
            out.emplace_back(json_object_get_double(el));
        }
    }
    return out;
}

// Escape a string for embedding inside a JSON string literal (no surrounding quotes).
inline std::string json_escape(const std::string& s) {
    std::ostringstream oss;
    for (char c : s) {
        switch (c) {
            case '\\': oss << "\\\\"; break;
            case '"':  oss << "\\\""; break;
            case '\b': oss << "\\b"; break;
            case '\f': oss << "\\f"; break;
            case '\n': oss << "\\n"; break;
            case '\r': oss << "\\r"; break;
            case '\t': oss << "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", (unsigned)(unsigned char)c);
                    oss << buf;
                } else {
                    oss << c;
                }
                break;
        }
    }
    return oss.str();
}

} // namespace machina::json_mini
