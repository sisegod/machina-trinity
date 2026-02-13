#include "machina/cts.h"
#include "machina/json_mini.h"

#include <fstream>
#include <sstream>

namespace machina {

static std::string slurp(const std::string& path) {
    std::ifstream f(path);
    std::stringstream ss; ss << f.rdbuf();
    return ss.str();
}

std::vector<CtsIssue> cts_check_toolpack(const std::string& manifest_path) {
    std::vector<CtsIssue> issues;
    auto j = slurp(manifest_path);

    if (!json_mini::has_key(j, "toolpack_id")) issues.push_back({"TP-01","missing toolpack_id"});
    if (!json_mini::has_key(j, "tools")) issues.push_back({"TP-02","missing tools[]"});

    auto tools_raw = json_mini::get_array_raw(j, "tools");
    if (tools_raw) {
        auto tool_objs = json_mini::parse_array_objects_raw(*tools_raw);
        bool has_aid = false;
        for (const auto& obj : tool_objs) {
            auto aid = json_mini::get_string(obj, "aid");
            if (aid && !aid->empty()) has_aid = true;

            // Extreme snapshot 4: enforce minimal tool safety metadata
            // - deterministic tools must declare side_effects
            // - if deterministic && side_effects != ["none"], require replay_inputs fences
            const bool det = json_mini::get_bool(obj, "deterministic").value_or(true);
            auto side = json_mini::get_array_strings(obj, "side_effects");
            auto fences = json_mini::get_array_strings(obj, "replay_inputs");

            if (side.empty()) {
                issues.push_back({"TP-10", std::string("tool missing side_effects: ") + (aid ? *aid : "<unknown>")});
            }

            bool pure = false;
            if (side.size() == 1 && side[0] == "none") pure = true;

            if (det && !pure && fences.empty()) {
                issues.push_back({"TP-11", std::string("deterministic tool with side effects must declare replay_inputs: ") + (aid ? *aid : "<unknown>")});
            }
        }
        if (!has_aid) issues.push_back({"TP-03","no tool has aid"});
    } else {
        // tools key exists but is not a JSON array
        if (json_mini::has_key(j, "tools")) issues.push_back({"TP-04","tools is not an array"});
    }

    return issues;
}

std::vector<CtsIssue> cts_check_goalpack(const std::string& manifest_path) {
    std::vector<CtsIssue> issues;
    auto j = slurp(manifest_path);

    if (!json_mini::has_key(j, "goalpack_id")) issues.push_back({"GP-01","missing goalpack_id"});
    if (!json_mini::has_key(j, "goals")) issues.push_back({"GP-02","missing goals[]"});

    auto goals_raw = json_mini::get_array_raw(j, "goals");
    if (goals_raw) {
        auto goal_objs = json_mini::parse_array_objects_raw(*goals_raw);
        bool has_goal_id = false;
        bool has_candidate_tags = false;
        for (const auto& obj : goal_objs) {
            auto gid = json_mini::get_string(obj, "goal_id");
            if (gid && !gid->empty()) has_goal_id = true;
            if (!json_mini::get_array_strings(obj, "candidate_tags").empty()) has_candidate_tags = true;
        }
        if (!has_goal_id) issues.push_back({"GP-03","no goal has goal_id"});
        if (!has_candidate_tags) issues.push_back({"GP-04","missing candidate_tags"});
    } else {
        if (json_mini::has_key(j, "goals")) issues.push_back({"GP-05","goals is not an array"});
    }

    return issues;
}

} // namespace machina
