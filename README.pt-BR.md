# Machina Trinity (Português Brasil)

Runtime de agentes autônomos com núcleo de segurança em C++ e orquestração em Python.

## Documentação

- Canonical (English): `README.md`
- Conjunto equivalente em PT-BR: `docs/i18n/pt-BR/EQUIVALENT_DOCSET.md`
- Estratégia de idioma: `docs/LANGUAGE_STRATEGY_EN.md`

## Início rápido

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
cd build && ctest --output-on-failure && cd ..
./build/machina_cli run examples/run_request.error_scan.json
```
