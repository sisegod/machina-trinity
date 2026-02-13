#!/usr/bin/env python3
"""Machina Chat Driver v3 "Pulse" — 3-Phase Architecture.

Phase 1: Intent classification (format:json, temp=0, Korean prompt)
Phase 2: Natural response / summary generation (free text, temp=0.7)
Phase 3: Continue loop — observe tool results, decide next action or done

Supports: Ollama (7-20B), Anthropic Claude, OAI-compatible backends.
All exception paths log errors before returning graceful fallbacks.

Reads JSON from stdin, returns JSON to stdout.

Env:
  MACHINA_CHAT_BACKEND  (default: oai_compat)  — "oai_compat" | "anthropic"
  MACHINA_CHAT_MAX_TOKENS (default: 4096)
  MACHINA_CHAT_TEMPERATURE (default: 0.7)
  OAI_COMPAT_BASE_URL, OAI_COMPAT_MODEL, OAI_COMPAT_API_KEY
  ANTHROPIC_API_KEY, ANTHROPIC_MODEL, ANTHROPIC_BASE_URL
"""

import json
import os
import re
import sys
import logging

# Ensure parent directory (project root) is in sys.path for machina_shared imports
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from chat_llm import (
    _call_ollama_json, _call_ollama_text, _call_oai_compat_text,
    _call_anthropic, _is_ollama, _extract_json_from_text,
)
from chat_intent_map import _normalize_intent, _intent_to_machina_action
from chat_driver_util import (
    track_dialogue_state, extract_entities, _get_skill_hint,
    _is_meta_question, _trim_history, _compress_old_messages,
    _INTENT_KEYWORDS, classify_plan, handle_plan,
    resolve_intent_fast,
)

logger = logging.getLogger("chat_driver")

