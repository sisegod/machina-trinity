"""Machina Autonomic Engine — Deep web search helpers (DDGS + LLM summary)."""

import json
import logging
import time

from machina_shared import (
    _jsonl_append,
    _call_engine_llm,
    MEM_DIR, INSIGHTS_STREAM,
)
from machina_autonomic._constants import (
    KNOWLEDGE_STREAM,
    WEB_MAX_RESULTS, WEB_DEEP_READ_URLS, WEB_PAGE_CONTENT_LEN,
    WEB_SUMMARY_TOKENS, STORE_SUMMARY_LEN,
    _audit_log, logger,
)

try:
    from ddgs import DDGS
except ImportError:
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

_ACTION_MARKERS = ["사용해", "적용", "개선", "수정", "추가", "설정",
                   "install", "configure", "implement", "optimize",
                   "변경", "교체", "업그레이드", "패턴"]


def _ddgs_search(query: str, max_results: int = WEB_MAX_RESULTS) -> list:
    """Raw DDGS search — returns list of result dicts."""
    if DDGS is None:
        return []
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with DDGS() as ddgs:
                return list(ddgs.text(query, region="wt-wt", max_results=max_results))
    except Exception as e:
        logger.debug(f"[DDGS] Search failed: {e}")
        return []


def _fetch_page(url: str) -> str:
    """Fetch a single URL, return clean text or empty string."""
    try:
        from machina_tools import run_machina_http_get
        text = run_machina_http_get(url)
        if text and not text.startswith(("curl error", "HTTP error")):
            return text[:WEB_PAGE_CONTENT_LEN]
    except Exception as e:
        logger.debug(f"[DeepWeb] Fetch failed {url}: {e}")
    return ""


def _deep_web_search(query: str, reason: str, event_tag: str,
                     max_rounds: int = 3, goal: str = "") -> dict:
    """Multi-round deep web search — like a human researcher.

    Round 1: Search initial query -> read top pages -> LLM evaluates
    Round 2+: If LLM says "insufficient", it generates a refined query -> search again
    Keeps accumulated knowledge across rounds. Stops when:
      - LLM says content is "sufficient" for the goal
      - max_rounds reached
      - 3+ pages of real content collected
    """
    if DDGS is None:
        return {"success": False, "detail": "DDGS 미설치"}

    all_page_contents = []
    all_sources = []
    queries_tried = [query]
    current_query = query
    results = []  # initialize before loop to prevent NameError

    for round_n in range(max_rounds):
        results = _ddgs_search(current_query)
        if not results:
            if round_n == 0:
                return {"success": False, "detail": f"'{current_query}' 검색 결과 없음"}
            break

        new_pages = 0
        for r in results[:WEB_DEEP_READ_URLS + round_n]:
            url = r.get("href", "")
            if not url or url in all_sources:
                continue
            content = _fetch_page(url)
            if content and len(content) > 100:
                all_page_contents.append(f"[{r.get('title', 'page')}]\n{content}")
                all_sources.append(url)
                new_pages += 1

        logger.info(f"[DeepWeb] R{round_n+1}: '{current_query}' -> {len(results)} hits, "
                    f"{new_pages} new pages (total {len(all_page_contents)})")

        if len(all_page_contents) >= 3 or round_n == max_rounds - 1:
            break

        if all_page_contents:
            preview = "\n".join(p[:500] for p in all_page_contents[-2:])
        else:
            preview = "\n".join(f"- {r.get('title','')}: {r.get('body','')[:150]}"
                                for r in results[:3])

        refine_prompt = (
            f"목표: '{goal or reason}'\n"
            f"지금까지 '{current_query}'로 검색해서 {len(all_page_contents)}페이지 읽음.\n"
            f"내용 미리보기:\n{preview[:800]}\n\n"
            "이 내용이 목표에 충분한가?\n"
            '충분하면: {"sufficient":true}\n'
            '부족하면: {"sufficient":false,"next_query":"더 나은 영어 검색어","why":"부족한 이유"}\n'
            'JSON만.'
        )
        raw = _call_engine_llm(refine_prompt, system="검색 품질 판단기. JSON만 출력.",
                           max_tokens=200, temperature=0.3, format_json=True, think=False)
        try:
            decision = json.loads(raw)
            if decision.get("sufficient"):
                break
            next_q = decision.get("next_query", "")
            if next_q and next_q not in queries_tried:
                current_query = next_q
                queries_tried.append(next_q)
                logger.info(f"[DeepWeb] Refining: '{next_q}' (reason: {decision.get('why','')})")
            else:
                break
        except (json.JSONDecodeError, TypeError):
            break

    if all_page_contents:
        llm_input = "\n\n---\n\n".join(all_page_contents)
        prompt = (
            f"다음은 '{query}'에 대해 {len(all_page_contents)}개 웹 페이지를 읽은 내용이다.\n"
            f"목표: {goal or reason}\n\n"
            "실용적 인사이트, howto, 구체적 절차/코드/설정을 한국어 5줄로 요약해. "
            "일반론은 빼고 즉시 실행 가능한 내용만:\n\n" + llm_input[:20000]
        )
    else:
        snippets = "\n".join(f"- {r.get('title','')}: {r.get('body','')[:200]}"
                             for r in results[:3])
        prompt = f"다음 검색 결과에서 실용적 인사이트를 한국어 3줄로 요약해:\n\n{snippets}"

    summary = _call_engine_llm(
        prompt,
        system="한국어 기술 요약기. 핵심만 간결하게. 구체적 코드/명령어/설정 우선.",
        max_tokens=WEB_SUMMARY_TOKENS, temperature=0.3, think=False)

    knowledge_entry = {
        "ts_ms": int(time.time() * 1000),
        "event": event_tag, "stream": KNOWLEDGE_STREAM,
        "query": query, "reason": reason,
        "queries_tried": queries_tried,
        "rounds": len(queries_tried),
        "results_count": len(all_sources),
        "summary": summary[:STORE_SUMMARY_LEN],
        "sources": all_sources[:5],
        "deep_read": len(all_page_contents) > 0,
        "pages_read": len(all_page_contents),
    }
    _jsonl_append(MEM_DIR / f"{KNOWLEDGE_STREAM}.jsonl", knowledge_entry)

    # Knowledge -> Insight bridge: if actionable
    if any(m in summary.lower() for m in _ACTION_MARKERS):
        _jsonl_append(MEM_DIR / f"{INSIGHTS_STREAM}.jsonl", {
            "ts_ms": int(time.time() * 1000),
            "event": "insight", "stream": INSIGHTS_STREAM,
            "type": "knowledge_actionable",
            "topic": query[:40],
            "reason": reason,
            "reflection": summary[:STORE_SUMMARY_LEN],
            "action_hint": f"웹에서 학습한 '{query[:30]}' 적용 가능",
            "source": "knowledge_bridge",
        })

    return {
        "success": True,
        "summary": summary,
        "sources": all_sources[:5],
        "results_count": len(all_sources),
        "pages_read": len(all_page_contents),
        "rounds": len(queries_tried),
        "queries_tried": queries_tried,
        "detail": f"'{query}' -> {len(all_sources)}건, {len(all_page_contents)}페이지, {len(queries_tried)}라운드\n{summary[:400]}",
    }
