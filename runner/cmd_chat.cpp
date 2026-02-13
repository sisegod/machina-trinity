#include "cmd_chat.h"
#include "runner_utils.h"
#include "tool_setup.h"

#include "machina/types.h"
#include "machina/registry.h"
#include "machina/ids.h"
#include "machina/selector.h"
#include "machina/selector_gpu.h"
#include "machina/state.h"
#include "machina/tx.h"
#include "machina/log.h"
#include "machina/tools.h"
#include "machina/json_mini.h"
#include "machina/hash.h"
#include "machina/goal_registry.h"
#include "machina/proc.h"

#include <algorithm>
#include <chrono>
#include <iostream>
#include <sstream>
#include <unordered_set>
#include <vector>

using namespace machina;

// ---- Chat data structures ----

struct ConvMessage {
    std::string role;     // "user" | "assistant" | "system"
    std::string content;
};

struct ChatAction {
    std::string kind;              // "goal" | "tool"
    std::string goal_id;           // kind=goal
    std::string aid;               // kind=tool
    std::string inputs_json;
    std::vector<std::string> tags; // kind=goal
};

struct ChatIntent {
    std::string type;              // "action" | "reply" | "config"
    std::vector<ChatAction> actions;
    std::string content;           // type=reply or config
    std::string assistant_prefix;
    bool needs_summary{false};
    std::vector<std::pair<std::string, std::string>> config_changes;
};

struct ActionResult {
    std::string label;
    bool success{false};
    int duration_ms{0};
    std::string artifacts_json; // JSON array of slot artifacts
};

// ---- Constants ----

static const size_t MAX_HISTORY_TURNS = 20;

// Config allowlist for type=config changes
static const std::unordered_set<std::string> CONFIG_ALLOWLIST = {
    "MACHINA_POLICY_CMD",
    "MACHINA_CHAT_BACKEND",
    "MACHINA_CHAT_MAX_TOKENS",
    "MACHINA_CHAT_TEMPERATURE",
    "MACHINA_SELECTOR",
    "OAI_COMPAT_BASE_URL",
    "OAI_COMPAT_MODEL",
    "OAI_COMPAT_MAX_TOKENS",
    "ANTHROPIC_MODEL",
};

// ---- Helpers ----

static std::string conv_to_json(const std::vector<ConvMessage>& conv) {
    std::string out = "[";
    for (size_t i = 0; i < conv.size(); i++) {
        if (i) out += ",";
        out += "{\"role\":\"" + json_escape(conv[i].role) + "\","
               "\"content\":\"" + json_escape(conv[i].content) + "\"}";
    }
    out += "]";
    return out;
}

static std::string tools_to_json(const Registry& reg) {
    std::string out = "[";
    bool first = true;
    for (const auto& td : reg.allToolDescs()) {
        if (!first) out += ",";
        first = false;
        out += "{\"aid\":\"" + json_escape(td.aid) + "\","
               "\"name\":\"" + json_escape(td.name) + "\","
               "\"tags\":[";
        for (size_t j = 0; j < td.tags.size(); j++) {
            if (j) out += ",";
            out += "\"" + json_escape(td.tags[j]) + "\"";
        }
        out += "]}";
    }
    out += "]";
    return out;
}

static std::string goals_to_json(const GoalRegistry& goal_reg) {
    auto ids = goal_reg.allGoalIds();
    std::string out = "[";
    for (size_t i = 0; i < ids.size(); i++) {
        if (i) out += ",";
        out += "\"" + json_escape(ids[i]) + "\"";
    }
    out += "]";
    return out;
}

static void trim_conversation(std::vector<ConvMessage>& conv) {
    while (conv.size() > MAX_HISTORY_TURNS * 2)
        conv.erase(conv.begin());
}

