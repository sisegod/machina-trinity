#!/usr/bin/env python3
"""Machina Chat Driver Utilities — DST, Entity Extraction, History, Plan.

Extracted from chat_driver.py to keep each module under 600 lines.

Contains:
- Dialogue State Tracking (DST): track_dialogue_state, extract_entities
- Skill auto-injection: _get_skill_hint
- Post-parse guardrail: _is_meta_question
- History management: _trim_history, _compress_old_messages
- Plan generation: PLAN_PROMPT, classify_plan, handle_plan
"""

import json
import os
import re
import sys
import logging
from collections import Counter

logger = logging.getLogger("chat_driver")


# ===========================================================================
# Dialogue State Tracking (DST) — lightweight, no LLM call
# ===========================================================================

# Stop words excluded from topic keyword extraction
_DST_STOP_WORDS = frozenset({
    "이", "그", "저", "뭐", "좀", "해", "줘", "봐", "거", "것", "수", "할",
    "하다", "있다", "없다", "되다", "않다", "the", "a", "an", "is", "are",
    "to", "of", "in", "for", "and", "or", "it", "do", "can", "me", "my",
    "you", "your", "this", "that", "what", "how", "please", "help",
})

# Korean verb/request suffixes to filter out non-topical words
_KOREAN_VERB_SUFFIXES = (
    "해줘", "해봐", "할래", "할게", "하자", "해요", "합니다",
    "어줘", "아줘", "여줘", "줘봐", "해라", "해줄래",
    "인지", "은지", "인가", "인데", "이야", "이다",
    "방법도", "방법이", "방법을",
)

# Intent type keywords for classification tracking
_INTENT_KEYWORDS = {
    "search": {"검색", "찾아", "search", "알려줘", "뭐야", "누구", "어디", "언제"},
    "code": {"코드", "짜줘", "프로그램", "피보나치", "소수", "구구단", "정렬", "계산",
             "code", "python", "algorithm", "코딩"},
    "shell": {"명령", "메모리", "GPU", "디스크", "프로세스", "shell", "nvidia",
              "시스템", "서버"},
    "file": {"파일", "읽어", "써줘", "저장", "file", "write", "read", "work/"},
    "memory": {"기억", "memory", "remember", "전에", "기억해"},
    "chat": {"안녕", "고마워", "잘", "ㅎㅎ", "ㅋㅋ", "hello", "hi", "thanks"},
}


def track_dialogue_state(conversation: list, current_state: dict = None) -> dict:
    """Lightweight DST: extract current topic, active entities, and intent continuity.

    No LLM call needed -- uses keyword matching and message analysis.
    Returns: {"topic": str, "entities": list, "intent_chain": list, "turn_count": int}
    """
    state = current_state or {
        "topic": "",
        "entities": [],
        "intent_chain": [],
        "turn_count": 0,
    }

    # Collect recent user messages (last 3)
    user_msgs = []
    for msg in reversed(conversation):
        if msg.get("role") == "user":
            user_msgs.append(msg.get("content", ""))
            if len(user_msgs) >= 3:
                break
    user_msgs.reverse()

    if not user_msgs:
        return state

    # --- Topic extraction: dominant keywords from recent messages ---
    word_counts = Counter()
    for text in user_msgs:
        # Tokenize: split on whitespace and common punctuation
        tokens = re.findall(r'[가-힣]{2,}|[a-zA-Z_][a-zA-Z0-9_./-]{2,}|\d{3,}', text)
        for token in tokens:
            lower = token.lower()
            if lower in _DST_STOP_WORDS or len(lower) < 2:
                continue
            # Filter Korean verb/request forms (non-topical)
            if any(lower.endswith(suffix) for suffix in _KOREAN_VERB_SUFFIXES):
                continue
            word_counts[lower] += 1

    # Pick top keyword as topic
    new_topic = ""
    if word_counts:
        top_keywords = word_counts.most_common(3)
        new_topic = top_keywords[0][0]

    # --- Intent chain: classify recent messages by type ---
    intent_chain = list(state.get("intent_chain", []))[-4:]  # keep last 4
    latest_msg = user_msgs[-1] if user_msgs else ""
    detected_intent = "chat"  # default
    best_score = 0
    for intent_type, keywords in _INTENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in latest_msg.lower())
        if score > best_score:
            best_score = score
            detected_intent = intent_type
    intent_chain.append(detected_intent)
    if len(intent_chain) > 5:
        intent_chain = intent_chain[-5:]

    # --- Turn count: same topic continuity ---
    prev_topic = state.get("topic", "")
    if new_topic and new_topic == prev_topic:
        turn_count = state.get("turn_count", 0) + 1
    elif new_topic:
        turn_count = 1
    else:
        turn_count = state.get("turn_count", 0) + 1

    # --- Entity aggregation from recent messages ---
    all_entities = []
    for text in user_msgs:
        ent = extract_entities(text)
        for category_items in ent.values():
            all_entities.extend(category_items)
    # Deduplicate while preserving order
    seen = set()
    unique_entities = []
    for e in all_entities:
        if e not in seen:
            seen.add(e)
            unique_entities.append(e)

    return {
        "topic": new_topic if new_topic else prev_topic,
        "entities": unique_entities[:10],  # cap at 10
        "intent_chain": intent_chain,
        "turn_count": turn_count,
    }