# ---------------------------------------------------------------------------
# Phase 1: Intent Classification Prompt (한국어, 짧게, few-shot)
# 핵심: 소형 모델이 확실히 따를 수 있도록 300토큰 이내 + 명확한 예시
# ---------------------------------------------------------------------------
INTENT_PROMPT = """너는 마키나(Machina), 뭐든 도와주는 범용 AI야. 대화, 계산, 조사, 코딩, 시스템 관리 전부 가능해.
사용자 메시지를 분석해서 JSON 하나만 출력해.

도구 (언제 쓰는지):
- shell: 시스템 명령 실행 (ls, df, nvidia-smi, ps 등)
- search: 웹 검색/뉴스/크롤링/날씨/가격 전부 이걸로! 모르면 검색. query는 영어로! (한국 주제도 영어 키워드). code로 requests/playwright/subprocess 쓰지 마 — 네트워크 차단됨!
- memory_save: "기억해" 요청 시 정보 저장
- memory_find: "전에 뭐라고 했지?" 저장된 기억 검색
- file_read: 파일 내용 읽기
- file_write: 파일 생성/수정 (work/ 경로만)
- genesis: 새 C++ 도구 생성
- config: LLM 모델/설정 변경
- code: 계산, 정렬, 알고리즘, 수학, 피보나치, 구구단, 수열, 코딩 요청 → 반드시 코드 실행 (직접 답 금지). lang=python|bash|cpp
- web: URL 페이지 내용 읽기 (텍스트 추출됨)
- util_save: 코드를 유틸리티로 저장 (재사용)
- util_run: 저장된 유틸리티 실행
- util_list: 유틸리티 목록
- util_delete: 유틸리티 삭제
- util_update: 유틸리티 코드/설명 수정
- file_list: 디렉토리 파일 목록 조회
- file_search: 파일 내용 검색 (grep). pattern=정규식
- file_diff: 두 파일 비교 (unified diff)
- file_edit: 줄 단위 편집 (replace/insert/delete). work/ 경로만
- file_append: 파일 끝에 내용 추가. work/ 경로만
- file_delete: 파일/디렉토리 삭제 (work/ 전용, 휴지통 이동)
- project_create: 멀티파일 프로젝트 생성 (lang=cpp/python, files=[{path,content}])
- project_build: C++ 프로젝트 빌드 (shared/executable)
- pip_install: Python 패키지 설치 (격리 venv, 사용자 승인 필요)
- pip_uninstall: Python 패키지 제거 (격리 venv, 사용자 승인 필요)
- pip_list: venv 설치된 패키지 목록 조회

출력 형식 (반드시 하나만):
대화: {"type":"chat","msg":"답변"}
명령: {"type":"run","tool":"shell","cmd":"명령어"}
검색: {"type":"run","tool":"search","query":"검색어"}
기억저장: {"type":"run","tool":"memory_save","text":"내용"}
기억검색: {"type":"run","tool":"memory_find","text":"검색어"}
파일읽기: {"type":"run","tool":"file_read","path":"경로"}
파일쓰기: {"type":"run","tool":"file_write","path":"work/파일명","content":"내용"}
도구생성: {"type":"run","tool":"genesis","name":"이름","description":"설명"}
설정변경: {"type":"config","key":"키","value":"값"}
코드실행: {"type":"run","tool":"code","lang":"python","code":"코드"}
URL읽기: {"type":"run","tool":"web","url":"URL"}
유틸저장: {"type":"run","tool":"util_save","name":"이름","lang":"python","code":"코드","description":"설명"}
유틸실행: {"type":"run","tool":"util_run","name":"이름","args":"인자"}
유틸목록: {"type":"run","tool":"util_list"}
유틸삭제: {"type":"run","tool":"util_delete","name":"이름"}
유틸수정: {"type":"run","tool":"util_update","name":"이름","code":"새코드","description":"새설명"}
파일목록: {"type":"run","tool":"file_list","path":"경로"}
파일검색: {"type":"run","tool":"file_search","root":".","pattern":"정규식","ext_filter":".py"}
파일비교: {"type":"run","tool":"file_diff","path1":"경로1","path2":"경로2"}
파일편집: {"type":"run","tool":"file_edit","path":"work/파일","operation":"replace","line":5,"content":"새내용"}
파일추가: {"type":"run","tool":"file_append","path":"work/파일","content":"추가내용"}
파일삭제: {"type":"run","tool":"file_delete","path":"work/파일"}
프로젝트: {"type":"run","tool":"project_create","name":"이름","lang":"cpp","files":[{"path":"main.cpp","content":"코드"}]}
빌드: {"type":"run","tool":"project_build","name":"이름","lang":"cpp","build_type":"shared"}
패키지설치: {"type":"run","tool":"pip_install","packages":["numpy","pandas"],"venv_name":"default"}
패키지제거: {"type":"run","tool":"pip_uninstall","packages":["numpy"],"venv_name":"default"}
패키지목록: {"type":"run","tool":"pip_list","venv_name":"default"}

예시:
안녕 -> {"type":"chat","msg":"안녕! 뭐 도와줄까?"}
오늘 기분 어때 -> {"type":"chat","msg":"좋지! 너는 어때?"}
고마워 -> {"type":"chat","msg":"별말씀을! 더 필요한거 있어?"}
비트코인 가격 -> {"type":"run","tool":"search","query":"bitcoin price today"}
오늘 날씨 -> {"type":"run","tool":"search","query":"Seoul weather today"}
이순신 누구야 -> {"type":"run","tool":"search","query":"Yi Sun-sin Korean admiral"}
파이썬 3.12 새기능 -> {"type":"run","tool":"search","query":"Python 3.12 new features"}
올라마 모델 종류 -> {"type":"run","tool":"search","query":"Ollama available models list"}
웹검색해줘 최신 AI 뉴스 -> {"type":"run","tool":"search","query":"latest AI news 2026"}
네이트 뉴스 보여줘 -> {"type":"run","tool":"search","query":"Korea news today nate"}
크롤링해서 정보 좀 -> {"type":"run","tool":"search","query":"web scraping information"}
이거 기억해 내 생일 3월 15일 -> {"type":"run","tool":"memory_save","text":"user birthday: March 15"}
내 생일 언제였지 -> {"type":"run","tool":"memory_find","text":"birthday"}
피보나치 짜줘 -> {"type":"run","tool":"code","lang":"python","code":"a,b=0,1\\nfor _ in range(10):\\n    print(a,end=' ')\\n    a,b=b,a+b"}
1~100 소수 -> {"type":"run","tool":"code","lang":"python","code":"for n in range(2,101):\\n    ok=True\\n    for i in range(2,int(n**0.5)+1):\\n        if n%i==0:\\n            ok=False\\n            break\\n    if ok:\\n        print(n,end=' ')"}
3의 20제곱 -> {"type":"run","tool":"code","lang":"python","code":"print(3**20)"}
구구단 출력해봐 -> {"type":"run","tool":"code","lang":"python","code":"for i in range(2,10):\\n    line=''\\n    for j in range(1,10):\\n        line+=str(i)+'x'+str(j)+'='+str(i*j)+' '\\n    print(line)"}
정렬해줘 [9,1,7,3] -> {"type":"run","tool":"code","lang":"python","code":"arr=[9,1,7,3]\\narr.sort()\\nprint(arr)"}
메모리 확인 -> {"type":"run","tool":"shell","cmd":"free -h"}
GPU 상태 -> {"type":"run","tool":"shell","cmd":"nvidia-smi"}
디스크 남았어 -> {"type":"run","tool":"shell","cmd":"df -h"}
프로세스 목록 -> {"type":"run","tool":"shell","cmd":"ps aux --sort=-%mem | head -20"}
이 URL 봐줘 https://example.com -> {"type":"run","tool":"web","url":"https://example.com"}
work/memo.txt에 hello 써줘 -> {"type":"run","tool":"file_write","path":"work/memo.txt","content":"hello"}
클로드로 바꿔 -> {"type":"config","key":"backend","value":"anthropic"}
exaone으로 바꿔 -> {"type":"config","key":"model","value":"exaone3.5:7.8b"}
유틸리티 뭐 있어 -> {"type":"run","tool":"util_list"}
피보나치 유틸 저장해 -> {"type":"run","tool":"util_save","name":"fibonacci","lang":"python","code":"import sys\\nn=int(sys.argv[1]) if len(sys.argv)>1 else 10\\na,b=0,1\\nfor _ in range(n):\\n    print(a,end=' ')\\n    a,b=b,a+b","description":"fibonacci sequence"}
피보나치 유틸 실행 -> {"type":"run","tool":"util_run","name":"fibonacci","args":"15"}
피보나치 유틸 삭제해 -> {"type":"run","tool":"util_delete","name":"fibonacci"}
카운트다운 유틸 코드 바꿔줘 -> {"type":"run","tool":"util_update","name":"countdown","code":"import sys\\nn=int(sys.argv[1]) if len(sys.argv)>1 else 5\\nfor i in range(n,0,-1):\\n    print(i)","description":"improved countdown"}
현재 폴더 파일 뭐 있어 -> {"type":"run","tool":"file_list","path":"."}
work 폴더 보여줘 -> {"type":"run","tool":"file_list","path":"work"}
TODO 있는 파일 찾아줘 -> {"type":"run","tool":"file_search","root":".","pattern":"TODO","ext_filter":".py"}
두 파일 뭐가 다른지 비교해 -> {"type":"run","tool":"file_diff","path1":"work/old.txt","path2":"work/new.txt"}
work/memo.txt 5번째 줄 바꿔줘 -> {"type":"run","tool":"file_edit","path":"work/memo.txt","operation":"replace","line":5,"content":"new content"}
work/log.txt에 한줄 추가해 -> {"type":"run","tool":"file_append","path":"work/log.txt","content":"appended line"}
work/temp.txt 지워줘 -> {"type":"run","tool":"file_delete","path":"work/temp.txt"}
C++ 프로젝트 만들어줘 -> {"type":"run","tool":"project_create","name":"my_tool","lang":"cpp","files":[{"path":"main.cpp","content":"#include <iostream>\\nint main(){std::cout<<\"hello\";}"}]}
프로젝트 빌드해 -> {"type":"run","tool":"project_build","name":"my_tool","lang":"cpp","build_type":"executable"}
numpy 설치해줘 -> {"type":"run","tool":"pip_install","packages":["numpy"],"venv_name":"default"}
numpy 지워줘 -> {"type":"run","tool":"pip_uninstall","packages":["numpy"],"venv_name":"default"}
설치된 패키지 뭐있어 -> {"type":"run","tool":"pip_list","venv_name":"default"}

★ 주의: 아래는 chat이다! 도구 실행이 아니라 "질문"이니까:
도구 리스트 충분해 보여? -> {"type":"chat","msg":"현재 22개 도구가 있어! 부족한 거 있으면 말해줘."}
도구 많이 늘었네? -> {"type":"chat","msg":"응! MCP 연결하면서 꽤 늘었지. 웹검색, 이미지분석 등 10개 추가됐어."}
검색 잘 되나? -> {"type":"chat","msg":"웹 검색 잘 돼! 테스트해볼까?"}
메모리 기능 잘 작동해? -> {"type":"chat","msg":"기억 저장/검색 둘 다 잘 돼!"}
코드 실행 가능해? -> {"type":"chat","msg":"Python, Bash, C++ 실행 가능해!"}
이 기능 괜찮은 것 같아? -> {"type":"chat","msg":"어떤 기능? 좀 더 구체적으로 말해줘!"}
성능 어때? -> {"type":"chat","msg":"현재 응답 속도는 괜찮은 편이야! 느린 부분 있으면 말해줘."}
지금 잘 돌아가고 있어? -> {"type":"chat","msg":"응, 정상 작동 중이야!"}
뭐 할 수 있어? -> {"type":"chat","msg":"대화, 코딩, 검색, 파일 관리, 시스템 모니터링 등 다양하게 할 수 있어!"}
MCP 도구 사용 (외부 서버 도구):
이미지 분석해줘 -> {"type":"run","tool":"mcp","mcp_server":"zai","mcp_tool":"analyze_image","args":{"image_source":"파일경로","prompt":"이미지 분석해줘"}}
C++ 프로젝트 만들고 빌드해서 실행해줘 -> {"type":"run","tool":"project_create","name":"my_tool","lang":"cpp","files":[{"path":"main.cpp","content":"#include <iostream>\\nint main(){std::cout<<\"hello\";}"}],"_next":{"tool":"project_build","name":"my_tool","lang":"cpp","build_type":"executable","_next":{"tool":"shell","cmd":"toolpacks/runtime_plugins/my_tool"}}}
GPU 확인하고 디스크도 봐줘 -> {"type":"run","tool":"shell","cmd":"nvidia-smi","_next":{"tool":"shell","cmd":"df -h"}}

멀티스텝 체이닝 (여러 작업이 필요할 때):
- _next 필드로 다음 단계를 미리 지정할 수 있음
- 프로젝트 만들고 빌드하고 실행 → _next로 체이닝
- 예: {"type":"run","tool":"project_create",...,"_next":{"tool":"project_build","name":"calc","lang":"cpp","build_type":"executable"}}
- _next가 또 _next를 가질 수 있음 (체인)
- 단일 작업이면 _next 없이 그냥 출력
- 확실하지 않으면 _next 쓰지 마. 없으면 시스템이 알아서 판단함

규칙:
- 일상 대화, 인사, 감정, 잡담은 chat으로. 도구 불필요.
- ★★★ 핵심 규칙: 도구 이름이 메시지에 있어도, "실행해줘/해줘/보여줘/돌려봐" 같은 명령이 아니면 chat! ★★★
  - 질문("~어때?", "~괜찮아?", "~충분해?", "~될까?", "~맞아?", "~있어?", "~알아?")은 무조건 chat!
  - 명령("~해줘", "~실행", "~보여줘", "~돌려봐", "~해봐")만 run!
  - 확실하지 않으면 chat으로! 잘못 실행하면 사용자가 짜증남.
- 사실 모르면 search. 추측하지 마. search query는 반드시 영어로!
- 웹검색/뉴스/날씨/가격 조회 → search 도구 우선! 단, 사용자가 playwright/requests/크롤링 명시 요청 시 code 도구 사용 OK (네트워크 승인 후 실행됨)
- 명령어는 단순하게. 주석/플레이스홀더 금지.
- 구구단/피보나치/소수/정렬/계산/수열 = 무조건 code 도구! 절대 chat 금지.
- code: 한국어 문자열 금지. 영어+숫자만. \\n으로 줄바꿈.
- code: f-string 금지. str()+연결. input() 금지.
- code: 들여쓰기 4칸. for/if 뒤 콜론 필수.
- util_save: sys.argv로 인자. 재사용 가능하게."""