static std::string call_chat_driver(const std::string& chat_cmd,
                                     const std::string& payload_json,
                                     const std::filesystem::path& root) {
    auto argv = split_argv_quoted(chat_cmd);
    if (argv.empty()) return "{}";

    ProcLimits lim;
    lim.timeout_ms = runner_detail::getenv_int("MACHINA_CHAT_TIMEOUT_MS", 30000);
    lim.stdout_max_bytes = 64 * 1024;
    lim.rlimit_cpu_sec = 30;
    lim.rlimit_as_mb = 0;     // unlimited for Python/torch
    lim.rlimit_nofile = 256;
    lim.rlimit_nproc = 0;     // unlimited for Python threads

    ProcResult pr;
    bool ok = proc_run_capture_sandboxed_stdin(argv, root.string(), payload_json, lim, &pr);

    if (!ok || pr.timed_out || pr.exit_code != 0) {
        return "{}";
    }
    // Extract first line of JSON (skip any stderr mixed in)
    auto nl = pr.output.find('\n');
    if (nl != std::string::npos) {
        // Try to find a line that starts with '{'
        std::istringstream iss(pr.output);
        std::string line;
        while (std::getline(iss, line)) {
            auto p = line.find('{');
            if (p != std::string::npos && line.rfind('}') != std::string::npos) {
                return line.substr(p);
            }
        }
    }
    return pr.output;
}

static ChatIntent parse_intent(const std::string& json_str) {
    ChatIntent intent;
    if (json_str.empty() || json_str == "{}") return intent;

    auto type = json_mini::get_string(json_str, "type");
    if (!type) return intent;
    intent.type = *type;

    if (intent.type == "reply" || intent.type == "config") {
        intent.content = json_mini::get_string(json_str, "content").value_or("");
    }

    if (intent.type == "config") {
        auto changes_raw = json_mini::get_array_raw(json_str, "changes");
        if (changes_raw) {
            // Parse array of {key, value} objects
            // Simple: split by },{ pattern
            std::string arr = *changes_raw;
            size_t pos = 0;
            while (pos < arr.size()) {
                auto start = arr.find('{', pos);
                if (start == std::string::npos) break;
                auto end = arr.find('}', start);
                if (end == std::string::npos) break;
                std::string obj = arr.substr(start, end - start + 1);
                auto k = json_mini::get_string(obj, "key");
                auto v = json_mini::get_string(obj, "value");
                if (k && v) {
                    intent.config_changes.push_back({*k, *v});
                }
                pos = end + 1;
            }
        }
    }

    if (intent.type == "action") {
        intent.assistant_prefix = json_mini::get_string(json_str, "assistant_prefix").value_or("");
        auto ns = json_mini::get_string(json_str, "needs_summary");
        intent.needs_summary = (ns && (*ns == "true" || *ns == "1"));
        // Also check for boolean true
        if (!intent.needs_summary) {
            // Check raw
            auto pos = json_str.find("\"needs_summary\"");
            if (pos != std::string::npos) {
                auto vpos = json_str.find(':', pos);
                if (vpos != std::string::npos) {
                    auto rest = json_str.substr(vpos + 1, 20);
                    if (rest.find("true") != std::string::npos) intent.needs_summary = true;
                }
            }
        }

        auto actions_raw = json_mini::get_array_raw(json_str, "actions");
        if (actions_raw) {
            std::string arr = *actions_raw;
            int depth = 0;
            size_t obj_start = 0;
            for (size_t i = 0; i < arr.size(); i++) {
                if (arr[i] == '{') {
                    if (depth == 0) obj_start = i;
                    depth++;
                } else if (arr[i] == '}') {
                    depth--;
                    if (depth == 0) {
                        std::string obj = arr.substr(obj_start, i - obj_start + 1);
                        ChatAction action;
                        action.kind = json_mini::get_string(obj, "kind").value_or("tool");
                        action.goal_id = json_mini::get_string(obj, "goal_id").value_or("");
                        action.aid = json_mini::get_string(obj, "aid").value_or("");
                        action.inputs_json = json_mini::get_object_raw(obj, "inputs").value_or("{}");
                        action.tags = json_mini::get_array_strings(obj, "candidate_tags");
                        intent.actions.push_back(std::move(action));
                    }
                }
            }
        }
    }

    return intent;
}

// ---- Pattern matching fallback (no LLM) ----

