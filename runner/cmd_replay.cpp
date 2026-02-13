#include "cmd_replay.h"
#include "runner_utils.h"
#include "tool_setup.h"

#include "machina/types.h"
#include "machina/registry.h"
#include "machina/ids.h"
#include "machina/selector.h"
#include "machina/selector_gpu.h"
#include "machina/state.h"
#include "machina/serialization.h"
#include "machina/tx.h"
#include "machina/tools.h"
#include "machina/json_mini.h"

#include <json-c/json.h>

#include <algorithm>
#include <chrono>
#include <iostream>
#include <map>

using namespace machina;

int cmd_replay(int argc, char** argv) {
    if (argc < 3) {
        std::cerr << "usage: machina_cli replay <run_log.jsonl>\n";
        std::cerr << "tip: use replay_strict for deterministic re-execution.\n";
        return 2;
    }
    std::string logp = argv[2];
    std::ifstream f(logp);
    if (!f) { std::cerr << "cannot open log: " << logp << "\n"; return 2; }

    bool has_menu=false, has_pick=false;
    std::string line;
    while (std::getline(f, line)) {
        if (line.find("\"event\":\"menu_built\"") != std::string::npos) has_menu = true;
        if (line.find("\"event\":\"selector_chosen\"") != std::string::npos) has_pick = true;
    }
    if (!has_menu || !has_pick) {
        std::cout << "REPLAY FAIL: missing required events\n";
        return 1;
    }
    std::cout << "REPLAY OK (RC1 structural).\n";
    return 0;
}

