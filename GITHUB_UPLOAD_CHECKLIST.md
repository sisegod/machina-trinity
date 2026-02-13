# GitHub Upload Checklist

## 1) 클린 패키지 재생성
```bash
scripts/export_github_ready.sh
```

## 2) 업로드할 폴더
- `github_ready/`

## 3) 제외된 항목
- `build/`, `logs/`, `work/`, `__pycache__/`
- `machina_env.sh`, `.env*`, `.secrets.env`
- `ops/` 런타임 보고 파일
- 내부 기록성 문서와 중복 README

## 4) 업로드 전 검증
```bash
scripts/run_guardrails.sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
cd build && ctest --output-on-failure
```
