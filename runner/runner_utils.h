#pragma once

#include "machina/types.h"
#include "machina/registry.h"
#include "machina/ids.h"
#include "machina/selector.h"
#include "machina/json_mini.h"
#include "machina/hash.h"
#include "machina/gpu_context.h"

#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <optional>
#include <sstream>
#include <string>
#include <unordered_set>
#include <vector>
#include <map>
#include <thread>

namespace machina {

// ---- Utility functions extracted from main.cpp ----

std::filesystem::path resolve_root(const char* argv0);
void set_env_if_missing(const char* key, const std::string& value);
std::string slurp(const std::string& path);
std::string json_escape(const std::string& s);
std::string shallow_merge_json_objects(const std::string& base_json, const std::string& patch_json);

// Merge patch into base, blocking reserved key prefixes.
// Keys starting with any of blocked_prefixes are silently dropped.
// If allowed_keys is non-empty, only those keys pass through (whitelist mode).
std::string safe_merge_patch(const std::string& base_json,
                             const std::string& patch_json,
                             const std::vector<std::string>& blocked_prefixes = {"_system", "_queue", "_meta"},
                             const std::vector<std::string>& allowed_keys = {});

std::string gen_run_id();
Menu build_menu_from_registry(const Registry& reg, const std::vector<std::string>& tags);

// Filter menu items by capability allowlist/blocklist.
// Supports glob suffix: "AID.GENESIS.*" matches any AID starting with "AID.GENESIS.".
Menu filter_menu_by_capabilities(const Menu& menu,
                                 const std::vector<std::string>& allowed,
                                 const std::vector<std::string>& blocked);
ControlMode parse_mode(const std::string& s);
std::unique_ptr<ISelector> make_selector(const std::string& backend, const std::filesystem::path& repo_root);
std::string json_array_compact(const std::vector<std::string>& items);

// Fingerprint / replay
std::optional<std::string> fingerprint_file_fnv1a64(const std::filesystem::path& p);
std::filesystem::path resolve_path_for_replay(const std::string& input_path,
                                               const std::filesystem::path& request_dir,
                                               const std::filesystem::path& root);
std::string gpu_signature();
std::map<std::string, std::string> compute_replay_inputs(const ToolDesc& td,
                                                          const std::string& inputs_json,
                                                          const std::filesystem::path& request_dir,
                                                          const std::filesystem::path& root);
std::string replay_inputs_to_json(const ToolDesc& td,
                                   const std::string& inputs_json,
                                   const std::filesystem::path& request_dir,
                                   const std::filesystem::path& root);

// Time
int64_t now_ms_i64();

// Queue/autopilot helpers
void sleep_ms(int ms);
bool ends_with(const std::string& s, const std::string& suf);
std::unordered_set<std::string> list_run_logs(const std::filesystem::path& log_dir);
std::optional<std::filesystem::path> newest_new_log(const std::filesystem::path& log_dir,
                                                    const std::unordered_set<std::string>& before);
std::filesystem::path default_queue_dir(const std::filesystem::path& root);
void ensure_queue_dirs(const std::filesystem::path& q);
std::string slurp_file(const std::filesystem::path& p);
int64_t getenv_i64(const char* k, int64_t defv);

namespace runner_detail {
int getenv_int(const char* k, int defv);
} // namespace runner_detail

int64_t parse_due_from_filename(const std::filesystem::path& p);
int64_t extract_next_run_at(const std::string& json);
bool parse_retry_name(const std::string& fname, int64_t& due_ms, std::string& rest_name);
void move_due_retries(const std::filesystem::path& retry_dir, const std::filesystem::path& inbox_dir);
std::string patch_queue_meta_for_retry(const std::string& req_json,
                                       int attempt,
                                       int max_attempts,
                                       int64_t next_run_at_ms,
                                       const std::string& last_error);
int parse_priority_prefix(const std::string& fname, int defv = 5000);
std::vector<std::filesystem::path> list_inbox_json(const std::filesystem::path& inbox);
std::vector<std::filesystem::path> list_dir_json(const std::filesystem::path& dir);
bool parse_attempt_from_name(const std::string& name, int& attempt_out);
int64_t backoff_delay_ms(int next_attempt,
                         int64_t base_ms,
                         int64_t mult,
                         int64_t max_ms,
                         int64_t jitter_ms);
std::string write_atomic_json(const std::filesystem::path& dst, const std::string& body);

// ---- Shared job processing (used by both serve and autopilot) ----

struct ToolMetricEntry {
    std::string aid;
    bool ok{false};
    int duration_ms{0};
};

struct JobResult {
    int exit_code{-1};
    bool scheduled_retry{false};
    bool deadletter{false};
    std::filesystem::path final_path;
    std::string result_json;    // metadata JSON written to out/
    std::string log_path;       // path of the run log file
    int attempt{1};
    int max_attempts{5};
    std::vector<ToolMetricEntry> tool_metrics; // per-tool execution stats from run log
};

// Process a queue job: run cmd_run, handle success/failure/retry/dlq.
// proc_file: the .processing file (already moved from inbox)
// base_name: original filename (without .processing suffix)
// Backoff config from _queue metadata or env defaults.
// Returns structured result. Caller handles WAL/counters/logging.
JobResult process_queue_job(const std::filesystem::path& proc_file,
                            const std::string& base_name,
                            char* argv0,
                            const std::filesystem::path& root,
                            const std::filesystem::path& queue_dir);

} // namespace machina