# ===========================================================================
# Entity Extraction -- regex-based, no LLM call
# ===========================================================================

# Compiled patterns for entity extraction
_RE_FILE_PATH = re.compile(
    r'(?:work/|toolpacks/|policies/|tools/|runtime_plugins/)'
    r'[a-zA-Z0-9_./-]+\.[a-zA-Z0-9]{1,10}'
    r'|[a-zA-Z0-9_./-]+\.(?:py|cpp|json|txt|sh|md|yaml|yml|toml|cfg|log|csv|jsonl|so|h|hpp)'
)
_RE_URL = re.compile(r'https?://[^\s<>"\')\]]+')
_RE_SIGNIFICANT_NUM = re.compile(
    r'\b\d{1,3}(?:\.\d{1,3}){3}\b'   # IP addresses (e.g. 192.168.1.1)
    r'|\b\d{4,5}\b'                    # ports / large numbers (1000+)
    r'|\b\d+\.\d+\.\d+\b'             # versions (e.g. 3.12.1)
)
_RE_KOREAN_NAME = re.compile(
    r'(?:이름|name|성명)[은는이가:\s]*([가-힣]{2,4})'
)
_RE_TOOL_NAME = re.compile(
    r'\b(?:AID\.[A-Z_.]+\.v\d+|'
    r'(?:shell|search|memory_save|memory_find|file_read|file_write|genesis|'
    r'config|code|web|util_save|util_run|util_list|util_delete|util_update|'
    r'file_list|file_search|file_diff|file_edit|file_append|file_delete|'
    r'project_create|project_build|pip_install|pip_uninstall|pip_list))\b'
)


def extract_entities(text: str) -> dict:
    """Extract entities from text: files, URLs, numbers, names.

    Returns: {"files": [...], "urls": [...], "numbers": [...], "names": [...]}
    """
    if not text:
        return {"files": [], "urls": [], "numbers": [], "names": []}

    files = list(set(_RE_FILE_PATH.findall(text)))
    urls = list(set(_RE_URL.findall(text)))
    numbers = list(set(_RE_SIGNIFICANT_NUM.findall(text)))

    # Names: Korean names after name-indicating keywords + tool names
    names = list(set(_RE_KOREAN_NAME.findall(text)))
    tool_names = list(set(_RE_TOOL_NAME.findall(text)))
    names.extend(tool_names)

    return {
        "files": files[:10],
        "urls": urls[:5],
        "numbers": numbers[:10],
        "names": names[:10],
    }


# ===========================================================================
# Skill Auto-Injection Helper
# ===========================================================================

# ===========================================================================
# LLM-free Fast Path — keyword-based intent routing (skips LLM call)
# ===========================================================================

