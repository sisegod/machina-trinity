# Machina Trinity (ไทย)

ระบบรันไทม์เอเจนต์อัตโนมัติ พร้อมแกนความปลอดภัย C++ และชั้น orchestration ด้วย Python

## เอกสาร

- Canonical (English): `README.md`
- ชุดเอกสารเทียบเท่า TH: `docs/i18n/th/EQUIVALENT_DOCSET.md`
- กลยุทธ์ภาษา: `docs/LANGUAGE_STRATEGY_EN.md`

## เริ่มต้นอย่างรวดเร็ว

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
cd build && ctest --output-on-failure && cd ..
./build/machina_cli run examples/run_request.error_scan.json
```
