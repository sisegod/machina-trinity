#include "machina/selector.h"
#include "machina/json_mini.h"
#include <json-c/json.h>
#include <regex>
#include <algorithm>

namespace machina {

Selection parse_selector_output(const std::string& s) {
    Selection out;
    out.raw = s;

    // Allowed:
    // <PICK><SID0007><END>
    // <PICK><SID0007><INP>{...}</INP><END>
    // <PICK><SID0007><INP64>BASE64(JSON)</INP64><END>
    // <ASK_SUP><END>
    // <NOOP><END>

    auto trim = [](std::string x) {
        while (!x.empty() && (x.back()=='\n' || x.back()=='\r' || x.back()==' ' || x.back()=='\t')) x.pop_back();
        size_t i=0;
        while (i<x.size() && (x[i]=='\n' || x[i]=='\r' || x[i]==' ' || x[i]=='\t')) i++;
        if (i) x.erase(0,i);
        return x;
    };

    const std::string t = trim(s);
    if (t == "<ASK_SUP><END>") { out.kind = Selection::Kind::ASK_SUP; return out; }
    if (t == "<NOOP><END>") { out.kind = Selection::Kind::NOOP; return out; }

    auto b64dec = [](const std::string& b64) -> std::optional<std::string> {
        static const int8_t T[256] = {
            -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
            -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
            -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,62,-1,-1,-1,63,
            52,53,54,55,56,57,58,59,60,61,-1,-1,-1,-2,-1,-1,
            -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9,10,11,12,13,14,
            15,16,17,18,19,20,21,22,23,24,25,-1,-1,-1,-1,-1,
            -1,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,
            41,42,43,44,45,46,47,48,49,50,51,-1,-1,-1,-1,-1,
            -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
            -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
            -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
            -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
            -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
            -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
            -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
            -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1
        };

        std::string in;
        in.reserve(b64.size());
        for (char c : b64) {
            if (c=='\n' || c=='\r' || c==' ' || c=='\t') continue;
            in.push_back(c);
        }
        if (in.empty()) return std::nullopt;

        std::string out;
        out.reserve((in.size()*3)/4);
        int val=0, valb=-8;
        for (unsigned char c : in) {
            int8_t d = T[c];
            if (d == -1) {
                if (c=='=') break;
                return std::nullopt;
            }
            if (d == -2) continue;
            val = (val<<6) + d;
            valb += 6;
            if (valb >= 0) {
                out.push_back(char((val>>valb)&0xFF));
                valb -= 8;
            }
        }
        return out;
    };

    // PICK cases
    const std::string pick_prefix = "<PICK><";
    if (t.rfind(pick_prefix, 0) == 0) {
        // Expect: <PICK><SIDdddd>...
        auto sid_end = t.find('>', pick_prefix.size());
        if (sid_end == std::string::npos) { out.kind = Selection::Kind::INVALID; return out; }
        std::string sid_tok = t.substr(pick_prefix.size(), sid_end - pick_prefix.size());
        auto sid = SID::parse(sid_tok);
        if (!sid) { out.kind = Selection::Kind::INVALID; return out; }

        // Must end with <END>
        if (t.size() < 5 || t.substr(t.size()-5) != "<END>") { out.kind = Selection::Kind::INVALID; return out; }

        // Optional patches
        const std::string inp64_open = "<INP64>";
        const std::string inp64_close = "</INP64>";
        const std::string inp_open = "<INP>";
        const std::string inp_close = "</INP>";

        std::optional<std::string> patch;
        auto p64 = t.find(inp64_open);
        if (p64 != std::string::npos) {
            auto q = t.find(inp64_close, p64 + inp64_open.size());
            if (q == std::string::npos) { out.kind = Selection::Kind::INVALID; return out; }
            auto b64 = t.substr(p64 + inp64_open.size(), q - (p64 + inp64_open.size()));
            auto dec = b64dec(b64);
            if (!dec) { out.kind = Selection::Kind::INVALID; return out; }
            patch = *dec;
        } else {
            auto p0 = t.find(inp_open);
            if (p0 != std::string::npos) {
                auto q = t.find(inp_close, p0 + inp_open.size());
                if (q == std::string::npos) { out.kind = Selection::Kind::INVALID; return out; }
                patch = t.substr(p0 + inp_open.size(), q - (p0 + inp_open.size()));
            }
        }

        // Validate patch if present (must be JSON object)
        if (patch) {
            auto doc = machina::json_mini::parse(*patch);
            if (!doc || !json_object_is_type(doc.root, json_type_object)) {
                out.kind = Selection::Kind::INVALID;
                return out;
            }
            out.input_patch_json = *patch;
        }

        out.kind = Selection::Kind::PICK;
        out.sid = *sid;
        return out;
    }

    out.kind = Selection::Kind::INVALID;
    return out;
}

static std::string sid4(uint16_t sid) {
    std::string z = (sid < 10 ? "000" : sid < 100 ? "00" : sid < 1000 ? "0" : "");
    return z + std::to_string(sid);
}

static bool menu_has_tag(const Menu& menu, const std::string& tag) {
    for (const auto& it : menu.items) {
        if (std::find(it.tags.begin(), it.tags.end(), tag) != it.tags.end()) return true;
    }
    return false;
}

static const MenuItem* first_with_tag(const Menu& menu, const std::string& tag, bool skip_noop=true) {
    const MenuItem* best = nullptr;
    for (const auto& it : menu.items) {
        if (skip_noop && it.aid == "AID.NOOP.v1") continue;
        if (std::find(it.tags.begin(), it.tags.end(), tag) == it.tags.end()) continue;
        if (!best || it.sid.value < best->sid.value) best = &it;
    }
    return best;
}


static const MenuItem* first_with_aid(const Menu& menu, const std::string& aid, bool skip_noop=true) {
    for (const auto& it : menu.items) {
        if (skip_noop && it.aid == "AID.NOOP.v1") continue;
        if (it.aid == aid) return &it;
    }
    return nullptr;
}

static std::string extract_flag_value(const std::string& goal_digest, const std::string& key) {
    // goal_digest format: "<goal_id>|<menu_digest>|FLAGS:...;KEY=VALUE;..."
    auto p = goal_digest.find(key + "=");
    if (p == std::string::npos) return "";
    p += key.size() + 1;
    auto e = goal_digest.find(';', p);
    if (e == std::string::npos) e = goal_digest.size();
    return goal_digest.substr(p, e - p);
}

Selection HeuristicSelector::select(const Menu& menu,
                                   const std::string& goal_digest,
                                   const std::string& /*state_digest*/,
                                   ControlMode /*mode*/,
                                   const std::string& /*inputs_json*/) {
    // Deterministic tier0 rules (Profile A / Release 2):
    // RC2+ adds a minimal state-aware handoff between tools (multi-step MVP):
    // - If DS0 exists and DS2 does not, prefer a report/summarization tool.
    // 1) If the menu contains an error-focused tool, prefer it when tag.error is present.
    // 2) If the menu contains a GPU probe tool, prefer it when tag.gpu is present.
    // 3) Otherwise pick the first non-NOOP tool tagged as meta.
    // 4) Else NOOP.

    // State flags are encoded into goal_digest by the runner (MVP trick):
    //   "...|FLAGS:DS0=1;DS2=0"


    // Structural Evolution bootstrap (Genesis ToolPack)
    // Activate when goal_id starts with "goal.GENESIS".
    if (goal_digest.find("goal.GENESIS") != std::string::npos) {
        const bool ds0 = goal_digest.find("FLAGS:DS0=1") != std::string::npos;
        std::string stage = extract_flag_value(goal_digest, "DS6_STAGE");

        // Stages are written by Genesis tools into DS6:
        //  - (none)   -> WRITE
        //  - WROTE    -> COMPILE
        //  - COMPILED -> LOAD
        //  - LOADED   -> optionally run a runtime tool once, then NOOP
        if (stage.empty()) {
            if (auto* it = first_with_aid(menu, "AID.GENESIS.WRITE_FILE.v1")) {
                return parse_selector_output("<PICK><SID" + sid4(it->sid.value) + "><END>");
            }
        } else if (stage == "WROTE") {
            if (auto* it = first_with_aid(menu, "AID.GENESIS.COMPILE_SHARED.v1")) {
                return parse_selector_output("<PICK><SID" + sid4(it->sid.value) + "><END>");
            }
        } else if (stage == "COMPILED") {
            if (auto* it = first_with_aid(menu, "AID.GENESIS.LOAD_PLUGIN.v1")) {
                return parse_selector_output("<PICK><SID" + sid4(it->sid.value) + "><END>");
            }
        } else if (stage == "LOADED") {
            // If a runtime tool exists and DS0 is not yet produced, run it once.
            if (!ds0 && menu_has_tag(menu, "tag.runtime")) {
                if (auto* it = first_with_tag(menu, "tag.runtime")) {
                    return parse_selector_output("<PICK><SID" + sid4(it->sid.value) + "><END>");
                }
            }
            return parse_selector_output("<NOOP><END>");
        }
        // If anything is missing, fall through to general rules.
    }
    if (goal_digest.find("FLAGS:DS0=1") != std::string::npos &&
        goal_digest.find("DS2=0") != std::string::npos) {
        if (menu_has_tag(menu, "tag.report")) {
            if (auto* it = first_with_tag(menu, "tag.report")) {
                return parse_selector_output("<PICK><SID" + sid4(it->sid.value) + "><END>");
            }
        }
    }

    if (menu_has_tag(menu, "tag.error")) {
        if (auto* it = first_with_tag(menu, "tag.error")) {
            return parse_selector_output("<PICK><SID" + sid4(it->sid.value) + "><END>");
        }
    }

    if (menu_has_tag(menu, "tag.gpu")) {
        if (auto* it = first_with_tag(menu, "tag.gpu")) {
            return parse_selector_output("<PICK><SID" + sid4(it->sid.value) + "><END>");
        }
    }

    if (menu_has_tag(menu, "tag.fs")) {
        if (auto* it = first_with_tag(menu, "tag.fs")) {
            return parse_selector_output("<PICK><SID" + sid4(it->sid.value) + "><END>");
        }
    }

    if (menu_has_tag(menu, "tag.shell")) {
        if (auto* it = first_with_tag(menu, "tag.shell")) {
            return parse_selector_output("<PICK><SID" + sid4(it->sid.value) + "><END>");
        }
    }

    if (menu_has_tag(menu, "tag.net")) {
        if (auto* it = first_with_tag(menu, "tag.net")) {
            return parse_selector_output("<PICK><SID" + sid4(it->sid.value) + "><END>");
        }
    }

    if (menu_has_tag(menu, "tag.meta")) {
        if (auto* it = first_with_tag(menu, "tag.meta")) {
            return parse_selector_output("<PICK><SID" + sid4(it->sid.value) + "><END>");
        }
    }

    return parse_selector_output("<NOOP><END>");
}

} // namespace machina