# Maps keyword patterns → (intent_type, tool, aid) tuples.
# Confidence: exact command match → 1.0, keyword match → 0.8.
# Only activates when confidence >= 0.8 (single dominant match).
_FAST_PATH_RULES = {
    # Shell / system status queries
    "shell": {
        "keywords": {"메모리", "GPU", "디스크", "프로세스", "nvidia", "uptime", "df", "top",
                      "free", "시스템", "서버 상태", "uname", "lsb_release"},
        "result": ("action", "shell", "AID.SHELL.EXEC.v1"),
    },
    # File read
    "file_read": {
        "keywords": {"읽어", "읽어줘", "내용", "보여줘", "열어줘", "cat"},
        "path_required": True,
        "result": ("action", "file_read", "AID.FILE.READ.v1"),
    },
    # Web search
    "search": {
        "keywords": {"검색", "찾아줘", "알아봐", "search", "뉴스", "최신"},
        "result": ("action", "search", "AID.NET.WEB_SEARCH.v1"),
    },
    # Memory save
    "memory_save": {
        "keywords": {"기억해", "기억해줘", "remember", "저장해", "메모해"},
        "result": ("action", "memory_save", "AID.MEMORY.APPEND.v1"),
    },
    # Memory query
    "memory_find": {
        "keywords": {"기억", "전에", "아까", "memory", "recall"},
        "excludes": {"기억해", "기억해줘", "저장"},  # avoid collision with memory_save
        "result": ("action", "memory_find", "AID.MEMORY.QUERY.v1"),
    },
    # Workspace cleanup request (safe junk patterns only)
    "cleanup_files": {
        "keywords": {"쓸데없는 파일", "불필요한 파일", "임시 파일 정리", "파일 정리", "정리해줘", "cleanup files"},
        "result": ("action", "shell", "AID.SHELL.EXEC.v1"),
    },
}

# File-path indicators (fast path for file_read)
_RE_FILE_INDICATOR = re.compile(r'(?:work/|\.(?:py|json|txt|md|jsonl|csv|log|yaml|yml|sh|cpp|h))\b')
_RE_FAST_FILE_PATH = re.compile(r'(?:work/)?[a-zA-Z0-9_./-]+\.(?:py|json|txt|md|jsonl|csv|log|yaml|yml|sh|cpp|h)')


def try_fast_path(user_text: str) -> dict:
    """Attempt LLM-free intent classification via keyword matching.

    Returns intent dict if confident (single dominant match), else empty dict.
    Empty dict → caller falls through to LLM intent classification.
    """
    if not user_text or len(user_text.strip()) < 2:
        return {}

    text_lower = user_text.lower().strip()

    # Reject if message looks like a question about capabilities (meta-question)
    if _is_meta_question(user_text):
        return {}

    # Score each fast path rule
    scores = {}
    for rule_name, rule in _FAST_PATH_RULES.items():
        kw_hits = sum(1 for kw in rule["keywords"] if kw.lower() in text_lower)
        if kw_hits == 0:
            continue
        # Check exclusions
        excludes = rule.get("excludes", set())
        if excludes and any(ex in text_lower for ex in excludes):
            continue
        # File read requires a file path indicator
        if rule.get("path_required") and not _RE_FILE_INDICATOR.search(user_text):
            continue
        scores[rule_name] = kw_hits

    if not scores:
        return {}

    # Only proceed if there's a single dominant match (no ambiguity)
    sorted_scores = sorted(scores.items(), key=lambda x: -x[1])
    if len(sorted_scores) >= 2 and sorted_scores[0][1] == sorted_scores[1][1]:
        return {}  # ambiguous → let LLM decide

    best_rule = sorted_scores[0][0]
    intent_type, tool, aid = _FAST_PATH_RULES[best_rule]["result"]

    # Build minimal inputs for dispatch-executable action.
    # Fast path must return kind="tool" for execute_intent() compatibility.
    cmd = ""
    text_lower = user_text.lower()
    if best_rule == "shell":
        if "gpu" in text_lower or "nvidia" in text_lower:
            cmd = "nvidia-smi"
        elif "메모리" in text_lower or "free" in text_lower:
            cmd = "free -h"
        elif "디스크" in text_lower or "df" in text_lower:
            cmd = "df -h"
        elif "프로세스" in text_lower or "top" in text_lower:
            cmd = "ps aux --sort=-%mem | head -20"
        else:
            cmd = "uname -a"
    elif best_rule == "cleanup_files":
        cmd = (
            "set -euo pipefail; "
            "echo '[cleanup] start'; "
            "find work/scripts -maxdepth 1 -type f "
            "\\( -name 'run_*.py' -o -name 'run_*.sh' \\) -print -delete 2>/dev/null || true; "
            "find work -maxdepth 1 -type f "
            "\\( -name '*.bak' -o -name 'self_test.txt' -o -name 'test_all_tools.txt' -o -name 'test_tool.txt' -o -name 'test_value' -o -name 'max_test_*' -o -name 'machina_max_test_*' \\) "
            "-print -delete 2>/dev/null || true; "
            "find . -type d -name '__pycache__' -prune -print -exec rm -rf {} + 2>/dev/null || true; "
            "echo '[cleanup] remaining top-level work files:'; "
            "ls -la work | sed -n '1,120p'; "
            "echo '[cleanup] done'"
        )

    inputs = {}
    if best_rule in ("shell", "cleanup_files"):
        inputs = {"cmd": cmd}
    elif best_rule == "search":
        inputs = {"query": user_text.strip()}
    elif best_rule == "memory_save":
        inputs = {"stream": "telegram", "text": user_text.strip(), "event": "user_note"}
    elif best_rule == "memory_find":
        inputs = {"query": user_text.strip(), "stream": "telegram", "top_k": 5}
    elif best_rule == "file_read":
        m = _RE_FAST_FILE_PATH.search(user_text)
        if m:
            inputs = {"path": m.group(0), "max_bytes": 8192}
        else:
            return {}

    # Build intent dict matching chat_driver.py output format
    return {
        "type": intent_type,
        "actions": [{"kind": "tool", "aid": aid, "inputs": inputs}],
        "content": "",
        "_fast_path": best_rule,  # marker for logging/metrics
    }


