# Jeu de Documentation Équivalente en Français

Ce document fournit une version équivalente pour les utilisateurs francophones et couvre **l'ensemble** des documents techniques en anglais.

## 1) Périmètre couvert

Ce jeu couvre les 19 documents sources:

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

## 2) Règles opérationnelles critiques

- Considérer que le LLM peut échouer.
- Exécuter les outils en transaction; rollback en cas d'échec.
- Conserver la traçabilité via logs JSONL chaînés par hash.
- Ne pas stocker de secrets dans le dépôt.

## 3) Validation minimale avant publication

```bash
scripts/run_guardrails.sh
python3 scripts/validate_docs_refs.py
python3 scripts/security_guardrails.py
cd build && ctest --output-on-failure
```

## 4) Contrat Policy Driver

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
```

Sortie attendue:

```text
<PICK><SID0004><END>
```

## 5) État linguistique et feuille de route

- Actuel: Telegram/Pulse est optimisé surtout pour `ko-KR`.
- Court terme: renforcer `en`, puis étendre les packs multilingues.
- Principe: base de code unique + ressources par locale.

## 6) Maintenance

- Synchroniser ce jeu à chaque changement des sources anglaises.
- Ne pas traduire noms de commandes, variables, ni AID.
- En cas d'ambiguïté, la source normative reste l'anglais.
