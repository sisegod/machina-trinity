# ชุดเอกสารเทียบเท่า (ภาษาไทย)

เอกสารนี้จัดทำขึ้นเพื่อให้ผู้ใช้ภาษาไทยเข้าถึงข้อมูลที่ **เทียบเท่า** กับเอกสารภาษาอังกฤษทั้งหมด.

## 1) ขอบเขตที่ครอบคลุม

ครอบคลุมเอกสารต้นฉบับ 19 รายการ:

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

## 2) กฎการปฏิบัติการที่สำคัญ

- ต้องถือว่า LLM อาจตัดสินใจผิดพลาดได้
- การเรียกใช้เครื่องมือเป็นแบบธุรกรรม; ถ้าล้มเหลวต้อง rollback
- เก็บ audit log แบบ hash-chain เพื่อการตรวจสอบย้อนหลัง
- ห้ามเก็บความลับใน repository

## 3) การตรวจสอบขั้นต่ำก่อนเผยแพร่

```bash
scripts/run_guardrails.sh
python3 scripts/validate_docs_refs.py
python3 scripts/security_guardrails.py
cd build && ctest --output-on-failure
```

## 4) สัญญา Policy Driver

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
```

ตัวอย่าง output ที่ถูกต้อง:

```text
<PICK><SID0004><END>
```

## 5) สถานะภาษาและแผนขยาย

- ปัจจุบัน Telegram/Pulse ปรับจูนหลักที่ `ko-KR`
- ระยะใกล้: เสริมเสถียรภาพเส้นทาง `en` แล้วค่อยขยายหลายภาษา
- หลักการ: codebase เดียว + locale resource packs

## 6) กฎการบำรุงรักษา

- เมื่อเอกสารอังกฤษเปลี่ยน ต้องอัปเดตชุดเทียบเท่าใน release เดียวกัน
- ห้ามแปลชื่อคำสั่ง, env vars, และ AID
- หากความหมายไม่ตรง ให้ยึดเอกสารอังกฤษเป็นมาตรฐาน