# Distill tool name → canonical AID mapping
_TOOL_AID_MAP = {
    "shell": "AID.SHELL.EXEC.v1",
    "file_read": "AID.FILE.READ.v1",
    "file_write": "AID.FILE.WRITE.v1",
    "search": "AID.NET.WEB_SEARCH.v1",
    "web_search": "AID.NET.WEB_SEARCH.v1",
    "memory_save": "AID.MEMORY.APPEND.v1",
    "memory_find": "AID.MEMORY.QUERY.v1",
    "memory_query": "AID.MEMORY.QUERY.v1",
    "code_exec": "AID.CODE.EXEC.v1",
    "genesis": "AID.GENESIS.WRITE_FILE.v1",
}


def resolve_intent_fast(user_text: str) -> dict:
    """3-tier LLM-free intent resolution: fast_path → distill → empty.
    Returns intent dict if resolved, empty dict if LLM needed."""
    fp = try_fast_path(user_text)
    if fp:
        return fp
    _parent = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if _parent not in sys.path:
        sys.path.insert(0, _parent)
    from machina_learning import lookup_distilled
    tool, conf = lookup_distilled(user_text)
    if tool and conf >= 0.8:
        aid = _TOOL_AID_MAP.get(tool, tool)
        return {"type": "action",
                "actions": [{"kind": "tool", "aid": aid, "inputs": {}}],
                "content": "", "_fast_path": f"distill:{conf}"}
    return {}


def _get_skill_hint(user_text: str) -> str:
    """Search skills.jsonl for similar code and return formatted hint.

    Lazy-imports skill_search from machina_learning (parent dir).
    Returns formatted code example if match score > 0.3, else "".
    Truncated to 500 chars max. Graceful on any failure.
    """
    if not user_text or len(user_text.strip()) < 3:
        return ""
    try:
        # chat_driver runs with CWD=policies/, machina_learning is in parent dir
        import sys as _sys
        _parent = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if _parent not in _sys.path:
            _sys.path.insert(0, _parent)
        from machina_learning import skill_search
        result = skill_search(user_text, limit=1)
        if result and len(result.strip()) > 10:
            return result.strip()[:500]
        return ""
    except Exception as e:
        logger.debug(f"Skill hint lookup failed: {e}")
        return ""


# ===========================================================================
# Post-parse Guardrail: Question-form -> force chat
# ===========================================================================

# Korean question endings that indicate "asking about" rather than "commanding"
_QUESTION_ENDINGS = (
    "어때", "어때?", "괜찮아", "괜찮아?", "충분해", "충분해?",
    "될까", "될까?", "맞아", "맞아?", "있어", "있어?", "알아", "알아?",
    "인가", "인가?", "인지", "인지?", "할까", "할까?", "볼까", "볼까?",
    "같아", "같아?", "하나", "하나?", "건가", "건가?",
    "가능해", "가능해?", "작동해", "작동해?", "돼", "돼?",
    "어떠니", "어떠니?",
    "잘 되나", "잘 되나?",
)

