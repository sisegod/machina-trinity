# Machina Trinity (Русский)

Платформа автономных агентов с безопасным C++ ядром и Python-оркестрацией.

## Документация

- Canonical (English): `README.md`
- Эквивалентный набор RU: `docs/i18n/ru/EQUIVALENT_DOCSET.md`
- Языковая стратегия: `docs/LANGUAGE_STRATEGY_EN.md`

## Быстрый старт

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
cd build && ctest --output-on-failure && cd ..
./build/machina_cli run examples/run_request.error_scan.json
```