static ChatIntent detect_pattern(const std::string& input, const Registry& /*reg*/, const GoalRegistry& /*goal_reg*/) {
    ChatIntent intent;
    std::string lower = input;
    std::transform(lower.begin(), lower.end(), lower.begin(), ::tolower);

    // Greeting patterns
    if (lower == "hi" || lower == "hello" || lower.find("안녕") != std::string::npos ||
        lower.find("hey") != std::string::npos) {
        intent.type = "reply";
        intent.content = "Hi! I'm Machina Trinity agent. I can help with log analysis, GPU monitoring, file operations, memory search, and more. What can I do for you?";
        return intent;
    }

    // Error scan
    if (lower.find("error") != std::string::npos || lower.find("log") != std::string::npos ||
        lower.find("에러") != std::string::npos || lower.find("로그") != std::string::npos) {
        intent.type = "action";
        ChatAction a;
        a.kind = "goal";
        a.goal_id = "goal.ERROR_SCAN.v1";
        a.inputs_json = "{\"input_path\":\"/var/log/syslog\",\"pattern\":\"ERROR\",\"max_rows\":1000000}";
        a.tags = {"tag.log", "tag.scan", "tag.error", "tag.report"};
        intent.actions.push_back(std::move(a));
        intent.assistant_prefix = "Checking logs...";
        intent.needs_summary = true;
        return intent;
    }

    // GPU
    if (lower.find("gpu") != std::string::npos || lower.find("그래픽") != std::string::npos) {
        intent.type = "action";
        ChatAction a;
        a.kind = "goal";
        a.goal_id = "goal.GPU_SMOKE.v1";
        a.inputs_json = "{}";
        a.tags = {"tag.gpu", "tag.meta"};
        intent.actions.push_back(std::move(a));
        intent.assistant_prefix = "Checking GPU...";
        intent.needs_summary = true;
        return intent;
    }

    // Memory
    if (lower.find("remember") != std::string::npos || lower.find("기억") != std::string::npos ||
        lower.find("memo") != std::string::npos) {
        intent.type = "action";
        ChatAction a;
        a.kind = "tool";
        a.aid = "AID.MEMORY.APPEND.v1";
        a.inputs_json = "{\"stream\":\"chat\",\"text\":\"" + json_escape(input) + "\"}";
        intent.actions.push_back(std::move(a));
        intent.assistant_prefix = "Saving to memory...";
        intent.needs_summary = false;
        return intent;
    }

    // Default: reply
    intent.type = "reply";
    intent.content = "I'm not sure what you mean. Could you be more specific? I can help with: log analysis, GPU monitoring, file operations, memory search.";
    return intent;
}

// ---- Goal execution (extracted from cmd_run pattern) ----

