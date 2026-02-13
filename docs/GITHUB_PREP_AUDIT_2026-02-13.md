# GitHub Upload Readiness Audit Report (2026-02-13)

## 1. Audit goals

- Select only files required for public upload based on real runtime flow
- Remove duplicate README paths (`README.md` vs legacy duplicates)
- Verify practical usefulness of docs (`*.md`) and root Python modules (`*.py`)

## 2. Runtime flow summary

### 2.1 C++ execution flow

- Entry point: `runner/main.cpp`
- Dispatch modes: `run | chat | replay | replay_strict | cts | autopilot | serve | tool_exec`
- Core runtime paths:
1. `runner/cmd_run.cpp`: goal loop, selector, tx/log, plugin reload
2. `runner/cmd_serve.cpp`: HTTP queue, workers, WAL/checkpoint, dedup
3. `runner/cmd_chat.cpp`: chat intent parsing and action execution

### 2.2 Python execution flow

- Main entry point: `telegram_bot.py` (`if __name__ == "__main__"`)
- Message path: `telegram_bot_pulse.py` -> `policies/chat_driver.py` -> `machina_dispatch.execute_intent()`
- Execution/permission/tool layers:
1. `machina_dispatch.py` (facade)
2. `machina_dispatch_exec.py` (execution branches)
3. `machina_dispatch_registry.py` (AID/alias registry)
4. `machina_permissions.py` (permission policy)

## 3. Root Python necessity assessment

### 3.1 Required for upload (runtime core)

- `machina_config.py`
- `machina_shared.py`
- `machina_dispatch.py`
- `machina_dispatch_exec.py`
- `machina_dispatch_registry.py`
- `machina_permissions.py`
- `machina_tools.py`
- `machina_tools_fileops.py`
- `machina_learning.py`
- `machina_learning_memory.py`
- `machina_graph.py`
- `machina_graph_memory.py`
- `machina_gvu.py`
- `machina_gvu_tracker.py`
- `machina_mcp.py`
- `machina_mcp_connection.py`
- `telegram_bot.py`
- `telegram_bot_handlers.py`
- `telegram_bot_pulse.py`
- `telegram_commands.py`
- `telegram_commands_ext.py`
- `machina_reindex.py` (operations verification CLI)

### 3.2 Recommended for upload (test/governance support)

- `machina_brain_orchestrator.py`
- `machina_evolution_governor.py`
- `machina_evolution_policy.py`

Reasoning:
- These three files are not hard dependencies of the primary production path,
  but they are used in governance tests (`tests/test_evolution_governance.py`)
  and policy experimentation paths.
- Keeping them is safer than removing them.

## 4. Documentation usefulness assessment

### 4.1 Keep in public repository

- `README.md`
- `docs/QUICKSTART.md`
- `docs/ARCHITECTURE.md`
- `docs/OPERATIONS.md`
- `docs/SERVE_API.md`
- `docs/LLM_BACKENDS.md`
- `docs/POLICY_DRIVER.md`
- `docs/ROADMAP.md`
- `docs/ipc_schema.md`
- `MACHINA_LLM_SETUP_GUIDE.md`
- `MACHINA_TEST_CATALOG.md`

### 4.2 Internal-record documents (optional for public root)

- `docs/REPO_AUDIT_2026-02-13.md`
- `docs/PROJECT_DEEP_DIVE_2026-02-13.md`
- `docs/CLAUDE_TO_CODEX_SKILL_PLAN.md`

Reasoning:
- These are date/session-specific internal notes.
- They are not required for external onboarding or normal usage.

## 5. Clean package policy for GitHub upload

### 5.1 Include

- Source: `core/`, `runner/`, `toolhost/`, `tools/`, `machina_autonomic/`, `policies/`
- Runtime Python: root `machina_*.py`, `telegram_*.py`
- Config/schema/examples/tests: `toolpacks/`, `goalpacks/`, `schemas/`, `examples/`, `tests/`, `scripts/`
- Metadata: `.github/`, `CMakeLists.txt`, `.gitignore`, `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `.secrets.env.example`, `mcp_servers.json`
- Docs: all items in section 4.1

### 5.2 Exclude

- Runtime/build artifacts: `build/`, `logs/`, `work/`, `__pycache__/`
- Local env files: `machina_env.sh`, `.env*`, `.secrets.env`
- Runtime ops dumps: `ops/*`
- Duplicate README variants
- Internal-record documents listed in section 4.2

## 6. Conclusion

- Most root `*.py` files are on real dependency paths and should stay.
- Removing duplicate README files and internal-only notes improves public repo readability.
- Building and publishing from `github_ready/` is the correct approach for immediate upload.