# ---------------------------------------------------------------------------
# Phase 2: Conversation prompt (자연스러운 한국어 대화)
# ---------------------------------------------------------------------------
CHAT_PROMPT = """너는 마키나(Machina), 뭐든 도와주는 범용 AI야. 반말로 편하게 대화해.
경험이 쌓일수록 더 똑똑해져. 사용자와 함께 성장하는 AI.
규칙: 한국어, 짧고 자연스럽게 (500자 이내), 이모지 적당히.
{memory}"""

# ---------------------------------------------------------------------------
# Phase 2: Summary prompt (실행 결과 요약)
# ---------------------------------------------------------------------------
SUMMARY_PROMPT = """도구 실행 결과를 한국어로 요약해. 반말로 편하게.
규칙: 핵심만 3-5문장, 숫자/데이터 포함, 에러면 원인+해결책, 성공이면 의미 설명."""

# ---------------------------------------------------------------------------
# Phase 3: Continue prompt (관찰→재판단: 추가 작업 필요한지 결정)
# ---------------------------------------------------------------------------
CONTINUE_PROMPT = """이전 도구 실행 결과를 보고 판단해. 사용자의 원래 요청이 완전히 달성될 때까지 계속해.

결과가 충분하면: {"type":"done","summary":"한국어 요약"}
추가 작업 필요하면: 원래 도구 형식으로 다음 명령 (JSON)

판단:
- 에러 → 원인 분석 후 다른 방법으로 수정/재시도. 바로 포기하지 마!
- 에러 예: 컴파일 에러 → 코드 수정 후 다시 빌드, 파일 없음 → 파일 생성 먼저
- 불완전 → 다음 단계 명령
- 정보 부족 → search로 검색해서 알아낸 후 이어서 작업
- 완료 → done + 결과 요약
- 사용자가 여러 작업/도구를 요청했으면 전부 끝날 때까지 하나씩 이어서 해. 아직 안 쓴 도구가 남았으면 done 하지 마!
- "하나씩 다 해봐", "전부 실행해봐" 같은 요청이면 남은 도구 계속 실행

JSON 하나만 출력."""


