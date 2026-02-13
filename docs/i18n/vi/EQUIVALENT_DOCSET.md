# Bộ Tài Liệu Tương Đương (Tiếng Việt)

Tài liệu này cung cấp bản tương đương cho người dùng tiếng Việt và bao phủ **toàn bộ phạm vi** tài liệu kỹ thuật tiếng Anh.

## 1) Phạm vi bao phủ

Bao gồm 19 tài liệu nguồn:

- `README.md`
- `CODE_OF_CONDUCT.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `GITHUB_UPLOAD_CHECKLIST.md`
- `MACHINA_LLM_SETUP_GUIDE.md`
- `MACHINA_TEST_CATALOG.md`
- `docs/QUICKSTART.md`
- `docs/ARCHITECTURE.md`
- `docs/OPERATIONS.md`
- `docs/SERVE_API.md`
- `docs/LLM_BACKENDS.md`
- `docs/POLICY_DRIVER.md`
- `docs/LANGUAGE_STRATEGY_EN.md`
- `docs/ROADMAP.md`
- `docs/ipc_schema.md`
- `docs/GITHUB_PREP_AUDIT_2026-02-13.md`
- `examples/policy_drivers/README.md`
- `toolpacks/runtime_genesis/src/self_test_calc/README.md`

## 2) Nguyên tắc vận hành bắt buộc

- Luôn giả định LLM có thể sai.
- Thực thi tool theo giao dịch; lỗi thì rollback.
- Duy trì log audit dạng hash-chain để truy vết.
- Không commit secrets vào repo.

## 3) Kiểm tra bắt buộc trước khi phát hành

```bash
scripts/run_guardrails.sh
python3 scripts/validate_docs_refs.py
python3 scripts/security_guardrails.py
cd build && ctest --output-on-failure
```

## 4) Hợp đồng Policy Driver

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
```

Định dạng output mẫu:

```text
<PICK><SID0004><END>
```

## 5) Trạng thái ngôn ngữ và kế hoạch

- Hiện tại: Telegram/Pulse tối ưu chủ yếu cho `ko-KR`.
- Gần hạn: tăng chất lượng luồng `en`, sau đó mở rộng locale.
- Nguyên tắc: một codebase, nhiều gói tài nguyên locale.

## 6) Quy tắc đồng bộ

- Khi tài liệu tiếng Anh thay đổi, cập nhật bộ tương đương cùng bản phát hành.
- Không dịch tên lệnh, biến môi trường, AID.
- Khi khác nghĩa, tài liệu tiếng Anh là nguồn chuẩn.
