#include "cmd_run.h"
#include "runner_utils.h"
#include "tool_setup.h"
#include "tools/tier0/genesis.h"

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

#include <algorithm>
#include <chrono>
#include <iostream>
#include <unordered_map>
#include <unordered_set>

using namespace machina;

int cmd_run(int argc, char** argv) {
    if (argc < 3) {
        std::cerr << "usage: machina_cli run <run_request.json>\n";
        std::cerr << "env: MACHINA_SELECTOR=HEURISTIC|GPU_CENTROID, MACHINA_USE_GPU=1 (CUDA build only)\n";
        return 2;
    }

    const auto root = resolve_root(argv[0]);
    set_env_if_missing("MACHINA_TOOLHOST_BIN", (std::filesystem::path(argv[0]).parent_path() / "machina_toolhost").string());

    // Load manifests, register tools, init genesis, preload plugins
    Registry reg;
    ToolRunner runner;
    PluginManager plugin_mgr;
    RunnerRegistrar registrar{reg, runner, false};
    setup_runtime(reg, runner, plugin_mgr, registrar, root);

    // Read run request
    std::filesystem::path req_path = std::filesystem::path(argv[2]);
    if (!req_path.is_absolute()) req_path = std::filesystem::absolute(req_path);
    std::filesystem::path request_dir = req_path.parent_path();
    set_env_if_missing("MACHINA_ROOT", root.string());
    set_env_if_missing("MACHINA_REQUEST_DIR", request_dir.string());
    std::string req = slurp(req_path.string());
    std::string goal_id = machina::json_mini::get_string(req, "goal_id").value_or("");
    auto tags = machina::json_mini::get_array_strings(req, "candidate_tags");
    if (std::find(tags.begin(), tags.end(), "tag.meta") == tags.end()) tags.push_back("tag.meta");
    std::string candidate_tags_raw = json_array_compact(tags);
    std::string inputs = machina::json_mini::get_object_raw(req, "inputs").value_or("{}");
    auto mode = parse_mode(machina::json_mini::get_string(req, "control_mode").value_or("FALLBACK_ONLY"));

    // Capability restrictions (opt-in per-request tool filtering)
    std::vector<std::string> cap_allowed, cap_blocked;
    {
        auto cap_raw = machina::json_mini::get_object_raw(req, "_capabilities").value_or("");
        if (!cap_raw.empty()) {
            cap_allowed = machina::json_mini::get_array_strings(cap_raw, "allowed_tools");
            cap_blocked = machina::json_mini::get_array_strings(cap_raw, "blocked_tools");
        }
    }

    // Convenience: a zero-input Genesis demo that bootstraps and runs a hot-loaded tool.
    if (goal_id == "goal.GENESIS_DEMO_HELLO.v1") {
        bool has_rel = machina::json_mini::has_key(inputs, "relative_path");
        bool has_content = machina::json_mini::has_key(inputs, "content");
        bool has_src = machina::json_mini::has_key(inputs, "src_relative_path");
        bool has_out = machina::json_mini::has_key(inputs, "out_name");
        if (!(has_rel && has_content && has_src && has_out)) {
            auto tpl_path = root / "toolpacks" / "runtime_genesis" / "templates" / "hello_tool.cpp";
            std::string tpl;
            try { tpl = slurp(tpl_path.string()); } catch (...) { tpl = "// missing template: hello_tool.cpp\n"; }
            auto jq = [&](const std::string& x){ return std::string("\"") + json_escape(x) + "\""; };

            inputs = std::string("{")
                   + "\"relative_path\":\"hello_tool.cpp\","
                   + "\"content\":" + jq(tpl) + ","
                   + "\"overwrite\":true,"
                   + "\"src_relative_path\":\"hello_tool.cpp\","
                   + "\"out_name\":\"hello_tool\""
                   + "}";
        }
    }

    if (goal_id.empty()) {
        std::cerr << "run_request missing goal_id\n";
        return 2;
    }

    // ---- GoalRegistry setup (Phase 2) ----
    GoalRegistry goal_reg;

    // Load goalpack manifests from goalpacks/ directory
    {
        std::error_code ec;
        auto gp_dir = root / "goalpacks";
        if (std::filesystem::exists(gp_dir, ec)) {
            for (auto& entry : std::filesystem::directory_iterator(gp_dir, ec)) {
                if (ec) break;
                if (!entry.is_directory(ec)) continue;
                auto manifest = entry.path() / "manifest.json";
                if (std::filesystem::exists(manifest, ec)) {
                    try {
                        goal_reg.loadGoalPackManifest(manifest.string());
                    } catch (...) {
                        // best-effort: skip malformed manifests
                    }
                }
            }
        }
    }

    // Programmatic registration for Genesis/Demo goals
    {
        GoalDesc genesis_desc;
        genesis_desc.goal_id = "goal.GENESIS";
        genesis_desc.required_slots = {(uint8_t)DSSlot::DS0, (uint8_t)DSSlot::DS7};
        goal_reg.registerGoal(genesis_desc, true);

        GoalDesc demo_missing;
        demo_missing.goal_id = "goal.DEMO.MISSING_TOOL.v1";
        demo_missing.required_slots = {(uint8_t)DSSlot::DS0};
        goal_reg.registerGoal(demo_missing, true);
    }

    // Selector backend (env)
    std::string selector_backend = "HEURISTIC";
    if (const char* s = std::getenv("MACHINA_SELECTOR")) {
        std::string v = s;
        if (v == "GPU_CENTROID") selector_backend = "GPU_CENTROID";
    }
    auto selector = make_selector(selector_backend, root);

    // Setup run header + logger
    RunHeader hdr;
    hdr.run_id = gen_run_id();
    hdr.request_id = machina::json_mini::get_string(req, "request_id").value_or("");
    auto log_dir = root / "logs";
    std::filesystem::create_directories(log_dir);
    std::string log_path = (log_dir / ("run_" + hdr.run_id + ".jsonl")).string();
    JsonlLogger log(hdr, log_path);

    // Init state
    DSState state;
    Budget budget;

    auto has_slot = [&](DSSlot s) -> bool {
        return state.slots.find((uint8_t)s) != state.slots.end();
    };

    auto dedup_tags = [&](std::vector<std::string> t) {
        std::sort(t.begin(), t.end());
        t.erase(std::unique(t.begin(), t.end()), t.end());
        return t;
    };

    int invalid = 0;

    auto env_true = [&](const char* k, bool defv=false) -> bool {
        const char* v = std::getenv(k);
        if (!v) return defv;
        std::string s = v;
        if (s == "1" || s == "true" || s == "TRUE" || s == "yes" || s == "YES") return true;
        if (s == "0" || s == "false" || s == "FALSE" || s == "no" || s == "NO") return false;
        return defv;
    };

    const bool auto_genesis_on_missing = env_true("MACHINA_GENESIS_AUTOTRIGGER", false);
    const bool auto_genesis_autostub = env_true("MACHINA_GENESIS_AUTOSTUB", false);
    std::unordered_set<std::string> autostub_done;

    // ---- Phase 4: Genesis compile retry config ----
    int genesis_compile_retries = 0;
    const int genesis_compile_retries_max = runner_detail::getenv_int("MACHINA_GENESIS_COMPILE_RETRIES", 3);

    // Loop guard
    std::unordered_map<std::string, int> loop_guard;

    for (int step = 0; step < budget.max_steps; step++) {
        const bool ds0 = has_slot(DSSlot::DS0);
        const bool ds2 = has_slot(DSSlot::DS2);
        const bool ds6 = has_slot(DSSlot::DS6);
        const bool ds7 = has_slot(DSSlot::DS7);

        std::string ds6_stage;
        if (ds6) {
            auto it = state.slots.find((uint8_t)DSSlot::DS6);
            if (it != state.slots.end()) {
                ds6_stage = machina::json_mini::get_string(it->second.content_json, "stage").value_or("");
            }
        }

        // Automatic reload of plugins
        {
            std::string perr;
            size_t newly = plugin_mgr.load_new_from_dir(root / "toolpacks" / "runtime_plugins", &registrar, &perr);
            if (newly > 0 || !perr.empty()) {
                log.event(step, "plugins_reload",
                          "{"
                          "\"newly_loaded\":" + std::to_string(newly) + ","
                          "\"error\":\"" + json_escape(perr) + "\""
                          "}");
            }
        }

        // Expand candidate tags based on current state
        std::vector<std::string> step_tags = tags;
        if (goal_id.rfind("goal.GENESIS", 0) == 0) {
            step_tags.push_back("tag.genesis");
            step_tags.push_back("tag.runtime");
            step_tags.push_back("tag.meta");
        }
        if (ds0 && !ds2) step_tags.push_back("tag.report");
        step_tags = dedup_tags(std::move(step_tags));
        std::string step_tags_raw = json_array_compact(step_tags);

        // Build menu
        Menu menu = build_menu_from_registry(reg, step_tags);

        // Apply per-request capability restrictions
        if (!cap_allowed.empty() || !cap_blocked.empty()) {
            menu = filter_menu_by_capabilities(menu, cap_allowed, cap_blocked);
        }

        // Digests
        std::string menu_digest = menu.digest();
        std::string menu_digest_fast = hash::hex64(hash::fnv1a64(menu.digest_raw()));
        std::string state_digest = state.digest();
        std::string state_digest_fast = state.digest_fast();

        {
            std::string lk = menu_digest_fast + "|" + state_digest_fast;
            int c = ++loop_guard[lk];
            if (c > 3) {
                log.event(step, "loop_guard_triggered",
                          "{"
                          "\"count\":" + std::to_string(c) + ","
                          "\"menu_digest_fast\":\"" + json_escape(menu_digest_fast) + "\","
                          "\"state_digest_fast\":\"" + json_escape(state_digest_fast) + "\""
                          "}");
                break;
            }
        }

        std::string flags = std::string("FLAGS:DS0=") + (ds0 ? "1" : "0") +
                            ";DS2=" + (ds2 ? "1" : "0") +
                            ";DS6=" + (ds6 ? "1" : "0") +
                            ";DS7=" + (ds7 ? "1" : "0") +
                            ";DS6_STAGE=" + ds6_stage + ";";
        // goal_context: used by both heuristic (needs FLAGS) and GPU_CENTROID (needs tags).
        // Exclude menu_digest — it pollutes the embedding space for GPU_CENTROID.
        std::string goal_context = goal_id + "|" + flags;
        for (auto& t : step_tags) goal_context += "|" + t;

        log.event(step, "menu_built",
                  "{"
                  "\"goal_id\":\"" + json_escape(goal_id) + "\","
                  "\"candidate_tags\":" + step_tags_raw + ","
                  "\"base_candidate_tags\":" + candidate_tags_raw + ","
                  "\"selector_backend\":\"" + json_escape(selector_backend) + "\","
                  "\"flags\":\"" + json_escape(flags) + "\","
                  "\"menu_digest\":\"" + json_escape(menu_digest) + "\","
                  "\"menu_digest_fast\":\"" + json_escape(menu_digest_fast) + "\","
                  "\"state_digest\":\"" + json_escape(state_digest) + "\","
                  "\"state_digest_fast\":\"" + json_escape(state_digest_fast) + "\""
                  "}");

        // ControlMode plumbing
        Selection fallback = selector->select(menu, goal_context, state_digest, ControlMode::FALLBACK_ONLY, inputs);
        Selection policy = selector->select(menu, goal_context, state_digest, ControlMode::POLICY_ONLY, inputs);

        log.event(step, "selector_fallback_raw", "{\"raw\":\"" + json_escape(fallback.raw) + "\"}");
        log.event(step, "selector_policy_raw", "{\"raw\":\"" + json_escape(policy.raw) + "\"}");

        Selection picked;
        switch (mode) {
            case ControlMode::POLICY_ONLY:
                picked = policy; break;
            case ControlMode::BLENDED:
                picked = (policy.kind == Selection::Kind::INVALID) ? fallback : policy; break;
            case ControlMode::SHADOW_POLICY:
                picked = fallback; break;
            case ControlMode::FALLBACK_ONLY:
            default:
                picked = fallback; break;
        }

        std::string selector_path = "N/A";
        if (selector_backend == "GPU_CENTROID") {
            auto* psel = dynamic_cast<GpuCentroidSelector*>(selector.get());
            if (psel) selector_path = psel->last_backend();
        }

        log.event(step, "selector_chosen",
                  "{"
                  "\"control_mode\":\"" + json_escape(machina::json_mini::get_string(req, "control_mode").value_or("FALLBACK_ONLY")) + "\","
                  "\"selector_backend\":\"" + json_escape(selector_backend) + "\","
                  "\"selector_path\":\"" + json_escape(selector_path) + "\","
                  "\"raw\":\"" + json_escape(picked.raw) + "\""
                  "}");

        // Optional input patch from selector (safe: blocks _system/_queue/_meta keys)
        if (picked.kind == Selection::Kind::PICK && picked.input_patch_json) {
            std::string merged = safe_merge_patch(inputs, *picked.input_patch_json);
            log.event(step, "inputs_patched", "{\"patch\":" + *picked.input_patch_json + ",\"inputs\":" + merged + "}");
            inputs = merged;
        }

        if (picked.kind == Selection::Kind::INVALID) {
            invalid++;
            log.event(step, "invalid_pick", "{\"count\":" + std::to_string(invalid) + "}");
            if (invalid > budget.max_invalid_picks) {
                log.event(step, "breaker", "{\"reason\":\"max_invalid_picks\"}");
                std::cout << "RUN END: breaker(max_invalid_picks)\n";
                std::cout << "log: " << log.path() << "\n";
                return 1;
            }
            continue;
        }

        if (picked.kind == Selection::Kind::NOOP) {
            log.event(step, "noop", "{}");
            std::cout << "RUN END: noop\n";
            std::cout << "log: " << log.path() << "\n";
            return 0;
        }

        if (picked.kind == Selection::Kind::ASK_SUP) {
            Tx tx(state);
            std::string ask = "{\"question\":\"Need clarification\"}";
            auto t0 = std::chrono::steady_clock::now();
            auto res = runner.run("AID.ASK_SUP.v1", ask, tx.tmp());
            auto t1 = std::chrono::steady_clock::now();
            auto dur_ms = (int)std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count();
            if (res.status == StepStatus::OK) tx.commit(state);
            log.event(step, "ask_sup",
                      "{"
                      "\"status\":\"ok\","
                      "\"duration_ms\":" + std::to_string(dur_ms) + ","
                      "\"ds_digest\":\"" + json_escape(state.digest()) + "\","
                      "\"ds_digest_fast\":\"" + json_escape(state.digest_fast()) + "\","
                      "\"tx_patch\":" + tx.patch_json() +
                      "}");
            std::cout << "RUN END: ask_sup (stored)\n";
            std::cout << "log: " << log.path() << "\n";
            return 0;
        }

        // PICK
        const auto* mi = menu.resolve(*picked.sid);

        // ---- Phase 3: SID hallucination fallback (Layer 2) ----
        if (!mi) {
            invalid++;
            log.event(step, "invalid_pick", "{\"reason\":\"sid_not_in_menu\",\"sid\":\"" + json_escape(picked.sid->toString()) + "\",\"count\":" + std::to_string(invalid) + "}");
            if (invalid > budget.max_invalid_picks) {
                log.event(step, "breaker", "{\"reason\":\"max_invalid_picks\"}");
                std::cout << "RUN END: breaker(max_invalid_picks)\n";
                std::cout << "log: " << log.path() << "\n";
                return 1;
            }
            continue;
        }

        // Determinism hint + replay fences
        const auto* td = reg.getTool(mi->aid);
        bool det = true;
        if (td) det = td->deterministic;
        std::string replay_inputs_json = "{}";
        if (td && !td->replay_inputs.empty()) {
            replay_inputs_json = replay_inputs_to_json(*td, inputs, request_dir, root);
        }

        // Run tool in transaction
        Tx tx(state);
        auto t0 = std::chrono::steady_clock::now();
        auto tool_res = runner.run(mi->aid, inputs, tx.tmp());
        auto t1 = std::chrono::steady_clock::now();
        auto dur_ms = (int)std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count();
        if (tool_res.status == StepStatus::OK) {
            tx.commit(state);
            log.event(step, "tool_ok",
                      "{"
                      "\"aid\":\"" + json_escape(mi->aid) + "\","
                      "\"deterministic\":" + std::string(det ? "true" : "false") + ","
                      "\"duration_ms\":" + std::to_string(dur_ms) + ","
                      "\"replay_inputs\":" + replay_inputs_json + ","
                      "\"ds_digest\":\"" + json_escape(state.digest()) + "\","
                      "\"ds_digest_fast\":\"" + json_escape(state.digest_fast()) + "\","
                      "\"tx_patch\":" + tx.patch_json() +
                      "}");
        } else {
            // Capture compile error from tmp state before rollback invalidates it
            std::string compile_error_json;
            if (mi->aid == "AID.GENESIS.COMPILE_SHARED.v1") {
                auto ds7_it = tx.tmp().slots.find((uint8_t)DSSlot::DS7);
                if (ds7_it != tx.tmp().slots.end()) {
                    compile_error_json = ds7_it->second.content_json;
                }
            }
            tx.rollback();
            log.event(step, "tool_error",
                      "{"
                      "\"aid\":\"" + json_escape(mi->aid) + "\","
                      "\"deterministic\":" + std::string(det ? "true" : "false") + ","
                      "\"duration_ms\":" + std::to_string(dur_ms) + ","
                      "\"replay_inputs\":" + replay_inputs_json + ","
                      "\"err\":\"" + json_escape(tool_res.error) + "\""
                      "}");

            // ---- Phase 4: Genesis compile error retry ----
            if (mi->aid == "AID.GENESIS.COMPILE_SHARED.v1") {
                genesis_compile_retries++;
                if (genesis_compile_retries <= genesis_compile_retries_max) {
                    if (!compile_error_json.empty()) {
                        inputs = shallow_merge_json_objects(inputs,
                            "{\"_system_compile_error\":" + compile_error_json + "}");
                    }
                    log.event(step, "genesis_compile_retry",
                              "{"
                              "\"retry\":" + std::to_string(genesis_compile_retries) + ","
                              "\"max\":" + std::to_string(genesis_compile_retries_max) +
                              "}");
                    continue;
                }
                // Exhausted retries — fall through to error handling below.
            }

            // Special-case: missing tool can be repaired by Genesis in-run.
            const std::string missing_prefix = "MISSING_TOOL: ";
            const bool is_missing_tool = (tool_res.error.rfind(missing_prefix, 0) == 0);
            if (is_missing_tool && auto_genesis_on_missing) {
                std::string missing_aid = tool_res.error.substr(missing_prefix.size());
                {
                    Tx tdx(state);
                    Artifact a;
                    a.type = "system_diag";
                    a.provenance = "runner";
                    a.content_json = std::string("{")
                        + "\"stage\":\"MISSING_TOOL\","
                        + "\"missing_aid\":\"" + json_escape(missing_aid) + "\","
                        + "\"at_step\":" + std::to_string(step) + ","
                        + "\"ts_ms\":" + std::to_string(now_ms_i64())
                        + "}";
                    a.size_bytes = a.content_json.size();
                    tdx.tmp().slots[(uint8_t)DSSlot::DS6] = a;
                    tdx.commit(state);
                }

                if (auto_genesis_autostub && autostub_done.find(missing_aid) == autostub_done.end()) {
                    autostub_done.insert(missing_aid);
                    auto hex64 = [&](uint64_t v) {
                        std::ostringstream oss;
                        oss << std::hex;
                        oss.width(16);
                        oss.fill('0');
                        oss << v;
                        return oss.str();
                    };
                    uint64_t h = machina::hash::fnv1a64(missing_aid);
                    std::string base = std::string("autostub_") + hex64(h);
                    std::string rel_cpp = base + ".cpp";
                    std::string out_name = base;

                    std::ostringstream src;
                    src << "#include \"machina/plugin_api.h\"\n";
                    src << "#include \"machina/json_mini.h\"\n";
                    src << "#include \"machina/state.h\"\n\n";
                    src << "namespace {\n";
                    src << "machina::ToolResult stub_tool(const std::string& input_json, machina::DSState& ds_tmp) {\n";
                    src << "  (void)input_json; (void)ds_tmp;\n";
                    src << "  std::string out = std::string(\"{\")\n";
                    src << "    + \"\\\"ok\\\":true,\"\n";
                    src << "    + \"\\\"autostub\\\":true,\"\n";
                    src << "    + \"\\\"note\\\":\\\"not implemented\\\"\"\n";
                    src << "    + \"}\";\n";
                    src << "  return {machina::StepStatus::OK, out, \"\"};\n";
                    src << "}\n";
                    src << "} // namespace\n\n";
                    src << "extern \"C\" void machina_plugin_init(machina::IToolRegistrar* host) {\n";
                    src << "  machina::ToolDesc d;\n";
                    src << "  d.aid = \"" << json_escape(missing_aid) << "\";\n";
                    src << "  d.name = \"" << json_escape(base) << "\";\n";
                    src << "  d.deterministic = true;\n";
                    src << "  d.tags = {\"tag.runtime\",\"tag.meta\",\"tag.autostub\"};\n";
                    src << "  d.side_effects = {\"none\"};\n";
                    src << "  host->register_tool(d, &stub_tool);\n";
                    src << "}\n";

                    auto jq = [&](const std::string& x){ return std::string("\"") + json_escape(x) + "\""; };

                    // 1) write
                    {
                        Tx gx(state);
                        std::string jin = std::string("{")
                            + "\"relative_path\":" + jq(rel_cpp) + ","
                            + "\"content\":" + jq(src.str()) + ","
                            + "\"overwrite\":true"
                            + "}";
                        auto r = runner.run("AID.GENESIS.WRITE_FILE.v1", jin, gx.tmp());
                        if (r.status == StepStatus::OK) gx.commit(state);
                        log.event(step, "genesis_autostub_write", std::string("{\"ok\":") + (r.status==StepStatus::OK?"true":"false") + ",\"err\":\"" + json_escape(r.error) + "\"}");
                    }
                    // 2) compile
                    {
                        Tx gx(state);
                        std::string jin = std::string("{")
                            + "\"src_relative_path\":" + jq(rel_cpp) + ","
                            + "\"out_name\":" + jq(out_name)
                            + "}";
                        auto r = runner.run("AID.GENESIS.COMPILE_SHARED.v1", jin, gx.tmp());
                        if (r.status == StepStatus::OK) gx.commit(state);
                        log.event(step, "genesis_autostub_compile", std::string("{\"ok\":") + (r.status==StepStatus::OK?"true":"false") + ",\"err\":\"" + json_escape(r.error) + "\"}");
                    }
                    // 3) load
                    {
                        Tx gx(state);
                        std::string jin = std::string("{")
                            + "\"out_name\":" + jq(out_name)
                            + "}";
                        auto r = runner.run("AID.GENESIS.LOAD_PLUGIN.v1", jin, gx.tmp());
                        if (r.status == StepStatus::OK) gx.commit(state);
                        log.event(step, "genesis_autostub_load", std::string("{\"ok\":") + (r.status==StepStatus::OK?"true":"false") + ",\"err\":\"" + json_escape(r.error) + "\"}");
                    }
                }

                continue;
            }

            std::cout << "RUN END: tool_error aid=" << mi->aid << " err=" << tool_res.error << "\n";
            std::cout << "log: " << log.path() << "\n";
            return 1;
        }

        // ---- Phase 2: GoalRegistry-based completion check ----
        if (goal_reg.isGoalComplete(goal_id, state)) {
            log.event(step, "goal_done", "{\"goal_id\":\"" + json_escape(goal_id) + "\"}");
            std::cout << "RUN END: goal_done " << goal_id << "\n";
            // Print all occupied slot artifacts
            for (const auto& kv : state.slots) {
                std::cout << "DS" << (int)kv.first << " artifact: " << kv.second.content_json << "\n";
            }
            std::cout << "log: " << log.path() << "\n";
            return 0;
        }

        // Continue to next step
    }

    log.event(budget.max_steps, "breaker", "{\"reason\":\"max_steps\"}");
    std::cout << "RUN END: breaker(max_steps)\n";
    std::cout << "log: " << log.path() << "\n";
    return 1;
}
