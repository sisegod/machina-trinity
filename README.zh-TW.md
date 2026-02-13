# Machina Trinity（繁體中文）

具備 C++ 安全核心與 Python 編排層的自治代理執行環境。

## 文件

- Canonical (English): `README.md`
- 繁中等價文件集: `docs/i18n/zh-TW/EQUIVALENT_DOCSET.md`
- 語言策略: `docs/LANGUAGE_STRATEGY_EN.md`

## 快速開始

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
cd build && ctest --output-on-failure && cd ..
./build/machina_cli run examples/run_request.error_scan.json
```
