# Machina Trinity (العربية)

منصة تشغيل لوكلاء ذاتيين مع نواة أمان C++ وطبقة orchestration بـ Python.

## الوثائق

- Canonical (English): `README.md`
- المجموعة المكافئة AR: `docs/i18n/ar/EQUIVALENT_DOCSET.md`
- استراتيجية اللغة: `docs/LANGUAGE_STRATEGY_EN.md`

## بدء سريع

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
cd build && ctest --output-on-failure && cd ..
./build/machina_cli run examples/run_request.error_scan.json
```
