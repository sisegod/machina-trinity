<div align="center">

# Machina Trinity

**安全ゲート付き自律エージェントランタイム (C++ コア + Python エージェント層)**

</div>

## 言語ドキュメント

- English: `README.md`
- 한국어: `README.ko.md`
- 日本語: `README.ja.md`
- 简体中文: `README.zh-CN.md`
- Language strategy: `docs/LANGUAGE_STRATEGY_EN.md`

## 概要

Machina Trinity は、LLM が失敗することを前提に設計されたエージェントランタイムです。
自律性だけでなく、安全性・追跡性・復旧性を重視します。

主要な構成は次の 3 つです。

1. C++ 安全コア: トランザクション実行、ロールバック、監査ログ、WAL
2. Python オーケストレーション: Telegram/Pulse ループ、dispatch、memory、MCP bridge
3. 運用ガードレール: 権限制御、replay、検証スクリプト

## クイックスタート

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake pkg-config libjson-c-dev

cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
cd build && ctest --output-on-failure && cd ..

./build/machina_cli run examples/run_request.error_scan.json
```

Telegram を使う場合:

```bash
cp .secrets.env.example .secrets.env
python3 telegram_bot.py
```

## 言語サポート状況

現在の Telegram/Pulse 経路は韓国語中心で最適化されています。

- 現在: `ko-KR` 優先運用
- 進行中: `en` のユーザー体験強化
- 予定: `ja-JP`, `zh-Hans-CN`, `zh-Hant-TW` を含む多言語展開

詳細は `docs/ROADMAP.md` と `docs/LANGUAGE_STRATEGY_EN.md` を参照してください。

## 主なドキュメント

- Quickstart: `docs/QUICKSTART.md`
- Architecture: `docs/ARCHITECTURE.md`
- Operations: `docs/OPERATIONS.md`
- Serve API: `docs/SERVE_API.md`
- Policy Driver: `docs/POLICY_DRIVER.md`
- LLM Backends: `docs/LLM_BACKENDS.md`
- Roadmap: `docs/ROADMAP.md`

## ライセンス

Apache-2.0 (`LICENSE`)
