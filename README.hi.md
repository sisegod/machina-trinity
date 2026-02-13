# Machina Trinity (हिंदी)

C++ सुरक्षा कोर और Python orchestration लेयर के साथ स्वायत्त एजेंट runtime.

## दस्तावेज़

- Canonical (English): `README.md`
- समतुल्य दस्तावेज़ सेट HI: `docs/i18n/hi/EQUIVALENT_DOCSET.md`
- भाषा रणनीति: `docs/LANGUAGE_STRATEGY_EN.md`

## त्वरित शुरुआत

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
cd build && ctest --output-on-failure && cd ..
./build/machina_cli run examples/run_request.error_scan.json
```
