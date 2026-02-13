# Set Dokumentasi Ekuivalen (Bahasa Indonesia)

Dokumen ini menyediakan versi ekuivalen untuk pengguna berbahasa Indonesia dan mencakup **seluruh cakupan** dokumentasi teknis berbahasa Inggris.

## 1) Cakupan penuh

Mencakup 19 dokumen sumber:

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

## 2) Aturan operasional utama

- Anggap LLM bisa gagal.
- Jalankan tool secara transaksional; jika gagal lakukan rollback.
- Simpan jejak audit dengan hash-chain.
- Jangan simpan secret di repo.

## 3) Validasi minimum sebelum rilis

```bash
scripts/run_guardrails.sh
python3 scripts/validate_docs_refs.py
python3 scripts/security_guardrails.py
cd build && ctest --output-on-failure
```

## 4) Kontrak Policy Driver

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
```

Format output:

```text
<PICK><SID0004><END>
```

## 5) Status bahasa dan rencana

- Saat ini Telegram/Pulse terutama dioptimalkan untuk `ko-KR`.
- Tahap berikutnya: perkuat jalur `en`, lalu tambah locale lain.
- Prinsip: satu codebase, resource pack per locale.

## 6) Aturan pemeliharaan

- Sinkronkan saat dokumen Inggris berubah.
- Jangan terjemahkan nama command, env var, atau AID.
- Jika ada konflik arti, gunakan dokumen Inggris sebagai acuan utama.
