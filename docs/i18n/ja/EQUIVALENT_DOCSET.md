# 日本語等価ドキュメントセット（英語ドキュメント全体対応）

この文書は、英語ソース 19 件の内容を運用判断レベルで等価に把握できるよう整理した日本語版です。

## 1. 対応範囲

| 英語ソース | 日本語等価カバー |
|---|---|
| `README.md` | 全体像、実行モード、セキュリティ思想、文書導線 |
| `CODE_OF_CONDUCT.md` | コミュニティ行動規範 |
| `CONTRIBUTING.md` | 貢献フロー、PR品質要件 |
| `SECURITY.md` | 脆弱性報告、秘密情報管理 |
| `GITHUB_UPLOAD_CHECKLIST.md` | 公開前チェック項目 |
| `MACHINA_LLM_SETUP_GUIDE.md` | 埋め込み/LLM/ポリシードライバ設定 |
| `MACHINA_TEST_CATALOG.md` | テスト体系（ツール/ゴール/リプレイ/MCP） |
| `docs/QUICKSTART.md` | 10分導入手順 |
| `docs/ARCHITECTURE.md` | Trinity設計と実行経路 |
| `docs/OPERATIONS.md` | 運用プロファイル/ガードレール |
| `docs/SERVE_API.md` | serve API 認証・観測 |
| `docs/LLM_BACKENDS.md` | バックエンド接続パターン |
| `docs/POLICY_DRIVER.md` | 外部ポリシープロトコル |
| `docs/LANGUAGE_STRATEGY_EN.md` | 言語戦略とBCP-47方針 |
| `docs/ROADMAP.md` | リリース済み機能と今後計画 |
| `docs/ipc_schema.md` | IPC契約 |
| `docs/GITHUB_PREP_AUDIT_2026-02-13.md` | 公開構成監査の根拠 |
| `examples/policy_drivers/README.md` | ドライバ実装サンプル |
| `toolpacks/runtime_genesis/src/self_test_calc/README.md` | Genesis ランタイムテスト説明 |

## 2. 運用の必須ポイント

### 2.1 安全前提

- LLM は誤る前提で運用する
- ツール実行はトランザクション単位、失敗時はロールバック
- 監査ログはハッシュチェーンで改ざん検知可能

### 2.2 公開前の標準検証

```bash
scripts/run_guardrails.sh
python3 scripts/validate_docs_refs.py
python3 scripts/security_guardrails.py
cd build && ctest --output-on-failure
```

### 2.3 ポリシードライバ実行条件

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
```

## 3. 文書群ごとの等価要約

### 3.1 導入系

対象: `README.md`, `docs/QUICKSTART.md`, `GITHUB_UPLOAD_CHECKLIST.md`

- 初期導入と実行確認の最短ルート
- 公開前に不要成果物を除外

### 3.2 設計/契約系

対象: `docs/ARCHITECTURE.md`, `docs/ipc_schema.md`

- Body/Driver/Memory 分離の理解
- selector/policy/replay 境界の確認

### 3.3 運用/セキュリティ系

対象: `docs/OPERATIONS.md`, `SECURITY.md`, `docs/SERVE_API.md`

- `dev`/`prod` プロファイルと安全既定値
- Token/HMAC 認証、レート制限、メトリクス運用

### 3.4 LLM/言語戦略系

対象: `MACHINA_LLM_SETUP_GUIDE.md`, `docs/LLM_BACKENDS.md`, `docs/POLICY_DRIVER.md`, `docs/LANGUAGE_STRATEGY_EN.md`

- エンジン用ポリシードライバと会話用バックエンドを分離
- 韓国語中心運用から多言語へ段階展開

### 3.5 品質保証系

対象: `MACHINA_TEST_CATALOG.md`, `docs/ROADMAP.md`, `docs/GITHUB_PREP_AUDIT_2026-02-13.md`

- テスト網羅とリリース計画の整合
- 公開構成の妥当性確認

### 3.6 例示/Genesis系

対象: `examples/policy_drivers/README.md`, `toolpacks/runtime_genesis/src/self_test_calc/README.md`

- `<PICK>...<END>` 契約に準拠した外部ドライバ実装
- Runtime plugin の安全な生成/読み込みパス

## 4. 日本語運用メモ

- Telegram/Pulse は現在 `ko-KR` チューニングが中心
- `ja-JP` は docs サポート済み、ランタイム言語パックを今後強化
- 実装契約は英語原文を基準として判断する

## 5. 維持ルール

- 英語原文更新時、同リリースで等価文書を更新
- コマンド/環境変数名は翻訳しない
- セキュリティと検証手順は省略しない
