# Äquivalentes Dokumentationsset (Deutsch)

Dieses Dokument stellt eine äquivalente Fassung für deutschsprachige Nutzer bereit und deckt den **gesamten Umfang** der englischen Technikdokumentation ab.

## 1) Vollständiger Umfang

Abgedeckte 19 Quelldokumente:

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

## 2) Kritische Betriebsregeln

- Davon ausgehen, dass das LLM fehlschlagen kann.
- Tools transaktional ausführen; bei Fehlern rollback.
- Nachvollziehbarkeit über Hash-Chain-Auditlogs sicherstellen.
- Keine Secrets im Repository speichern.

## 3) Pflicht-Validierung vor Veröffentlichung

```bash
scripts/run_guardrails.sh
python3 scripts/validate_docs_refs.py
python3 scripts/security_guardrails.py
cd build && ctest --output-on-failure
```

## 4) Policy-Driver-Vertrag

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
```

Erwartete Ausgabe:

```text
<PICK><SID0004><END>
```

## 5) Sprachstatus und Ausbau

- Aktuell: Telegram/Pulse ist primär für `ko-KR` optimiert.
- Nächster Schritt: `en` stärken, danach weitere Locale-Packs.
- Prinzip: eine Codebasis, keine langfristigen Sprach-Forks.

## 6) Wartungsregeln

- Dieses Set bei Änderungen der englischen Quellen mitpflegen.
- Befehle, Env-Variablen, AIDs nicht übersetzen.
- Bei Unklarheit gilt der englische Quelltext als verbindlich.
