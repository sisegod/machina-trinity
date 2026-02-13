# Conjunto de Documentación Equivalente en Español

Este documento ofrece una versión equivalente para usuarios hispanohablantes, cubriendo **todo el alcance** de la documentación técnica en inglés.

## 1) Cobertura completa

Este conjunto cubre el contenido operativo y de arquitectura de los siguientes 19 documentos fuente:

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

## 2) Reglas operativas no negociables

- Diseñar con la premisa de que el LLM puede fallar.
- Usar ejecución transaccional; ante fallo, rollback completo.
- Mantener trazabilidad con logs JSONL encadenados por hash.
- No guardar secretos en el repositorio.

## 3) Validación mínima antes de publicar

```bash
scripts/run_guardrails.sh
python3 scripts/validate_docs_refs.py
python3 scripts/security_guardrails.py
cd build && ctest --output-on-failure
```

## 4) Contrato de Policy Driver

Si usas drivers de `examples/policy_drivers`:

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
```

La salida debe cumplir el contrato selector, por ejemplo:

```text
<PICK><SID0004><END>
```

## 5) Estado de idioma y plan

- Estado actual: Telegram/Pulse optimizado principalmente para `ko-KR`.
- Objetivo cercano: mejorar experiencia `en` y habilitar recursos `es`.
- Estrategia: una sola base de código + paquetes de recursos por locale (sin forks largos).

## 6) Reglas de mantenimiento

- Al cambiar documentos fuente en inglés, actualizar este set en la misma versión.
- No traducir nombres de comandos, variables de entorno ni AID.
- Si hay conflicto semántico, la referencia normativa es la documentación en inglés.