# Explicit command suffixes -- these ARE action requests, NOT questions
_COMMAND_SUFFIXES = (
    "해줘", "해봐", "해라", "하자", "실행", "보여줘", "돌려봐", "돌려줘",
    "열어줘", "읽어줘", "써줘", "저장해", "삭제해", "지워줘", "설치해",
    "만들어줘", "빌드해", "검색해", "찾아줘", "알려줘", "가르쳐줘",
    "뭐 있어", "뭐있어", "뭐 있냐", "뭐있냐",  # "list" requests
)


def _is_meta_question(text: str) -> bool:
    """Detect if the user message is a question ABOUT capabilities/tools,
    not a command to execute them.

    Returns True if the message is a meta-question -> should be chat.
    Returns False if the message is a command -> let LLM decide.
    """
    if not text:
        return False
    stripped = text.strip().rstrip("?\uff1f")
    lower = text.lower().strip()

    # If it ends with explicit command suffixes -> NOT a question
    if any(lower.endswith(s) for s in _COMMAND_SUFFIXES):
        return False

    # If it ends with question mark -> strong signal
    has_qmark = text.strip().endswith("?") or text.strip().endswith("\uff1f")

    # If it ends with Korean question endings -> strong signal
    has_q_ending = any(stripped.endswith(q.rstrip("?")) for q in _QUESTION_ENDINGS)

    if not has_qmark and not has_q_ending:
        return False

    # Additional check: does it contain tool/capability keywords?
    # If asking about tools/capabilities -> definitely meta question
    _meta_keywords = (
        "도구", "기능", "뭐 할 수", "할 수 있", "가능", "지원", "작동",
        "리스트", "목록", "충분", "괜찮", "잘 되", "잘되", "돌아가",
        "성능", "상태", "어때",
    )
    if any(kw in lower for kw in _meta_keywords):
        return True

    # Has question ending but no meta keyword -> still might be a question
    # Check if there's NO actionable content (no file paths, no code, no URLs)
    has_path = bool(re.search(r'work/|\.py|\.txt|\.cpp|\.json', lower))
    has_url = bool(re.search(r'https?://', lower))
    has_code_content = bool(re.search(r'def |import |class |for |while |print\(', lower))
    if has_path or has_url or has_code_content:
        return False

    # Pure question with question ending + no actionable content -> chat
    return has_q_ending or has_qmark


# ===========================================================================
# History Management
# ===========================================================================

def _trim_history(messages: list, max_turns: int = 10) -> list:
    """Sliding window + summary: keep recent turns verbatim, compress old ones.

    When history exceeds max_turns, older messages are compressed into a
    brief context summary (keyword extraction, no LLM call needed).
    This preserves topic continuity across long conversations.
    """
    # Filter to user/assistant only
    filtered = [m for m in messages if m.get("role") in ("user", "assistant")]
    if len(filtered) <= max_turns * 2:
        return filtered

    # Split: old messages to summarize, recent messages to keep
    keep_count = max_turns * 2
    old_msgs = filtered[:-keep_count]
    recent_msgs = filtered[-keep_count:]

    # Extract key context from old messages (lightweight, no LLM)
    summary = _compress_old_messages(old_msgs)
    if summary:
        # Inject summary as first user message for context
        context_msg = {"role": "user", "content": f"[이전 대화 요약] {summary}"}
        ack_msg = {"role": "assistant", "content": "네, 이전 맥락 이해했어."}
        return [context_msg, ack_msg] + recent_msgs

    return recent_msgs


def _compress_old_messages(messages: list) -> str:
    """Extract key facts from old messages without LLM call.

    Extracts: user requests, mentioned entities (files, URLs, numbers),
    and any tool/action keywords for topic continuity.
    """
    if not messages:
        return ""

    topics = []
    entities = set()

    for msg in messages:
        content = msg.get("content", "")[:300]
        role = msg.get("role", "")

        if role == "user" and content.strip():
            # Extract the core request (first sentence or line)
            first_line = content.split("\n")[0].strip()
            if first_line and len(first_line) > 3:
                topics.append(first_line[:80])

            # Extract entities: file paths, URLs, numbers
            for path in re.findall(r'[\w./]+\.\w{1,5}', content):
                if len(path) > 4:
                    entities.add(path)
            for url in re.findall(r'https?://\S+', content):
                entities.add(url[:60])

    parts = []
    if topics:
        # Keep unique topics, max 5
        seen = set()
        unique_topics = []
        for t in topics:
            key = t[:20].lower()
            if key not in seen:
                seen.add(key)
                unique_topics.append(t)
        parts.append("요청: " + " -> ".join(unique_topics[:5]))
    if entities:
        parts.append("참조: " + ", ".join(sorted(entities)[:5]))

    result = "; ".join(parts)
    return result[:400] if result else ""