# ===========================================================================
# Intent Classifier (Phase 1)
# ===========================================================================

def classify_intent(conversation: list, session: dict = None) -> dict:
    """Phase 1: Classify user intent -> structured JSON.

    Uses Ollama format:json when available for guaranteed valid JSON.
    Falls back to text extraction + json_repair for other backends.
    """
    backend = os.getenv("MACHINA_CHAT_BACKEND", "oai_compat")
    messages = _trim_history(conversation, max_turns=6)

    # Inject memory context if available
    memory_ctx = (session or {}).get("memory_context", "")
    wisdom_ctx = (session or {}).get("wisdom", "")
    skill_ctx = (session or {}).get("skill_hint", "")
    prompt = INTENT_PROMPT
    if memory_ctx and memory_ctx != "없음":
        prompt += f"\n\n[기억] {memory_ctx[:300]}"
    if wisdom_ctx:
        prompt += f"\n\n[경험 교훈] {wisdom_ctx[:200]}"
    if skill_ctx:
        prompt += f"\n\n[비슷한 성공] {skill_ctx[:200]}"

    # Skill auto-injection: search skills.jsonl for code-related requests
    # Extract last user message for skill search
    _last_user_text = ""
    for _m in reversed(messages):
        if _m.get("role") == "user":
            _last_user_text = _m.get("content", "")
            break
    if _last_user_text and not skill_ctx:
        # Check if the request looks code-related (lightweight keyword check)
        _lower = _last_user_text.lower()
        _code_keywords = _INTENT_KEYWORDS.get("code", set())
        if any(kw in _lower for kw in _code_keywords):
            _auto_skill = _get_skill_hint(_last_user_text)
            if _auto_skill:
                prompt += f"\n\n참고 스킬:\n{_auto_skill}"

    # MCP tools injection: dynamically add MCP tool descriptions to prompt
    mcp_tools_desc = (session or {}).get("mcp_tools", "")
    if mcp_tools_desc:
        prompt += f"\n\nMCP 도구 (외부 서버):\n{mcp_tools_desc}"
        prompt += '\nMCP 도구 사용: {"type":"run","tool":"mcp","mcp_server":"서버명","mcp_tool":"도구명","args":{"key":"value"}}'

    # DST context injection: provide topic continuity hint to LLM
    if dst_state := (session or {}).get("dst_state"):
        topic = dst_state.get("topic", "")
        if topic:
            prompt += f"\n[현재 주제: {topic}]"
        entities = dst_state.get("entities", [])
        if entities:
            prompt += f"\n[활성 엔티티: {', '.join(entities[:5])}]"

    if backend == "anthropic":
        # Adaptive prompt: English JSON instruction for Claude (more reliable)
        json_prompt = prompt + (
            "\n\nIMPORTANT: Output ONLY a single JSON object. "
            "No markdown, no explanation, no code fences."
        )
        for attempt in range(2):
            try:
                raw = _call_anthropic(json_prompt, messages, temperature=0.0)
                cleaned = _extract_json_from_text(raw)
                result = json.loads(cleaned)
                if isinstance(result, dict) and result.get("type"):
                    return result
                # Got JSON but missing type field — treat as chat
                logger.warning(f"Anthropic returned JSON without type: {cleaned[:100]}")
                return {"type": "chat", "msg": result.get("msg", result.get("content", str(result)))}
            except json.JSONDecodeError:
                logger.warning(f"Anthropic JSON parse failed (attempt {attempt+1}): raw={repr(raw[:200])}")
                if attempt == 0:
                    continue
                # Last resort: Claude returned prose instead of JSON — use as chat
                return {"type": "chat", "msg": raw[:500] if raw else "다시 말해줄래?"}
            except Exception as e:
                logger.error(f"Anthropic intent error (attempt {attempt+1}): {type(e).__name__}: {e}")
                if attempt == 0:
                    continue
                return {"type": "chat", "msg": "Claude 연결에 문제가 있어. 잠시 후 다시 해봐."}

    # Ollama path — use format:json for guaranteed valid JSON
    if _is_ollama():
        try:
            return _call_ollama_json(prompt, messages)
        except Exception as e:
            logger.warning(f"Ollama JSON mode failed, falling back to text: {e}")

    # Generic OAI-compat fallback
    raw = ""
    try:
        raw = _call_oai_compat_text(prompt + "\nJSON만 출력. 다른 텍스트 없이.", messages)
        cleaned = _extract_json_from_text(raw)
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # (4b) Graceful fallback: return raw text as chat, hide parse error
        return {"type": "chat", "msg": raw if raw else "무슨 말인지 잘 모르겠어. 다시 말해줄래?"}
    except Exception:
        # (4b) Graceful fallback: hide connection errors from user
        return {"type": "chat", "msg": "잠시 연결이 불안정해. 다시 시도해줘!"}


