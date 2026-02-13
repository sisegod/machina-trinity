# समतुल्य दस्तावेज़ सेट (हिंदी)

यह दस्तावेज़ हिंदी उपयोगकर्ताओं के लिए अंग्रेज़ी तकनीकी दस्तावेज़ों का **पूर्ण समतुल्य** कवरेज प्रदान करता है.

## 1) पूर्ण कवरेज

कुल 19 स्रोत दस्तावेज़ शामिल हैं:

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

## 2) अनिवार्य ऑपरेशन नियम

- हमेशा मानें कि LLM गलती कर सकता है.
- टूल execution transactional होना चाहिए; failure पर rollback.
- hash-chain audit logs बनाए रखें.
- secrets को repository में commit न करें.

## 3) रिलीज़ से पहले न्यूनतम सत्यापन

```bash
scripts/run_guardrails.sh
python3 scripts/validate_docs_refs.py
python3 scripts/security_guardrails.py
cd build && ctest --output-on-failure
```

## 4) Policy Driver कॉन्ट्रैक्ट

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
```

उदाहरण output:

```text
<PICK><SID0004><END>
```

## 5) भाषा स्थिति और विस्तार योजना

- वर्तमान: Telegram/Pulse मुख्यतः `ko-KR` के लिए optimized है.
- निकट लक्ष्य: `en` path को मजबूत करना, फिर multi-locale विस्तार.
- सिद्धांत: single codebase + locale resource packs.

## 6) मेंटेनेंस नियम

- अंग्रेज़ी स्रोत बदलने पर इसी release में समतुल्य दस्तावेज़ अपडेट करें.
- commands, env vars, और AID के नाम अनुवाद न करें.
- अर्थ-संघर्ष की स्थिति में अंग्रेज़ी पाठ canonical reference है.
