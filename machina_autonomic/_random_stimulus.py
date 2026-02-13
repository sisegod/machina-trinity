"""Machina Autonomic Engine -- RandomStimulus class.

Anti-stasis exploration via deterministic pool-based stimulus generation.
"""

import hashlib
import json
import logging
import random
import time

from machina_shared import (
    _jsonl_append, _jsonl_read,
    _call_engine_llm, _load_manifest_tools_full,
    MEM_DIR,
    EXPERIENCE_STREAM, INSIGHTS_STREAM,
)
from machina_autonomic._constants import logger

# ---------------------------------------------------------------------------
# RandomStimulus -- anti-stasis exploration (pool-based, deterministic)
# ---------------------------------------------------------------------------
class RandomStimulus:
    """Deterministic stimulus pool -- prevents idle stasis without LLM self-judgment.

    Pool categories rotate via round-robin with done-hash dedup.
    Fires ONLY when _pick_next_action() finds zero productive work.
    """
    DONE_FILE = MEM_DIR / "stimulus_done.jsonl"

    CATEGORIES = {
        "tool_challenge": [
            # Removed: empty input (100% fail), max-size input (75% fail, ASK tools)
            {"action": "test", "desc": "Chain 2 random tools: output of A feeds input of B"},
            {"action": "test", "desc": "Run tool with unicode/emoji-heavy input"},
            {"action": "test", "desc": "Run tool with nested JSON 5 levels deep"},
            {"action": "test", "desc": "Test memory_save then memory_find round-trip"},
        ],
        "knowledge_quest": [],  # populated dynamically by _refresh_knowledge_quests()
        "cross_domain": [
            {"action": "integration", "desc": "Memory write -> query -> verify round-trip"},
            {"action": "integration", "desc": "Genesis create tool -> manifest register -> invoke"},
            {"action": "integration", "desc": "Experience record -> reflect -> insight extraction"},
            {"action": "integration", "desc": "Skill create -> trust score -> hygiene prune lifecycle"},
            {"action": "integration", "desc": "Web search -> summarize -> store in knowledge stream"},
        ],
        "optimization": [
            {"action": "benchmark", "desc": "Time all registered tools, log p50/p99"},
            {"action": "benchmark", "desc": "Measure memory file sizes, report growth rate"},
            {"action": "benchmark", "desc": "Count duplicate entries across all JSONL streams"},
            {"action": "benchmark", "desc": "Profile curriculum get_rates() latency"},
            {"action": "benchmark", "desc": "Check disk usage of work/ directory"},
        ],
    }

    def __init__(self):
        self._cat_order = list(self.CATEGORIES.keys())
        self._cat_idx = 0
        self._done_hashes = set()
        self._load_done()
        self._refresh_knowledge_quests()

    def _load_done(self):
        if self.DONE_FILE.exists():
            for entry in _jsonl_read(self.DONE_FILE, max_lines=500):
                self._done_hashes.add(entry.get("hash", ""))

    def _refresh_knowledge_quests(self):
        """Dynamically populate knowledge_quest from system state."""
        queries = []

        # 1. From failed experiences -- search for solutions
        exps = _jsonl_read(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl", max_lines=50)
        fail_tools = {}
        for e in exps:
            if not e.get("success") and e.get("tool_used"):
                t = e["tool_used"]
                fail_tools[t] = fail_tools.get(t, 0) + 1
        for tool, count in sorted(fail_tools.items(), key=lambda x: -x[1])[:3]:
            queries.append({
                "action": "web",
                "query": f"{tool.split('.')[-2] if '.' in tool else tool} error handling best practices",
                "hypothesis": f"도구 '{tool}' 실패율 개선 방법 ({count}회 실패)",
            })

        # 2. From untested manifest tools -- find docs
        manifest = _load_manifest_tools_full()
        used_aids = {e.get("tool_used", "") for e in exps}
        untested = [t for t in manifest if t["aid"] not in used_aids]
        for t in untested[:3]:
            queries.append({
                "action": "web",
                "query": f"{t['name']} tool usage documentation API",
                "hypothesis": f"미사용 도구 '{t['name']}' 활용법 학습",
            })

        # 3. From recent insights -- deepen understanding (with relevance filter)
        _IRRELEVANT_TOPICS = {"etcd", "kraft", "statefulset", "kubernetes", "raft",
                              "consul", "zookeeper", "kafka", "grpc", "protobuf",
                              "terraform", "ansible", "helm", "istio", "envoy"}
        insights = _jsonl_read(MEM_DIR / f"{INSIGHTS_STREAM}.jsonl", max_lines=5)
        for ins in insights[-2:]:
            topic = ins.get("topic", "")[:30]
            while topic.startswith("검색 결과:") or topic.startswith("검색 결과: "):
                topic = topic.split(":", 1)[-1].strip()
            words = topic.split()
            has_dup_words = len(words) != len(set(w.lower() for w in words))
            # Reject irrelevant infrastructure topics (prevents rabbit holes)
            if any(irr in topic.lower() for irr in _IRRELEVANT_TOPICS):
                continue
            if topic and len(topic) > 10 and not has_dup_words:
                queries.append({
                    "action": "web",
                    "query": f"{topic} advanced techniques optimization",
                    "hypothesis": f"인사이트 '{topic}' 심화 학습",
                })

        # Fallback: always have at least 4 items
        base_queries = [
            {"action": "web", "query": "autonomous agent self-improvement loop architecture 2025",
             "hypothesis": "자율 학습 아키텍처 최신 패턴"},
            {"action": "web", "query": "Python sandboxed code execution best practices Linux",
             "hypothesis": "코드 실행 보안 강화"},
            {"action": "web", "query": "JSONL memory system scaling optimization",
             "hypothesis": "메모리 성능 병목 해결"},
            {"action": "web", "query": "LLM agent tool selection accuracy improvement",
             "hypothesis": "도구 선택 정확도 개선"},
        ]
        for bq in base_queries:
            if len(queries) < 8:
                queries.append(bq)

        self.CATEGORIES["knowledge_quest"] = queries

    def _hash(self, stim: dict) -> str:
        return hashlib.md5(json.dumps(stim, sort_keys=True).encode()).hexdigest()[:12]

    def pick(self) -> dict | None:
        """Randomized category + item selection with round-robin fallback."""
        # Cooldown check: after 2nd pool exhaustion, wait 5 minutes
        now = time.time()
        cooldown_until = getattr(self, '_stim_cooldown_until', 0)
        if now < cooldown_until:
            return None
        # Cooldown expired -- allow a fresh cycle
        if cooldown_until > 0 and now >= cooldown_until:
            self._stim_cooldown_until = 0
            self._reset_count = 0

        cats = list(self._cat_order)
        random.shuffle(cats)
        for cat in cats:
            items = list(self.CATEGORIES[cat])
            random.shuffle(items)
            for stim in items:
                h = self._hash(stim)
                if h not in self._done_hashes:
                    return {**stim, "category": cat, "hash": h}
        # All static items exhausted -> try dynamic generation (1 attempt)
        dyn = self._generate_dynamic_stimulus()
        if dyn:
            return dyn
        # Pool fully exhausted -- reset once per session, then signal stall
        if not getattr(self, '_reset_count', 0):
            self._reset_count = 1
            self._done_hashes.clear()
            self._dyn_gen_count = 0
            self._refresh_knowledge_quests()
            if self.DONE_FILE.exists():
                try:
                    self.DONE_FILE.unlink()
                except OSError as e:
                    logger.debug(f"OSError: {e}")
                    pass
            logger.info("[Stimulus] Pool exhausted -> reset (1st cycle)")
            cat = random.choice(cats)
            items = self.CATEGORIES[cat]
            if items:
                stim = random.choice(items)
                h = self._hash(stim)
                return {**stim, "category": cat, "hash": h}
        # 2nd+ exhaustion -> cooldown 5 minutes, then allow fresh cycle
        self._stim_cooldown_until = time.time() + 300
        logger.info("[Stimulus] Pool exhausted after reset -> 5min cooldown")
        return None

    def _generate_dynamic_stimulus(self) -> dict | None:
        """LLM-generated novel stimulus when static pool is exhausted."""
        dyn_count = getattr(self, '_dyn_gen_count', 0)
        if dyn_count >= 5:
            return None
        try:
            manifest = _load_manifest_tools_full()
            _recently_tested = set()
            for h in self._done_hashes:
                pass
            done_file = self.DONE_FILE
            if done_file.exists():
                recent_done = _jsonl_read(done_file, max_lines=20)
                for rd in recent_done:
                    desc = rd.get("desc", "").lower()
                    for t in manifest:
                        if t["name"].lower() in desc:
                            _recently_tested.add(t["name"])
            available = [t for t in manifest[:15] if t["name"] not in _recently_tested]
            if len(available) < 3:
                available = manifest[:15]
            tool_names = [f"{t['name']}({t['aid']})" for t in available]
            tool_ctx = ", ".join(tool_names) if tool_names else "MEMORY.QUERY, CODE.EXEC, FILE.READ, etc."
            avoid_hint = ""
            if _recently_tested:
                avoid_hint = f"\n이미 테스트한 도구(피해라): {', '.join(list(_recently_tested)[:5])}\n"
            prompt = (
                "자율 학습 에이전트의 새로운 자가 테스트/실험 아이디어를 하나 생성해.\n"
                f"사용 가능한 실제 도구: {tool_ctx}\n"
                "반드시 위 도구만 사용하는 테스트를 생성해. 존재하지 않는 도구 금지.\n"
                f"{avoid_hint}"
                "카테고리: tool_challenge, knowledge_quest, cross_domain 중 하나.\n"
                "tool_challenge: 위 도구 중 하나를 테스트 (empty/max/unicode/chain).\n"
                "knowledge_quest: 도구 활용법이나 시스템 개선 관련 웹 검색.\n"
                "cross_domain: 2개 이상 도구 조합 테스트.\n"
                '{"category":"카테고리","action":"test|web|integration",'
                '"desc":"설명(영어, 위 도구명 포함)","query":"검색어(영어,web일때만)"}\nJSON만.'
            )
            raw = _call_engine_llm(prompt, system="JSON만 출력.", max_tokens=300,
                               temperature=0.3, format_json=True, think=False)
            data = json.loads(raw)
            cat = data.get("category", "cross_domain")
            if cat not in self.CATEGORIES:
                cat = "cross_domain"
            stim = {
                "action": data.get("action", "test"),
                "desc": data.get("desc", "dynamic stimulus"),
            }
            if data.get("query"):
                stim["query"] = data["query"]
            h = self._hash(stim)
            if h not in self._done_hashes:
                self._dyn_gen_count = dyn_count + 1
                return {**stim, "category": cat, "hash": h}
        except Exception as e:
            logger.debug(f"[DynStimulus] generation failed: {type(e).__name__}: {e}")
        return None

    def mark_done(self, stim: dict):
        h = stim.get("hash", self._hash(stim))
        self._done_hashes.add(h)
        _jsonl_append(self.DONE_FILE, {
            "ts_ms": int(time.time() * 1000),
            "hash": h, "category": stim.get("category"),
            "desc": stim.get("desc", stim.get("query", ""))[:100],
        })
