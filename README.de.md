# Machina Trinity (Deutsch)

Autonomes Agenten-Runtime mit C++-Sicherheitskern und Python-Orchestrierung.

## Dokumentation

- Canonical (English): `README.md`
- Äquivalentes Set DE: `docs/i18n/de/EQUIVALENT_DOCSET.md`
- Sprachstrategie: `docs/LANGUAGE_STRATEGY_EN.md`

## Schnellstart

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
cd build && ctest --output-on-failure && cd ..
./build/machina_cli run examples/run_request.error_scan.json
```