# ===========================================================================
# Response Generator (Phase 2)
# ===========================================================================

def generate_chat_response(conversation: list, memory_context: str = "") -> str:
    """Phase 2a: Generate natural Korean conversation response."""
    backend = os.getenv("MACHINA_CHAT_BACKEND", "oai_compat")
    messages = _trim_history(conversation, max_turns=10)
    mem_str = f"\n과거 기억:\n{memory_context[:500]}" if memory_context else ""
    prompt = CHAT_PROMPT.format(memory=mem_str)

    if backend == "anthropic":
        try:
            return _call_anthropic(prompt, messages)
        except Exception as e:
            logger.error(f"Anthropic chat error: {e}")
            return "미안, Claude 연결에 문제가 있어. 잠시 후 다시 해봐."

    if _is_ollama():
        return _call_ollama_text(prompt, messages)

    return _call_oai_compat_text(prompt, messages)


def generate_summary(conversation: list, results: list) -> str:
    """Phase 2b: Summarize tool execution results in Korean."""
    backend = os.getenv("MACHINA_CHAT_BACKEND", "oai_compat")
    results_text = json.dumps(results, ensure_ascii=False, indent=1)[:3000]
    messages = _trim_history(conversation, max_turns=3)
    messages.append({
        "role": "user",
        "content": f"실행 결과야. 핵심만 한국어로 요약해:\n{results_text}"
    })

    if backend == "anthropic":
        try:
            return _call_anthropic(SUMMARY_PROMPT, messages)
        except Exception as e:
            logger.error(f"Anthropic summary error: {e}")
            return "요약 실패 — 원본 결과를 확인해줘."

    if _is_ollama():
        return _call_ollama_text(SUMMARY_PROMPT, messages)

    return _call_oai_compat_text(SUMMARY_PROMPT, messages)