static ActionResult run_goal_action(
    Registry& reg,
    ToolRunner& runner,
    GoalRegistry& goal_reg,
    ISelector* selector,
    PluginManager& plugin_mgr,
    RunnerRegistrar& registrar,
    const std::filesystem::path& root,
    const std::string& goal_id,
    const std::string& inputs_json,
    const std::vector<std::string>& tags)
{
    ActionResult result;
    result.label = goal_id;

    DSState state;
    Budget budget;
    budget.max_steps = 20;

    std::string inputs = inputs_json;
    auto merged_tags = tags;
    if (std::find(merged_tags.begin(), merged_tags.end(), "tag.meta") == merged_tags.end())
        merged_tags.push_back("tag.meta");

    auto dedup_tags = [](std::vector<std::string> t) {
        std::sort(t.begin(), t.end());
        t.erase(std::unique(t.begin(), t.end()), t.end());
        return t;
    };

    auto has_slot = [&](DSSlot s) -> bool {
        return state.slots.find((uint8_t)s) != state.slots.end();
    };

    auto t0 = std::chrono::steady_clock::now();
    int invalid = 0;

    for (int step = 0; step < budget.max_steps; step++) {
        const bool ds0 = has_slot(DSSlot::DS0);
        const bool ds2 = has_slot(DSSlot::DS2);

        std::vector<std::string> step_tags = merged_tags;
        if (ds0 && !ds2) step_tags.push_back("tag.report");
        step_tags = dedup_tags(std::move(step_tags));

        Menu menu = build_menu_from_registry(reg, step_tags);
        std::string state_digest = state.digest();

        std::string flags = std::string("FLAGS:DS0=") + (ds0 ? "1" : "0") +
                            ";DS2=" + (ds2 ? "1" : "0") + ";";
        std::string goal_context = goal_id + "|" + flags;
        for (auto& t : step_tags) goal_context += "|" + t;

        Selection picked = selector->select(menu, goal_context, state_digest, ControlMode::BLENDED, inputs);

        if (picked.kind == Selection::Kind::INVALID) {
            invalid++;
            if (invalid > budget.max_invalid_picks) break;
            continue;
        }
        if (picked.kind == Selection::Kind::NOOP || picked.kind == Selection::Kind::ASK_SUP)
            break;

        const auto* mi = menu.resolve(*picked.sid);
        if (!mi) { invalid++; continue; }

        if (picked.input_patch_json)
            inputs = safe_merge_patch(inputs, *picked.input_patch_json);

        Tx tx(state);
        auto tool_res = runner.run(mi->aid, inputs, tx.tmp());
        if (tool_res.status == StepStatus::OK) {
            tx.commit(state);
            std::cerr << "  > " << mi->aid << " OK\n";
        } else {
            tx.rollback();
            std::cerr << "  > " << mi->aid << " ERR: " << tool_res.error << "\n";
            break;
        }

        // Automatic reload of plugins
        {
            std::string perr;
            plugin_mgr.load_new_from_dir(root / "toolpacks" / "runtime_plugins", &registrar, &perr);
        }

        if (goal_reg.isGoalComplete(goal_id, state)) {
            result.success = true;
            break;
        }
    }

    auto t1 = std::chrono::steady_clock::now();
    result.duration_ms = (int)std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count();

    // Collect artifacts
    std::string arts = "[";
    bool first = true;
    for (const auto& kv : state.slots) {
        if (!first) arts += ",";
        first = false;
        arts += "{\"slot\":\"DS" + std::to_string((int)kv.first) + "\","
                "\"type\":\"" + json_escape(kv.second.type) + "\","
                "\"content\":" + kv.second.content_json + "}";
    }
    arts += "]";
    result.artifacts_json = arts;
    return result;
}

// ---- Single tool execution ----

static ActionResult exec_tool_action(
    ToolRunner& runner,
    const Registry& reg,
    const std::string& aid,
    const std::string& inputs_json)
{
    ActionResult result;
    result.label = aid;

    const auto* td = reg.getTool(aid);
    if (!td) {
        result.success = false;
        result.artifacts_json = "[]";
        return result;
    }

    DSState tmp;
    auto t0 = std::chrono::steady_clock::now();
    auto res = runner.run(aid, inputs_json, tmp);
    auto t1 = std::chrono::steady_clock::now();
    result.duration_ms = (int)std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count();
    result.success = (res.status == StepStatus::OK);

    std::string arts = "[";
    bool first = true;
    for (const auto& kv : tmp.slots) {
        if (!first) arts += ",";
        first = false;
        arts += "{\"slot\":\"DS" + std::to_string((int)kv.first) + "\","
                "\"type\":\"" + json_escape(kv.second.type) + "\","
                "\"content\":" + kv.second.content_json + "}";
    }
    if (first && !res.output_json.empty()) {
        arts += "{\"slot\":\"output\",\"type\":\"result\",\"content\":" + res.output_json + "}";
    }
    arts += "]";
    result.artifacts_json = arts;
    return result;
}

// ---- Build payloads ----

static std::string build_intent_payload(
    const std::string& user_msg,
    const std::vector<ConvMessage>& conv,
    const Registry& reg,
    const GoalRegistry& goal_reg,
    const std::string& selector_backend)
{
    std::string session = "{\"selector\":\"" + json_escape(selector_backend) + "\","
                          "\"ops_count\":" + std::to_string(conv.size() / 2) + "}";

    return std::string("{")
        + "\"mode\":\"intent\","
        + "\"user_message\":\"" + json_escape(user_msg) + "\","
        + "\"conversation\":" + conv_to_json(conv) + ","
        + "\"available_tools\":" + tools_to_json(reg) + ","
        + "\"available_goals\":" + goals_to_json(goal_reg) + ","
        + "\"session\":" + session
        + "}";
}

