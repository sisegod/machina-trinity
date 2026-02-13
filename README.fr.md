# Machina Trinity (Français)

Runtime d'agents autonomes avec noyau de sécurité C++ et orchestration Python.

## Documentation

- Canonical (English): `README.md`
- Ensemble équivalent FR: `docs/i18n/fr/EQUIVALENT_DOCSET.md`
- Stratégie linguistique: `docs/LANGUAGE_STRATEGY_EN.md`

## Démarrage rapide

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
cd build && ctest --output-on-failure && cd ..
./build/machina_cli run examples/run_request.error_scan.json
```