int cmd_replay_strict(int argc, char** argv) {
    if (argc < 4) {
        std::cerr << "usage: machina_cli replay_strict <run_request.json> <run_log.jsonl>\n";
        return 2;
    }

    const auto root = resolve_root(argv[0]);

    std::filesystem::path req_path = std::filesystem::path(argv[2]);
    if (!req_path.is_absolute()) req_path = std::filesystem::absolute(req_path);
    std::filesystem::path request_dir = req_path.parent_path();
    set_env_if_missing("MACHINA_ROOT", root.string());
    set_env_if_missing("MACHINA_REQUEST_DIR", request_dir.string());
    std::string req = slurp(req_path.string());
    std::string goal_id = machina::json_mini::get_string(req, "goal_id").value_or("");
    auto base_tags = machina::json_mini::get_array_strings(req, "candidate_tags");
    if (std::find(base_tags.begin(), base_tags.end(), "tag.meta") == base_tags.end()) base_tags.push_back("tag.meta");
    std::string inputs = machina::json_mini::get_object_raw(req, "inputs").value_or("{}");
    auto base_mode = parse_mode(machina::json_mini::get_string(req, "control_mode").value_or("FALLBACK_ONLY"));
    if (goal_id.empty()) {
        std::cerr << "run_request missing goal_id\n";
        return 2;
    }

    struct StepLog {
        bool has_menu = false;
        bool has_choice = false;
        bool has_inputs_patched = false;
        bool has_tool_ok = false;
        bool has_ask_sup = false;
        bool has_noop = false;
        bool has_goal_done = false;

        std::string menu_digest;
        std::string state_digest;
        std::string raw;
        std::string control_mode;
        std::string selector_backend;

        std::string patched_inputs;

        std::string tool_ok_aid;
        std::string tool_ok_ds_digest;
        std::string tool_ok_tx_patch;
        std::string tool_ok_replay_input_path_fingerprint;
        std::string tool_ok_replay_gpu_signature;
        bool tool_ok_det = true;

        std::string ask_ds_digest;
    };

    std::string logp = argv[3];
    std::ifstream f(logp);
    if (!f) { std::cerr << "cannot open log: " << logp << "\n"; return 2; }

    std::map<int, StepLog> steps;

    std::string line;
    while (std::getline(f, line)) {
        int step = (int)machina::json_mini::get_int(line, "step").value_or(-1);
        if (step < 0) continue;
        auto ev = machina::json_mini::get_string(line, "event").value_or("");
        auto payload = machina::json_mini::get_object_raw(line, "payload").value_or("{}");

        StepLog& s = steps[step];
        if (ev == "menu_built") {
            s.has_menu = true;
            s.menu_digest = machina::json_mini::get_string(payload, "menu_digest").value_or("");
            s.state_digest = machina::json_mini::get_string(payload, "state_digest").value_or("");
        } else if (ev == "selector_chosen") {
            s.has_choice = true;
            s.raw = machina::json_mini::get_string(payload, "raw").value_or("");
            s.control_mode = machina::json_mini::get_string(payload, "control_mode").value_or("");
            s.selector_backend = machina::json_mini::get_string(payload, "selector_backend").value_or("");
        } else if (ev == "inputs_patched") {
            s.has_inputs_patched = true;
            s.patched_inputs = machina::json_mini::get_object_raw(payload, "inputs").value_or("{}");
        } else if (ev == "tool_ok") {
            s.has_tool_ok = true;
            s.tool_ok_aid = machina::json_mini::get_string(payload, "aid").value_or("");
            s.tool_ok_ds_digest = machina::json_mini::get_string(payload, "ds_digest").value_or("");
            s.tool_ok_tx_patch = machina::json_mini::get_array_raw(payload, "tx_patch").value_or("[]");
            auto rip = machina::json_mini::get_object_raw(payload, "replay_inputs").value_or("{}");
            s.tool_ok_replay_input_path_fingerprint = machina::json_mini::get_string(rip, "input_path_fingerprint").value_or("");
            s.tool_ok_replay_gpu_signature = machina::json_mini::get_string(rip, "gpu_signature").value_or("");
            s.tool_ok_det = machina::json_mini::get_bool(payload, "deterministic").value_or(true);
        } else if (ev == "ask_sup") {
            s.has_ask_sup = true;
            s.ask_ds_digest = machina::json_mini::get_string(payload, "ds_digest").value_or("");
        } else if (ev == "noop") {
            s.has_noop = true;
        } else if (ev == "goal_done") {
            s.has_goal_done = true;
        }
    }

    if (steps.empty()) {
        std::cerr << "replay_strict: no step events found\n";
        return 1;
    }

    std::string selector_backend = "HEURISTIC";
    if (steps.count(0) && steps[0].has_choice && !steps[0].selector_backend.empty()) {
        selector_backend = steps[0].selector_backend;
    } else {
        for (auto& kv : steps) {
            if (kv.second.has_choice && !kv.second.selector_backend.empty()) {
                selector_backend = kv.second.selector_backend;
                break;
            }
        }
    }

    Registry reg;
    ToolRunner runner;
    PluginManager plugin_mgr;
    RunnerRegistrar registrar{reg, runner, false};
    setup_runtime(reg, runner, plugin_mgr, registrar, root);

    auto selector = make_selector(selector_backend, root);

    DSState state;

    auto has_slot = [&](DSSlot s) -> bool {
        return state.slots.find((uint8_t)s) != state.slots.end();
    };

    auto dedup_tags = [&](std::vector<std::string> t) {
        std::sort(t.begin(), t.end());
        t.erase(std::unique(t.begin(), t.end()), t.end());
        return t;
    };

    for (const auto& kv : steps) {
        const int step = kv.first;
        const StepLog& slog = kv.second;

        const bool has_structural =
            slog.has_menu || slog.has_choice || slog.has_tool_ok ||
            slog.has_ask_sup || slog.has_noop || slog.has_goal_done ||
            slog.has_inputs_patched;
        if (!has_structural) {
            // Ignore non-step events that reuse the "step" field (e.g. breaker).
            continue;
        }

        if (!slog.has_menu || !slog.has_choice) {
            std::cerr << "REPLAY_STRICT FAIL: missing required events at step " << step << "\n";
            return 1;
        }

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

        {
            std::string perr;
            (void)plugin_mgr.load_new_from_dir(root / "toolpacks" / "runtime_plugins", &registrar, &perr);
        }

        std::vector<std::string> step_tags = base_tags;
        if (goal_id.rfind("goal.GENESIS", 0) == 0) {
            step_tags.push_back("tag.genesis");
            step_tags.push_back("tag.runtime");
            step_tags.push_back("tag.meta");
        }
        if (ds0 && !ds2) step_tags.push_back("tag.report");
        step_tags = dedup_tags(std::move(step_tags));

        Menu menu = build_menu_from_registry(reg, step_tags);

        std::string menu_digest = menu.digest();
        std::string state_digest = state.digest();

        if (!slog.menu_digest.empty() && menu_digest != slog.menu_digest) {
            std::cerr << "REPLAY_STRICT FAIL: menu_digest mismatch at step " << step << "\n";
            std::cerr << "  logged: " << slog.menu_digest << "\n";
            std::cerr << "  calc : " << menu_digest << "\n";
            return 1;
        }
        if (!slog.state_digest.empty() && state_digest != slog.state_digest) {
            std::cerr << "REPLAY_STRICT FAIL: state_digest mismatch at step " << step << "\n";
            std::cerr << "  logged: " << slog.state_digest << "\n";
            std::cerr << "  calc : " << state_digest << "\n";
            return 1;
        }

        std::string flags = std::string("FLAGS:DS0=") + (ds0 ? "1" : "0") +
                            ";DS2=" + (ds2 ? "1" : "0") +
                            ";DS6=" + (ds6 ? "1" : "0") +
                            ";DS7=" + (ds7 ? "1" : "0") +
                            ";DS6_STAGE=" + ds6_stage;
        std::string goal_context = goal_id + "|" + menu_digest + "|" + flags;

        Selection fallback = selector->select(menu, goal_context, state_digest, ControlMode::FALLBACK_ONLY, inputs);
        Selection policy = selector->select(menu, goal_context, state_digest, ControlMode::POLICY_ONLY, inputs);

        auto mode_local = base_mode;
        if (!slog.control_mode.empty()) mode_local = parse_mode(slog.control_mode);

        Selection picked;
        switch (mode_local) {
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

        if (picked.raw != slog.raw) {
            std::cerr << "REPLAY_STRICT FAIL: choice mismatch at step " << step << "\n";
            std::cerr << "  logged: " << slog.raw << "\n";
            std::cerr << "  calc : " << picked.raw << "\n";
            return 1;
        }

        if (slog.has_inputs_patched) {
            inputs = slog.patched_inputs.empty() ? std::string("{}") : slog.patched_inputs;
        }

        if (slog.has_tool_ok) {
            if (picked.kind != Selection::Kind::PICK || !picked.sid) {
                std::cerr << "REPLAY_STRICT FAIL: tool_ok but selection is not PICK at step " << step << "\n";
                return 1;
            }
            const auto* mi = menu.resolve(*picked.sid);
            if (!mi) {
                std::cerr << "REPLAY_STRICT FAIL: sid not in menu at step " << step << "\n";
                return 1;
            }
            if (!slog.tool_ok_aid.empty() && mi->aid != slog.tool_ok_aid) {
                std::cerr << "REPLAY_STRICT FAIL: aid mismatch at step " << step << "\n";
                std::cerr << "  logged: " << slog.tool_ok_aid << "\n";
                std::cerr << "  calc : " << mi->aid << "\n";
                return 1;
            }

            bool det = true;
            if (const auto* td_desc = reg.getTool(mi->aid)) det = td_desc->deterministic;

            if (!det || !slog.tool_ok_det) {
                // Non-deterministic tool: skip re-execution, inject logged state
                std::cerr << "replay_strict: skipping non-deterministic tool " << mi->aid << " at step " << step << "\n";
                if (!slog.tool_ok_tx_patch.empty()) {
                    json_object* patch = json_tokener_parse(slog.tool_ok_tx_patch.c_str());
                    if (!patch || !dsstate_apply_tx_patch(patch, &state)) {
                        if (patch) json_object_put(patch);
                        std::cerr << "REPLAY_STRICT FAIL: cannot apply logged tx_patch at step " << step << "\n";
                        return 1;
                    }
                    json_object_put(patch);
                }
            } else if (det && slog.tool_ok_det) {
                if (const auto* tdesc = reg.getTool(mi->aid)) {
                    if (!tdesc->replay_inputs.empty()) {
                        auto cur = compute_replay_inputs(*tdesc, inputs, request_dir, root);
                        if (!slog.tool_ok_replay_input_path_fingerprint.empty()) {
                            auto it = cur.find("input_path_fingerprint");
                            if (it == cur.end() || it->second != slog.tool_ok_replay_input_path_fingerprint) {
                                std::cerr << "REPLAY_STRICT FAIL: input_path_fingerprint mismatch at step " << step << "\n";
                                std::cerr << "  logged: " << slog.tool_ok_replay_input_path_fingerprint << "\n";
                                std::cerr << "  calc : " << (it == cur.end() ? std::string("(missing)") : it->second) << "\n";
                                return 1;
                            }
                        }
                        if (!slog.tool_ok_replay_gpu_signature.empty()) {
                            auto it = cur.find("gpu_signature");
                            if (it == cur.end() || it->second != slog.tool_ok_replay_gpu_signature) {
                                std::cerr << "REPLAY_STRICT FAIL: gpu_signature mismatch at step " << step << "\n";
                                std::cerr << "  logged: " << slog.tool_ok_replay_gpu_signature << "\n";
                                std::cerr << "  calc : " << (it == cur.end() ? std::string("(missing)") : it->second) << "\n";
                                return 1;
                            }
                        }
                    }
                }

                Tx tx(state);
                auto tool_res = runner.run(mi->aid, inputs, tx.tmp());
                if (tool_res.status != StepStatus::OK) {
                    std::cerr << "REPLAY_STRICT FAIL: tool re-exec failed at step " << step << ": " << tool_res.error << "\n";
                    return 1;
                }
                tx.commit(state);
                if (!slog.tool_ok_tx_patch.empty() && tx.patch_json() != slog.tool_ok_tx_patch) {
                    std::cerr << "REPLAY_STRICT FAIL: tx_patch mismatch after tool at step " << step << "\n";
                    std::cerr << "  logged: " << slog.tool_ok_tx_patch << "\n";
                    std::cerr << "  calc : " << tx.patch_json() << "\n";
                    return 1;
                }
                std::string ds = state.digest();
                if (!slog.tool_ok_ds_digest.empty() && ds != slog.tool_ok_ds_digest) {
                    std::cerr << "REPLAY_STRICT FAIL: ds_digest mismatch after tool at step " << step << "\n";
                    std::cerr << "  logged: " << slog.tool_ok_ds_digest << "\n";
                    std::cerr << "  calc : " << ds << "\n";
                    return 1;
                }
            }
        }

        if (slog.has_ask_sup) {
            if (picked.kind != Selection::Kind::ASK_SUP) {
                std::cerr << "REPLAY_STRICT FAIL: ask_sup event but selection is not ASK_SUP at step " << step << "\n";
                return 1;
            }
            Tx tx(state);
            std::string ask = "{\"question\":\"Need clarification\"}";
            auto t0 = std::chrono::steady_clock::now();
            auto res = runner.run("AID.ASK_SUP.v1", ask, tx.tmp());
            auto t1 = std::chrono::steady_clock::now();
            (void)t0; (void)t1;
            if (res.status == StepStatus::OK) tx.commit(state);
            if (!slog.ask_ds_digest.empty() && state.digest() != slog.ask_ds_digest) {
                std::cerr << "REPLAY_STRICT FAIL: ask_sup ds_digest mismatch at step " << step << "\n";
                std::cerr << "  logged: " << slog.ask_ds_digest << "\n";
                std::cerr << "  calc : " << state.digest() << "\n";
                return 1;
            }
            std::cout << "REPLAY_STRICT OK (terminated by ask_sup).\n";
            return 0;
        }

        if (slog.has_noop) {
            std::cout << "REPLAY_STRICT OK (terminated by noop).\n";
            return 0;
        }

        if (slog.has_goal_done) {
            std::cout << "REPLAY_STRICT OK (goal_done).\n";
            return 0;
        }
    }

    std::cout << "REPLAY_STRICT OK.\n";
    return 0;
}