# ===========================================================================
# Main Entry Points
# ===========================================================================

def handle_intent(payload: dict) -> dict:
    """Handle intent mode — Phase 1 classification + mapping to Machina actions."""
    conversation = list(payload.get("conversation", []))
    session = payload.get("session", {})

    # Extract last user message (needed for fast path + normalization)
    user_msg = ""
    for m in reversed(conversation):
        if m.get("role") == "user":
            user_msg = m.get("content", "")
            break

    # --- 3-tier fast path (LLM-free) ---
    fp = resolve_intent_fast(user_msg)
    if fp:
        return fp

    # Phase 1: Classify intent (LLM call)
    intent = classify_intent(conversation, session)

    # --- Post-LLM guardrail: meta-questions override action → chat ---
    # If LLM classified as action/run but user message is a question, force chat
    if user_msg and _is_meta_question(user_msg):
        itype = intent.get("type", "")
        if itype in ("run",) or (itype == "action"):
            logger.info(f"Guardrail OVERRIDE: LLM said '{itype}' but message is a "
                        f"question → forcing chat: {user_msg[:60]}")
            # Return empty content → telegram_bot.py will call LLM in chat mode
            # This gives a natural conversational response instead of a canned one
            intent = {"type": "chat", "msg": ""}

    # Map to Machina action format
    return _intent_to_machina_action(intent, user_msg=user_msg)


