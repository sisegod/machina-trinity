# 繁體中文等價文件集

本文件為繁體中文使用者提供與英文技術文件**等價**的資訊覆蓋。

## 1) 完整覆蓋範圍

涵蓋以下 19 份英文來源文件：

- `README.md`
- `CODE_OF_CONDUCT.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `GITHUB_UPLOAD_CHECKLIST.md`
- `MACHINA_LLM_SETUP_GUIDE.md`
- `MACHINA_TEST_CATALOG.md`
- `docs/QUICKSTART.md`
- `docs/ARCHITECTURE.md`
- `docs/OPERATIONS.md`
- `docs/SERVE_API.md`
- `docs/LLM_BACKENDS.md`
- `docs/POLICY_DRIVER.md`
- `docs/LANGUAGE_STRATEGY_EN.md`
- `docs/ROADMAP.md`
- `docs/ipc_schema.md`
- `docs/GITHUB_PREP_AUDIT_2026-02-13.md`
- `examples/policy_drivers/README.md`
- `toolpacks/runtime_genesis/src/self_test_calc/README.md`

## 2) 核心運維規則

- 預設 LLM 可能失誤。
- 工具執行採交易模式；失敗即 rollback。
- 稽核日誌採 hash-chain，確保可追溯。
- 機密資訊不得提交至 repo。

## 3) 發佈前最小驗證

```bash
scripts/run_guardrails.sh
python3 scripts/validate_docs_refs.py
python3 scripts/security_guardrails.py
cd build && ctest --output-on-failure
```

## 4) Policy Driver 契約

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
```

輸出範例：

```text
<PICK><SID0004><END>
```

## 5) 語言現況與擴展

- 目前 Telegram/Pulse 主要針對 `ko-KR` 優化。
- 近期重點：強化 `en` 體驗，再擴展多語 locale。
- 原則：單一程式碼基線 + locale 資源包。

## 6) 維護規範

- 英文來源更新時，同版同步更新本等價文件。
- 指令、環境變數、AID 名稱不翻譯。
- 若語義衝突，以英文文件為準。
