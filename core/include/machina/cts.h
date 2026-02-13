#pragma once
#include <string>
#include <vector>

namespace machina {

// Minimal CTS for Profile A (no external deps):
// - JSON parse sanity (very small, permissive)
// - required fields presence checks
struct CtsIssue {
    std::string code;
    std::string message;
};

std::vector<CtsIssue> cts_check_toolpack(const std::string& manifest_path);
std::vector<CtsIssue> cts_check_goalpack(const std::string& manifest_path);

} // namespace machina
