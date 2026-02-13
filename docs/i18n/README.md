# Multilingual Equivalent Documentation Sets

This directory provides language-specific **equivalent documentation sets** that map to the full English documentation scope.

## Scope policy

- English files remain the canonical technical contract.
- Each locale set documents equivalent operational intent, safety requirements, command usage, and release checks.
- If wording differs, follow English source for implementation-level decisions.

## Covered English source set (19 files)

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

## Available equivalent sets

| Locale | File |
|---|---|
| Korean (`ko-KR`) | `docs/i18n/ko/EQUIVALENT_DOCSET.md` |
| Japanese (`ja-JP`) | `docs/i18n/ja/EQUIVALENT_DOCSET.md` |
| Simplified Chinese (`zh-Hans-CN`) | `docs/i18n/zh-CN/EQUIVALENT_DOCSET.md` |
| Traditional Chinese (`zh-Hant-TW`) | `docs/i18n/zh-TW/EQUIVALENT_DOCSET.md` |
| Spanish (`es`) | `docs/i18n/es/EQUIVALENT_DOCSET.md` |
| Portuguese Brazil (`pt-BR`) | `docs/i18n/pt-BR/EQUIVALENT_DOCSET.md` |
| French (`fr-FR`) | `docs/i18n/fr/EQUIVALENT_DOCSET.md` |
| German (`de-DE`) | `docs/i18n/de/EQUIVALENT_DOCSET.md` |
| Vietnamese (`vi-VN`) | `docs/i18n/vi/EQUIVALENT_DOCSET.md` |
| Indonesian (`id-ID`) | `docs/i18n/id/EQUIVALENT_DOCSET.md` |
| Thai (`th-TH`) | `docs/i18n/th/EQUIVALENT_DOCSET.md` |
| Russian (`ru-RU`) | `docs/i18n/ru/EQUIVALENT_DOCSET.md` |
| Arabic (`ar-SA`) | `docs/i18n/ar/EQUIVALENT_DOCSET.md` |
| Hindi (`hi-IN`) | `docs/i18n/hi/EQUIVALENT_DOCSET.md` |

## Maintenance rule

Whenever English source docs are updated, update impacted locale sets in the same release window.
