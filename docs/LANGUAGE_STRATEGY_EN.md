# Language Strategy (Draft)

Current runtime behavior is Korean-first in the Python orchestration layer.

Short-term plan:
- Keep one codebase.
- Add language selection via environment variable (e.g. `MACHINA_LANG=ko|en`).
- Split prompts/keyword maps by language, but keep execution logic shared.

Non-goal:
- Do not maintain long-lived product forks for Korean vs English runtime behavior.

Rationale:
- Reduces merge drift and regression risk while enabling English-first UX for public users.
