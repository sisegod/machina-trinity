# Language Strategy (BCP-47 aligned)

## 1) Goals

- Keep one shared runtime codebase
- Separate language resources (prompts/keywords/messages) from core execution logic
- Expand Telegram and CLI UX from Korean-first to multilingual without forks

## 2) Current status

As of 2026-02-13:

- Telegram/Pulse behavior is tuned primarily for Korean (`ko-KR`)
- English (`en`) documentation is the primary source of truth for technical specs
- Localized README variants are available for Korean, Japanese, and Simplified Chinese

## 3) Locale naming policy (modern style)

Machina uses BCP-47 style locale tags for classification and rollout planning.

| Locale | Tag | Notes |
|--------|-----|-------|
| English | `en` | Global source docs |
| Korean | `ko-KR` | Current runtime-first language |
| Japanese | `ja-JP` | Planned runtime support |
| Simplified Chinese | `zh-Hans-CN` | Planned runtime support |
| Traditional Chinese | `zh-Hant-TW` | Planned runtime support |

Document file naming keeps GitHub-friendly conventions:

- `README.md` -> English
- `README.ko.md` -> Korean (`ko-KR`)
- `README.ja.md` -> Japanese (`ja-JP`)
- `README.zh-CN.md` -> Simplified Chinese (`zh-Hans-CN`)

## 4) Coverage tiers

### Tier A (active)

- `ko-KR`: Telegram prompts, intent keywords, and ops usage patterns actively tuned
- `en`: documentation source of truth and release notes baseline

### Tier B (in progress)

- `ja-JP`: docs available, runtime prompt pack planned
- `zh-Hans-CN`: docs available, runtime prompt pack planned

### Tier C (planned expansion)

- `zh-Hant-TW`
- `es-ES`, `es-419`
- `pt-BR`
- `fr-FR`
- `de-DE`
- `vi-VN`
- `id-ID`
- `th-TH`
- `ru-RU`
- `ar-SA`
- `hi-IN`

## 5) Telegram/Pulse multilingual roadmap

### Phase 1: Locale switch and fallback

- Introduce `MACHINA_LANG` (`ko-KR`, `en`, `ja-JP`, `zh-Hans-CN`, ...)
- Fallback chain: `user_lang` -> `MACHINA_LANG` -> `en`

### Phase 2: Prompt and intent packs

- Split language-dependent resources from `policies/chat_driver.py` and `chat_intent_map.py`
- Define locale packs under a dedicated resource path (planned)

### Phase 3: Alias and tool phrase maps

- Add localized alias dictionaries for tool routing
- Keep canonical AID dispatch unchanged

### Phase 4: Quality gates per locale

- Add multilingual intent/regression tests
- Add language-specific replay fixtures where needed

## 6) Governance rules

- No long-lived language forks
- One runtime path, many locale resource packs
- English docs remain source-of-truth for core technical contract
- Localized docs are maintained as user onboarding surfaces

## 7) Immediate actions completed in this repo

- Added and aligned README localization set:
  - `README.md`
  - `README.ko.md`
  - `README.ja.md`
  - `README.zh-CN.md`
- Updated roadmap to explicitly state Korean-first Telegram status and multilingual expansion plan
- Added multilingual equivalent docsets (full English scope coverage):
  - `docs/i18n/ko/EQUIVALENT_DOCSET.md`
  - `docs/i18n/ja/EQUIVALENT_DOCSET.md`
  - `docs/i18n/zh-CN/EQUIVALENT_DOCSET.md`
