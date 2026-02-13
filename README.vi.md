# Machina Trinity (Tiếng Việt)

Nền tảng runtime agent tự động với lõi an toàn C++ và lớp điều phối Python.

## Tài liệu

- Canonical (English): `README.md`
- Bộ tài liệu tương đương VI: `docs/i18n/vi/EQUIVALENT_DOCSET.md`
- Chiến lược ngôn ngữ: `docs/LANGUAGE_STRATEGY_EN.md`

## Bắt đầu nhanh

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
cd build && ctest --output-on-failure && cd ..
./build/machina_cli run examples/run_request.error_scan.json
```
