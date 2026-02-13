#include "machina/goal_registry.h"
#include "machina/json_mini.h"

#include <algorithm>
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace machina {

void GoalRegistry::loadGoalPackManifest(const std::string& path) {
    std::ifstream f(path);
    if (!f) throw std::runtime_error("GoalRegistry: cannot open " + path);
    std::ostringstream ss;
    ss << f.rdbuf();
    std::string json = ss.str();

    auto goals_raw = json_mini::get_array_raw(json, "goals");
    if (!goals_raw) return;

    auto goal_objs = json_mini::parse_array_objects_raw(*goals_raw);
    for (const auto& gj : goal_objs) {
        GoalDesc desc;
        desc.goal_id = json_mini::get_string(gj, "goal_id").value_or("");
        if (desc.goal_id.empty()) continue;

        desc.candidate_tags = json_mini::get_array_strings(gj, "candidate_tags");
        desc.required_tools = json_mini::get_array_strings(gj, "required_tools");

        // completion_slots: array of slot numbers
        auto slots_raw = json_mini::get_array_raw(gj, "completion_slots");
        if (slots_raw) {
            auto nums = json_mini::parse_array_numbers(*slots_raw);
            for (double n : nums) {
                int v = (int)n;
                if (v >= 0 && v <= 7) desc.required_slots.push_back((uint8_t)v);
            }
        }

        desc.any_slot_sufficient = json_mini::get_bool(gj, "any_slot_sufficient").value_or(false);

        goals_[desc.goal_id] = std::move(desc);
    }
}

void GoalRegistry::registerGoal(const GoalDesc& desc, bool allow_override) {
    if (desc.goal_id.empty()) return;
    auto it = goals_.find(desc.goal_id);
    if (it != goals_.end() && !allow_override) {
        throw std::runtime_error("GoalRegistry: duplicate goal_id: " + desc.goal_id);
    }
    goals_[desc.goal_id] = desc;
}

const GoalDesc* GoalRegistry::getGoal(const std::string& goal_id) const {
    auto it = goals_.find(goal_id);
    if (it != goals_.end()) return &it->second;
    return nullptr;
}

const GoalDesc* GoalRegistry::findByPrefix(const std::string& goal_id) const {
    // Find the longest prefix match.
    const GoalDesc* best = nullptr;
    size_t best_len = 0;
    for (const auto& kv : goals_) {
        const std::string& key = kv.first;
        if (goal_id.rfind(key, 0) == 0 && key.size() > best_len) {
            best = &kv.second;
            best_len = key.size();
        }
    }
    return best;
}

bool GoalRegistry::isGoalComplete(const std::string& goal_id, const DSState& state) const {
    const GoalDesc* desc = getGoal(goal_id);
    if (!desc) desc = findByPrefix(goal_id);
    if (!desc) return false;
    if (desc->required_slots.empty()) return false;

    if (desc->any_slot_sufficient) {
        // OR: any one required slot occupied => done
        for (uint8_t slot : desc->required_slots) {
            if (state.slots.find(slot) != state.slots.end()) return true;
        }
        return false;
    } else {
        // AND: all required slots must be occupied
        for (uint8_t slot : desc->required_slots) {
            if (state.slots.find(slot) == state.slots.end()) return false;
        }
        return true;
    }
}

std::vector<std::string> GoalRegistry::allGoalIds() const {
    std::vector<std::string> out;
    out.reserve(goals_.size());
    for (const auto& kv : goals_) out.push_back(kv.first);
    std::sort(out.begin(), out.end());
    return out;
}

} // namespace machina