static std::string build_summary_payload(
    const std::string& user_msg,
    const std::vector<ConvMessage>& conv,
    const std::vector<ActionResult>& results)
{
    std::string action_results = "[";
    for (size_t i = 0; i < results.size(); i++) {
        if (i) action_results += ",";
        action_results += "{"
            "\"label\":\"" + json_escape(results[i].label) + "\","
            "\"success\":" + std::string(results[i].success ? "true" : "false") + ","
            "\"duration_ms\":" + std::to_string(results[i].duration_ms) + ","
            "\"artifacts\":" + results[i].artifacts_json +
            "}";
    }
    action_results += "]";

    return std::string("{")
        + "\"mode\":\"summary\","
        + "\"user_message\":\"" + json_escape(user_msg) + "\","
        + "\"conversation\":" + conv_to_json(conv) + ","
        + "\"action_results\":" + action_results
        + "}";
}

// ---- Main REPL ----

int cmd_chat(int argc, char** argv) {
    (void)argc;
    const auto root = resolve_root(argv[0]);
    set_env_if_missing("MACHINA_TOOLHOST_BIN",
        (std::filesystem::path(argv[0]).parent_path() / "machina_toolhost").string());
    set_env_if_missing("MACHINA_ROOT", root.string());

    // Runtime initialization (same as cmd_run)
    Registry reg;
    ToolRunner runner;
    PluginManager plugin_mgr;
    RunnerRegistrar registrar{reg, runner, false};
    setup_runtime(reg, runner, plugin_mgr, registrar, root);

    // GoalRegistry
    GoalRegistry goal_reg;
    {
        std::error_code ec;
        auto gp_dir = root / "goalpacks";
        if (std::filesystem::exists(gp_dir, ec)) {
            for (auto& entry : std::filesystem::directory_iterator(gp_dir, ec)) {
                if (ec) break;
                if (!entry.is_directory(ec)) continue;
                auto manifest = entry.path() / "manifest.json";
                if (std::filesystem::exists(manifest, ec)) {
                    try { goal_reg.loadGoalPackManifest(manifest.string()); } catch (...) {}
                }
            }
        }
    }
    {
        GoalDesc genesis_desc;
        genesis_desc.goal_id = "goal.GENESIS";
        genesis_desc.required_slots = {(uint8_t)DSSlot::DS0, (uint8_t)DSSlot::DS7};
        goal_reg.registerGoal(genesis_desc, true);
    }

    // Selector
    std::string selector_backend = "HEURISTIC";
    if (const char* s = std::getenv("MACHINA_SELECTOR"))
        if (std::string(s) == "GPU_CENTROID") selector_backend = "GPU_CENTROID";
    auto selector = make_selector(selector_backend, root);

    // Chat driver command
    std::string default_chat_cmd = "python3 " + (root / "policies" / "chat_driver.py").string();
    const char* chat_cmd_env = std::getenv("MACHINA_CHAT_CMD");
    std::string chat_cmd = chat_cmd_env ? chat_cmd_env : default_chat_cmd;

    // Conversation history
    std::vector<ConvMessage> conversation;

    std::cout << "Machina Trinity Chat (type /quit to exit)\n";
    std::cout << "  selector: " << selector_backend << "\n";
    std::cout << "  tools: " << reg.allToolDescs().size() << " registered\n";
    std::cout << "  goals: " << goal_reg.allGoalIds().size() << " registered\n\n";

    int ops = 0;
    std::string line;

    while (true) {
        std::cout << "machina:" << ops << "> ";
        std::cout.flush();
        if (!std::getline(std::cin, line)) break;

        // Trim whitespace
        auto start = line.find_first_not_of(" \t\r\n");
        if (start == std::string::npos) continue;
        line = line.substr(start);
        auto end = line.find_last_not_of(" \t\r\n");
        if (end != std::string::npos) line = line.substr(0, end + 1);

        if (line.empty()) continue;
        if (line == "/quit" || line == "/exit" || line == "/q") break;

        // Add to conversation
        conversation.push_back({"user", line});
        trim_conversation(conversation);

        // Phase 1: LLM Intent parsing
        std::string intent_payload = build_intent_payload(
            line, conversation, reg, goal_reg, selector_backend);
        std::string intent_response = call_chat_driver(chat_cmd, intent_payload, root);
        ChatIntent intent = parse_intent(intent_response);

        // Fallback if LLM failed
        if (intent.type.empty()) {
            intent = detect_pattern(line, reg, goal_reg);
        }

        // Phase 2: Handle by type
        if (intent.type == "reply") {
            std::cout << "\n" << intent.content << "\n\n";
            conversation.push_back({"assistant", intent.content});
            ops++;
            continue;
        }

        if (intent.type == "config") {
            for (const auto& c : intent.config_changes) {
                if (CONFIG_ALLOWLIST.count(c.first)) {
                    setenv(c.first.c_str(), c.second.c_str(), 1);
                    std::cerr << "  [config] " << c.first << " = " << c.second << "\n";
                } else {
                    std::cerr << "  [config] blocked: " << c.first << "\n";
                }
            }
            // Rebuild selector if needed
            if (const char* s = std::getenv("MACHINA_SELECTOR")) {
                std::string new_sel = s;
                if (new_sel != selector_backend) {
                    selector_backend = new_sel;
                    selector = make_selector(selector_backend, root);
                    std::cerr << "  [config] selector rebuilt: " << selector_backend << "\n";
                }
            }
            std::cout << "\n" << intent.content << "\n\n";
            conversation.push_back({"assistant", intent.content});
            ops++;
            continue;
        }

        if (intent.type == "action") {
            if (!intent.assistant_prefix.empty()) {
                std::cout << "\n" << intent.assistant_prefix << "\n";
            }

            // Phase 3: Execute actions
            std::vector<ActionResult> results;
            for (const auto& action : intent.actions) {
                // Validate before executing
                if (action.kind == "goal") {
                    if (!goal_reg.getGoal(action.goal_id)) {
                        std::cerr << "  [warn] unknown goal: " << action.goal_id << "\n";
                        continue;
                    }
                    auto r = run_goal_action(reg, runner, goal_reg, selector.get(),
                                             plugin_mgr, registrar, root,
                                             action.goal_id, action.inputs_json, action.tags);
                    results.push_back(std::move(r));
                } else if (action.kind == "tool") {
                    if (!reg.getTool(action.aid)) {
                        std::cerr << "  [warn] unknown tool: " << action.aid << "\n";
                        continue;
                    }
                    auto r = exec_tool_action(runner, reg, action.aid, action.inputs_json);
                    results.push_back(std::move(r));
                }
            }

            // Phase 4: Summary
            if (intent.needs_summary && !results.empty()) {
                std::string summary_payload = build_summary_payload(line, conversation, results);
                std::string summary_response = call_chat_driver(chat_cmd, summary_payload, root);
                auto summary_content = json_mini::get_string(summary_response, "content");
                std::string summary = summary_content.value_or("");
                if (summary.empty()) {
                    // Fallback: print raw results
                    for (const auto& r : results) {
                        summary += r.label + ": " + (r.success ? "OK" : "FAIL")
                                + " (" + std::to_string(r.duration_ms) + "ms)\n";
                    }
                }
                std::cout << "\n" << summary << "\n\n";
                conversation.push_back({"assistant", summary});
            } else {
                // No summary needed — print results directly
                for (const auto& r : results) {
                    std::cout << "  " << r.label << ": "
                              << (r.success ? "OK" : "FAIL")
                              << " (" << r.duration_ms << "ms)\n";
                }
                std::cout << "\n";
                std::string msg;
                for (const auto& r : results)
                    msg += r.label + ": " + (r.success ? "OK" : "FAIL") + ". ";
                conversation.push_back({"assistant", msg});
            }
        }

        ops++;
    }

    std::cout << "\nBye!\n";
    return 0;
}