# ===========================================================================
# Phase 4: Plan Generation (multi-step execution plan)
# ===========================================================================

PLAN_PROMPT = """사용자가 여러 단계 작업을 요청했어. 실행할 도구 목록을 JSON으로 만들어.

도구:
- shell: 시스템 명령 (cmd)
- search: 웹 검색 (query, 영어)
- code: 코드 실행 (lang=python|bash, code)
- memory_save: 기억 저장 (text)
- memory_find: 기억 검색 (text)
- file_write: 파일 쓰기 (path=work/..., content)
- file_read: 파일 읽기 (path)
- file_list: 디렉토리 목록 (path)
- web: URL 읽기 (url)
- util_list: 유틸 목록
{mcp_tools}

출력 (JSON만):
{{"type":"plan","steps":[
  {{"tool":"shell","cmd":"명령","desc":"설명"}},
  {{"tool":"search","query":"english query","desc":"설명"}},
  {{"tool":"code","lang":"python","code":"print(1)","desc":"설명"}}
]}}

규칙:
- step당 도구 1개. 의미 있는 명령 생성
- code: \\n줄바꿈, f-string 금지, 한국어 문자열 금지
- search query: 영어
- 최대 12 steps"""


def classify_plan(conversation: list, session: dict = None) -> dict:
    """Generate a multi-step execution plan from user request.

    Returns {"type":"plan","steps":[{"tool":"...","desc":"..."},...]}
    Lazy-imports LLM call functions to avoid circular deps at module level.
    """
    # Lazy imports — these are in the same policies/ directory
    from chat_llm import (
        _call_ollama_json, _call_oai_compat_text,
        _call_anthropic, _is_ollama, _extract_json_from_text,
    )

    backend = os.getenv("MACHINA_CHAT_BACKEND", "oai_compat")
    messages = _trim_history(conversation, max_turns=4)

    prompt = PLAN_PROMPT
    mcp_tools_desc = (session or {}).get("mcp_tools", "")
    if mcp_tools_desc:
        prompt = prompt.replace("{mcp_tools}",
                                f"MCP 도구:\n{mcp_tools_desc}\n"
                                'MCP: {{"tool":"mcp","mcp_server":"서버","mcp_tool":"도구","args":{{}}}}'
                                )
    else:
        prompt = prompt.replace("{mcp_tools}", "")

    json_instruction = (
        "\n\nIMPORTANT: Output ONLY a valid JSON object with type and steps array. "
        "No markdown, no explanation, no code fences."
    )

    if backend == "anthropic":
        try:
            raw = _call_anthropic(prompt + json_instruction, messages, temperature=0.0)
            _parent = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            if _parent not in sys.path:
                sys.path.insert(0, _parent)
            from machina_shared import _extract_json_robust
            cleaned = _extract_json_robust(raw)
            return json.loads(cleaned)
        except Exception as e:
            logger.warning(f"Plan generation failed: {type(e).__name__}: {e}")
            return {}

    if _is_ollama():
        try:
            return _call_ollama_json(prompt, messages, num_predict=4096)
        except Exception:
            return {}

    try:
        raw = _call_oai_compat_text(prompt + "\nJSON만 출력.", messages)
        cleaned = _extract_json_from_text(raw)
        return json.loads(cleaned)
    except Exception:
        return {}


def handle_plan(payload: dict) -> dict:
    """Handle plan mode -- generate multi-step execution plan."""
    conversation = list(payload.get("conversation", []))
    session = payload.get("session", {})
    result = classify_plan(conversation, session)
    if result.get("type") == "plan" and result.get("steps"):
        return result
    return {"type": "error", "content": "Plan generation failed"}