def handle_summary(payload: dict) -> dict:
    """Handle summary mode — Phase 2b result summarization."""
    conversation = list(payload.get("conversation", []))
    results = payload.get("action_results", [])

    try:
        content = generate_summary(conversation, results)
        return {"content": content}
    except Exception as e:
        return {"content": f"요약 오류: {e}"}


def handle_chat(payload: dict) -> dict:
    """Handle chat mode — Phase 2a natural conversation."""
    conversation = list(payload.get("conversation", []))
    memory = payload.get("session", {}).get("memory_context", "")

    try:
        content = generate_chat_response(conversation, memory)
        return {"type": "reply", "content": content}
    except Exception as e:
        return {"type": "reply", "content": f"대화 오류: {e}"}


def classify_continue(conversation: list, observation: str, session: dict = None) -> dict:
    """Phase 3: Given tool results (observation), decide next action or done.

    Returns either:
      {"type":"done","summary":"..."} — task complete
      {"type":"run","tool":"...","cmd":"..."} — need another action
    """
    backend = os.getenv("MACHINA_CHAT_BACKEND", "oai_compat")

    # Build messages: original conversation + observation
    messages = _trim_history(conversation, max_turns=4)
    messages.append({
        "role": "assistant",
        "content": "(도구 실행 완료)"
    })
    messages.append({
        "role": "user",
        "content": f"실행 결과:\n{observation}\n\n위 결과를 보고 추가 작업이 필요한지 판단해."
    })

    prompt = CONTINUE_PROMPT
    # Inject wisdom for better continue decisions
    wisdom_ctx = (session or {}).get("wisdom", "")
    if wisdom_ctx:
        prompt += f"\n\n[경험 교훈] {wisdom_ctx[:150]}"

    # Inject used-tools context for multi-step awareness
    used_tools = (session or {}).get("used_tools", [])
    cycle_num = (session or {}).get("cycle_num", 0)
    if used_tools:
        used_str = ", ".join(t.split(".")[-2] if "." in t else t for t in used_tools)
        prompt += f"\n\n[진행 상황] 사이클 {cycle_num}, 사용한 도구: {used_str}"
        # Show available tool categories to help LLM pick the next one
        all_tool_types = {"shell", "search", "code", "memory_save", "memory_find",
                          "file_read", "file_write", "file_list", "web", "genesis",
                          "util_list", "util_run", "pip_list", "project_create"}
        # Map AIDs to simple names
        used_simple = set()
        for t in used_tools:
            parts = t.replace("AID.", "").split(".")
            if len(parts) >= 2:
                used_simple.add(parts[0].lower() + "_" + parts[1].lower())
                used_simple.add(parts[1].lower())
        remaining = [t for t in sorted(all_tool_types) if not any(
            s in t for s in used_simple)]
        if remaining:
            prompt += f"\n아직 안 쓴 도구: {', '.join(remaining[:10])}"

    # Inject MCP tools into continue context too
    mcp_tools_desc = (session or {}).get("mcp_tools", "")
    if mcp_tools_desc:
        prompt += f"\n\nMCP 도구 (외부 서버):\n{mcp_tools_desc}"
        prompt += '\nMCP 도구 사용: {"type":"run","tool":"mcp","mcp_server":"서버명","mcp_tool":"도구명","args":{"key":"value"}}'

    if backend == "anthropic":
        raw = ""
        try:
            raw = _call_anthropic(
                prompt + "\n\nIMPORTANT: Output ONLY a single JSON object. "
                "No markdown, no explanation, no code fences.",
                messages, temperature=0.0
            )
            # Use robust 3-layer extraction (fence strip + bracket match)
            from machina_shared import _extract_json_robust
            cleaned = _extract_json_robust(raw)
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning(f"Anthropic continue JSON failed: raw={repr(raw[:200])}")
            # Last resort: if text shows intent to continue, don't auto-done
            _raw_lower = raw.lower() if raw else ""
            if any(kw in _raw_lower for kw in ("계속", "다음", "이어서", "돌려볼", "next")):
                logger.info("Continue: JSON failed but continuation signal detected")
                return {"type": "continue_signal"}
            return {"type": "done", "summary": observation}
        except Exception as e:
            logger.error(f"Anthropic continue error: {type(e).__name__}: {e}")
            return {"type": "done", "summary": observation}

    if _is_ollama():
        try:
            return _call_ollama_json(prompt, messages, num_predict=2048)
        except Exception as e:
            logger.warning(f"Ollama continue error: {type(e).__name__}: {e}")
            return {"type": "done", "summary": observation}

    try:
        raw = _call_oai_compat_text(prompt + "\nJSON만 출력.", messages)
        cleaned = _extract_json_from_text(raw)
        return json.loads(cleaned)
    except Exception as e:
        logger.warning(f"OAI continue error: {type(e).__name__}: {e}")
        return {"type": "done", "summary": observation}


def handle_continue(payload: dict) -> dict:
    """Handle continue mode — observe results, decide next action or finish."""
    conversation = list(payload.get("conversation", []))
    observation = payload.get("observation", "")
    session = payload.get("session", {})

    # Phase 3: Classify what to do next
    result = classify_continue(conversation, observation, session)

    if result.get("type") == "done":
        return {"type": "done", "content": result.get("summary", observation)}

    # Extract user message for normalization
    user_msg = ""
    for m in reversed(conversation):
        if m.get("role") == "user":
            user_msg = m.get("content", "")
            break

    # It returned a new action intent — map it
    mapped = _intent_to_machina_action(result, user_msg=user_msg)
    return mapped


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        print(json.dumps({"type": "reply", "content": "입력 오류"}))
        return

    mode = payload.get("mode", "intent")

    if mode == "intent":
        result = handle_intent(payload)
    elif mode == "summary":
        result = handle_summary(payload)
    elif mode == "chat":
        result = handle_chat(payload)
    elif mode == "continue":
        result = handle_continue(payload)
    elif mode == "plan":
        result = handle_plan(payload)
    else:
        result = {"type": "reply", "content": f"알 수 없는 모드: {mode}"}

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
