#pragma once

#include "state.h"

#include <string>
#include <unordered_map>
#include <vector>

namespace machina {

struct GoalDesc {
    std::string goal_id;
    std::vector<std::string> candidate_tags;
    std::vector<std::string> required_tools;
    std::vector<uint8_t> required_slots;   // e.g. {0, 2} = DS0+DS2
    bool any_slot_sufficient{false};       // true = OR, false = AND
};

class GoalRegistry {
public:
    // Load goals from a goalpack manifest JSON file.
    void loadGoalPackManifest(const std::string& path);

    // Register a goal programmatically.
    void registerGoal(const GoalDesc& desc, bool allow_override = false);

    // Exact lookup.
    const GoalDesc* getGoal(const std::string& goal_id) const;

    // Check if goal is complete based on required_slots and current state.
    // Falls back to prefix matching when exact goal_id not found.
    bool isGoalComplete(const std::string& goal_id, const DSState& state) const;

    // Get all registered goal IDs.
    std::vector<std::string> allGoalIds() const;

private:
    std::unordered_map<std::string, GoalDesc> goals_;

    // Prefix scan: find the best matching GoalDesc for a goal_id.
    const GoalDesc* findByPrefix(const std::string& goal_id) const;
};

} // namespace machina
