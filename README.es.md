# Machina Trinity (Español)

Runtime de agentes autónomos con núcleo de seguridad en C++ y orquestación Python.

## Documentación

- Canonical (English): `README.md`
- Conjunto equivalente en español: `docs/i18n/es/EQUIVALENT_DOCSET.md`
- Estrategia de idioma: `docs/LANGUAGE_STRATEGY_EN.md`

## Inicio rápido

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
cd build && ctest --output-on-failure && cd ..
./build/machina_cli run examples/run_request.error_scan.json
```
