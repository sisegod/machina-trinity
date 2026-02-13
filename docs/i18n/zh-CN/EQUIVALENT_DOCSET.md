# 中文等价文档集（覆盖全部英文文档）

本文件用于覆盖英文原始文档 19 份的核心信息，使中文读者在运维与开发决策上获得等价信息。

## 1. 覆盖范围

| 英文原文 | 中文等价覆盖 |
|---|---|
| `README.md` | 项目总览、运行模式、安全设计、文档导航 |
| `CODE_OF_CONDUCT.md` | 社区行为规范 |
| `CONTRIBUTING.md` | 贡献流程与 PR 质量要求 |
| `SECURITY.md` | 漏洞披露与安全实践 |
| `GITHUB_UPLOAD_CHECKLIST.md` | GitHub 发布前检查清单 |
| `MACHINA_LLM_SETUP_GUIDE.md` | Embedding/LLM/策略驱动接入指南 |
| `MACHINA_TEST_CATALOG.md` | 全量测试目录（工具/目标/重放/MCP） |
| `docs/QUICKSTART.md` | 10 分钟上手路径 |
| `docs/ARCHITECTURE.md` | Trinity 架构与执行路径 |
| `docs/OPERATIONS.md` | 运维配置与守护策略 |
| `docs/SERVE_API.md` | serve API、认证与可观测性 |
| `docs/LLM_BACKENDS.md` | LLM 后端接入方式 |
| `docs/POLICY_DRIVER.md` | 外部 Policy Driver 协议 |
| `docs/LANGUAGE_STRATEGY_EN.md` | 语言策略与 BCP-47 分类 |
| `docs/ROADMAP.md` | 已发布能力与后续规划 |
| `docs/ipc_schema.md` | IPC 协议契约 |
| `docs/GITHUB_PREP_AUDIT_2026-02-13.md` | 发布准备审计依据 |
| `examples/policy_drivers/README.md` | 驱动示例使用说明 |
| `toolpacks/runtime_genesis/src/self_test_calc/README.md` | Genesis 运行时测试工具说明 |

## 2. 必须掌握的运行要点

### 2.1 安全前提

- 默认假设 LLM 会犯错
- 工具调用以事务方式执行，失败自动回滚
- 审计日志采用哈希链，支持篡改检测与追踪

### 2.2 发布前标准验证

```bash
scripts/run_guardrails.sh
python3 scripts/validate_docs_refs.py
python3 scripts/security_guardrails.py
cd build && ctest --output-on-failure
```

### 2.3 使用策略驱动示例时的必要环境变量

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
```

## 3. 按文档分组的等价说明

### 3.1 上手与发布文档组

对象: `README.md`, `docs/QUICKSTART.md`, `GITHUB_UPLOAD_CHECKLIST.md`

- 提供从构建到首跑的最短路径
- 提供公开仓库发布前的清理与验证流程

### 3.2 架构与协议文档组

对象: `docs/ARCHITECTURE.md`, `docs/ipc_schema.md`

- 理解 Body/Driver/Memory 分层
- 保证 runner、selector、policy、replay 的边界清晰

### 3.3 运维与安全文档组

对象: `docs/OPERATIONS.md`, `SECURITY.md`, `docs/SERVE_API.md`

- 说明 `dev/prod` 配置差异与安全默认值
- 覆盖 Token/HMAC、限流、指标等关键运维点

### 3.4 LLM 与语言策略文档组

对象: `MACHINA_LLM_SETUP_GUIDE.md`, `docs/LLM_BACKENDS.md`, `docs/POLICY_DRIVER.md`, `docs/LANGUAGE_STRATEGY_EN.md`

- 区分“引擎策略驱动”与“聊天后端”两条链路
- 说明当前韩语优先运行状态与多语言扩展路径

### 3.5 质量与规划文档组

对象: `MACHINA_TEST_CATALOG.md`, `docs/ROADMAP.md`, `docs/GITHUB_PREP_AUDIT_2026-02-13.md`

- 覆盖测试体系、回归保障、版本演进计划
- 提供公开仓库内容取舍依据

### 3.6 示例与 Genesis 文档组

对象: `examples/policy_drivers/README.md`, `toolpacks/runtime_genesis/src/self_test_calc/README.md`

- 说明 `<PICK>...<END>` 协议输出格式
- 说明运行时插件生成与加载边界

## 4. 当前语言状态（重要）

- Telegram/Pulse 当前仍以 `ko-KR` 为主要优化语言
- `en` 为技术规范与实现契约的主文档语言
- 中文/日文文档用于等价说明与运营接入

## 5. 维护规则

- 英文原文更新时，同版本同步更新等价文档
- 命令、环境变量、AID 标识保持原文不翻译
- 安全与验证步骤不得省略
