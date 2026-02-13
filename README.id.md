# Machina Trinity (Bahasa Indonesia)

Runtime agen otonom dengan inti keamanan C++ dan orkestrasi Python.

## Dokumentasi

- Canonical (English): `README.md`
- Set ekuivalen ID: `docs/i18n/id/EQUIVALENT_DOCSET.md`
- Strategi bahasa: `docs/LANGUAGE_STRATEGY_EN.md`

## Mulai cepat

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
cd build && ctest --output-on-failure && cd ..
./build/machina_cli run examples/run_request.error_scan.json
```
