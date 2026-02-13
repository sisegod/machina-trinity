# Эквивалентный Набор Документации (Русский)

Этот документ предоставляет эквивалентную версию для русскоязычных пользователей и покрывает **весь объем** англоязычной технической документации.

## 1) Полный охват

Покрываются 19 исходных документов:

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

## 2) Критические правила эксплуатации

- Всегда предполагать, что LLM может ошибаться.
- Выполнять инструменты транзакционно; при ошибке делать rollback.
- Сохранять проверяемый аудит через hash-chain.
- Не хранить секреты в репозитории.

## 3) Минимальная проверка перед публикацией

```bash
scripts/run_guardrails.sh
python3 scripts/validate_docs_refs.py
python3 scripts/security_guardrails.py
cd build && ctest --output-on-failure
```

## 4) Контракт Policy Driver

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
```

Ожидаемый формат ответа:

```text
<PICK><SID0004><END>
```

## 5) Языковой статус и план

- Сейчас Telegram/Pulse в первую очередь оптимизирован под `ko-KR`.
- Ближайший шаг: усиление сценариев `en`, затем расширение локалей.
- Принцип: единая кодовая база + пакеты ресурсов по локалям.

## 6) Правила сопровождения

- Синхронизировать при каждом изменении английских источников.
- Не переводить команды, env-переменные и AID.
- При расхождениях опираться на английский текст как канонический.
