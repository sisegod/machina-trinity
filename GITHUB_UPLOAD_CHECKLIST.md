# GitHub Upload Checklist

## 1) Rebuild clean package

```bash
scripts/export_github_ready.sh
```

## 2) Folder to upload

- `github_ready/`

## 3) Items excluded from public upload

- `build/`, `logs/`, `work/`, `__pycache__/`
- `machina_env.sh`, `.env*`, `.secrets.env`
- `ops/` runtime report artifacts
- Internal session/audit notes and duplicate README files

## 4) Pre-upload validation

```bash
scripts/run_guardrails.sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
cd build && ctest --output-on-failure
```
