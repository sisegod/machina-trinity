# مجموعة توثيق مكافئة (العربية)

يوفر هذا المستند نسخة مكافئة للمستخدمين الناطقين بالعربية ويغطي **النطاق الكامل** لوثائق الإنجليزية التقنية.

## 1) نطاق التغطية الكامل

يغطي 19 مستندًا مرجعيًا:

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

## 2) قواعد تشغيل أساسية

- افترض دائمًا أن الـ LLM قد يخطئ.
- تنفيذ الأدوات يجب أن يكون معامليًا (transactional) مع rollback عند الفشل.
- الحفاظ على سجلات تدقيق مرتبطة بسلسلة hash.
- عدم حفظ الأسرار داخل المستودع.

## 3) التحقق الإلزامي قبل النشر

```bash
scripts/run_guardrails.sh
python3 scripts/validate_docs_refs.py
python3 scripts/security_guardrails.py
cd build && ctest --output-on-failure
```

## 4) عقد Policy Driver

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
```

صيغة خرج صحيحة:

```text
<PICK><SID0004><END>
```

## 5) حالة اللغات وخطة التوسع

- الوضع الحالي: Telegram/Pulse محسّن أساسًا لـ `ko-KR`.
- المدى القريب: تحسين تجربة `en` ثم التوسع للغات إضافية.
- المبدأ: قاعدة كود واحدة + حزم موارد لكل locale.

## 6) قواعد الصيانة

- عند تعديل المصدر الإنجليزي، يجب تحديث النسخة المكافئة في نفس الإصدار.
- لا تترجم أسماء الأوامر أو متغيرات البيئة أو AID.
- عند التعارض، النص الإنجليزي هو المرجع الرسمي.
