# Conjunto de Documentação Equivalente em Português (Brasil)

Este documento fornece uma versão equivalente para usuários lusófonos, cobrindo **todo o escopo** da documentação técnica em inglês.

## 1) Cobertura total

A cobertura inclui os 19 documentos-fonte:

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

## 2) Regras operacionais essenciais

- Assumir que o LLM pode falhar.
- Executar ferramentas com transação; falhou, rollback.
- Preservar trilha de auditoria com hash-chain.
- Não versionar segredos no repositório.

## 3) Verificação obrigatória antes de publicar

```bash
scripts/run_guardrails.sh
python3 scripts/validate_docs_refs.py
python3 scripts/security_guardrails.py
cd build && ctest --output-on-failure
```

## 4) Contrato de Policy Driver

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
```

Formato esperado de saída:

```text
<PICK><SID0004><END>
```

## 5) Situação de idioma e expansão

- Hoje: Telegram/Pulse prioriza `ko-KR`.
- Próximo passo: reforçar UX em `en` e preparar recursos `pt-BR`.
- Princípio: uma base de código única + pacotes de locale.

## 6) Política de manutenção

- Atualizar este conjunto quando os documentos em inglês mudarem.
- Não traduzir nomes técnicos (AID, variáveis, comandos).
- Em divergência, seguir o texto em inglês como contrato canônico.
