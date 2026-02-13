<div align="center">

# Machina Trinity

**安全门控的自治代理运行时（C++ Core + Python Agent Layer）**

</div>

## 文档语言

- English: `README.md`
- 한국어: `README.ko.md`
- 日本語: `README.ja.md`
- 简体中文: `README.zh-CN.md`
- 语言策略: `docs/LANGUAGE_STRATEGY_EN.md`
- 全量等价文档集: `docs/i18n/README.md`

## 项目简介

Machina Trinity 的设计前提是：LLM 一定会犯错。
系统目标不是盲目追求“全自动”，而是先保证安全性、可追踪性和可恢复性。

核心由三部分组成：

1. C++ 安全内核：事务执行、回滚、审计日志、WAL
2. Python 编排层：Telegram/Pulse 循环、dispatch、memory、MCP bridge
3. 运维护栏：权限策略、replay、验证脚本

## 快速开始

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake pkg-config libjson-c-dev

cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
cd build && ctest --output-on-failure && cd ..

./build/machina_cli run examples/run_request.error_scan.json
```

如需 Telegram 代理：

```bash
cp .secrets.env.example .secrets.env
python3 telegram_bot.py
```

## 语言支持现状

目前 Telegram/Pulse 路径仍以韩语语料与提示为主进行优化。

- 当前: `ko-KR` 为优先运行语言
- 进行中: 强化 `en` 用户路径
- 计划中: 扩展到 `ja-JP`, `zh-Hans-CN`, `zh-Hant-TW` 等多语言

详情见 `docs/ROADMAP.md` 与 `docs/LANGUAGE_STRATEGY_EN.md`。

## 主要文档

- 快速开始: `docs/QUICKSTART.md`
- 架构: `docs/ARCHITECTURE.md`
- 运维: `docs/OPERATIONS.md`
- Serve API: `docs/SERVE_API.md`
- Policy Driver: `docs/POLICY_DRIVER.md`
- LLM Backends: `docs/LLM_BACKENDS.md`
- Roadmap: `docs/ROADMAP.md`

## 许可证

Apache-2.0 (`LICENSE`)
