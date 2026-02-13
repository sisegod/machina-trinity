#!/usr/bin/env python3
"""Machina Graph Memory — entity-relation graph with multi-hop traversal.

Memory 2.0: Graph-based knowledge representation layered on top of existing
BM25+Embedding hybrid search. Provides:
  - Entity extraction (Tier 0 regex + Tier 1 noun chunks)
  - Relation extraction from text (subject-predicate-object triples)
  - In-memory adjacency list built from JSONL storage
  - Multi-hop BFS query with beam pruning
  - Exponential time decay (30-day half-life)
  - Conflict resolution (ADD/UPDATE/NOOP)
  - Periodic compaction of JSONL files

Storage: work/memory/entities.jsonl + work/memory/relations.jsonl

GraphMemory class is in machina_graph_memory.py; re-exported here for
backward compatibility.
"""

import logging
import re

from machina_graph_memory import GraphMemory  # noqa: F401 — re-export

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Entity Extraction — Tier 0 (regex) + Tier 1 (heuristic noun chunks)
# ---------------------------------------------------------------------------

# Tier 0: High-precision regex patterns
_ENTITY_PATTERNS = [
    # Korean names (2-4 syllable: common family name + given name, followed by particle)
    # Family names: 김이박최정강조윤장임한오서신권황안송전홍유고문양손배조백허유남심노정하곽성차주우구신임나전민류진장위표명기반왕금옥육인맹제모장남궁탁공도편
    (re.compile(r'(?:^|[\s,.(])((?:김|이|박|최|정|강|조|윤|장|임|한|오|서|신|권|황|안|송|전|홍|유|고|문|양|손|배|백|허|남|심|노|하|곽|성|차|주|구|나|민|류|진|위|표|명|반|왕|금|옥|제|장|궁|탁|공|도|편)[가-힣]{1,2})(?:은|는|이|가|의|을|를|에게|한테|께|와|과|도|이다|이야|이고|이랑)?(?=[\s,.]|$)'), "person"),
    # Email addresses (stop before Korean particles)
    (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+(?=[^a-zA-Z0-9._-]|$)'), "email"),
    # Dates (YYYY-MM-DD, YYYY.MM.DD, YYYY/MM/DD)
    (re.compile(r'\b(\d{4}[./-]\d{1,2}[./-]\d{1,2})\b'), "date"),
    # Korean dates (N월 N일)
    (re.compile(r'(\d{1,2}월\s*\d{1,2}일)'), "date"),
    # URLs
    (re.compile(r'https?://[^\s<>"]+'), "url"),
    # IP addresses
    (re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), "ip"),
    # File paths
    (re.compile(r'(?:/[\w.-]+){2,}'), "path"),
    # Numbers with units (GPU 메모리, 용량 등)
    (re.compile(r'\b(\d+(?:\.\d+)?)\s*(GB|MB|TB|KB|GHz|MHz|fps|ms|초|분|시간|일|개|명|원|달러|%)\b', re.I), "measure"),
    # Technical terms (common in this project)
    (re.compile(r'\b(Python|Ollama|Claude|Anthropic|Qwen|EXAONE|Telegram|BM25|GPU|CUDA|Docker|nginx|Redis|PostgreSQL|MongoDB)\b', re.I), "tech"),
]

# Korean topic markers for relation extraction
_KO_TOPIC_MARKERS = re.compile(r'(은|는|이|가)$')
_KO_OBJECT_MARKERS = re.compile(r'(을|를|에|에서|로|으로|와|과|하고)$')

# Tier 1: Korean noun-like chunk extraction (no external NLP dependency)
_KO_NOUN_PATTERN = re.compile(
    r'(?:^|[\s,.(])'
    r'([가-힣]{2,8})'
    r'(?:은|는|이|가|을|를|의|에|와|과|도|로|으로|에서|까지|부터|처럼|같이|야|이야)?'
    r'(?=[\s,.)]|$)'
)

# Stop words — common Korean particles/verbs that aren't entities
_KO_STOPWORDS = {
    "그리고", "하지만", "그래서", "그러면", "그런데", "때문에", "그것은",
    "이것은", "저것은", "어떻게", "왜냐하면", "그러나", "이미", "아직",
    "정말로", "아마도", "거의", "매우", "조금", "많이", "항상", "가끔",
    "이런", "저런", "그런", "어떤", "모든", "각각", "다른", "같은",
    "있다", "없다", "하다", "되다", "이다", "아니다", "보다", "나다",
    "좋아", "싫어", "알겠", "모르겠", "네가", "내가", "우리",
    "한다", "한데", "한테", "했다", "할수", "하는", "된다", "된건",
    "이런", "그냥", "뭐야", "봐봐", "해줘", "해봐", "할게", "한거",
    # Common false positives from person regex (family name + common syllable)
    "나는", "나도", "나의", "나한테", "나를", "나에게", "나왔다", "나온다", "나갔다",
    "서버", "서비스", "서울", "서로",
    "이것", "이제", "이미", "이건", "이게", "이후",
    "정말", "정보", "정도", "정리",
    "강화", "강조",
    "조금", "조건",
    "주로", "주의", "주요",
    "최근", "최대", "최소", "최적",
    "임시", "임의",
    "장치", "장소", "장점",
    "권한",
    "황금",
    "안전", "안내",
    "배포", "배경", "배열",
    "손실",
    "유지",
    "고정", "고장",
    "문제", "문서", "문자",
    "노드",
    "성능", "성공",
    "차이",
}

# English stopwords
_EN_STOPWORDS = {
    "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "must",
    "and", "but", "or", "not", "no", "yes", "this", "that",
    "with", "for", "from", "into", "about", "just", "also",
    "very", "really", "quite", "much", "many", "some", "any",
    "what", "which", "who", "whom", "when", "where", "how", "why",
}


def extract_entities(text: str) -> list[dict]:
    """Extract entities from text using Tier 0 (regex) + Tier 1 (noun chunks).

    Returns list of {"name": str, "type": str, "source": "t0"|"t1"}.
    Deduplicates by normalized name.
    """
    if not text or len(text) < 2:
        return []

    seen = set()
    entities = []

    def _add(name: str, etype: str, source: str):
        key = name.lower().strip()
        if key and len(key) >= 2 and key not in seen:
            if key not in _KO_STOPWORDS and key not in _EN_STOPWORDS:
                seen.add(key)
                entities.append({"name": name.strip(), "type": etype, "source": source})

    # Tier 0: regex patterns (high precision)
    for pattern, etype in _ENTITY_PATTERNS:
        for m in pattern.finditer(text):
            val = m.group(1) if m.lastindex else m.group(0)
            # Skip matches that are actually stopwords (e.g. 한다 matching 한 + 다)
            if val.lower().strip() in _KO_STOPWORDS:
                continue
            _add(val, etype, "t0")

    # Tier 1: Korean noun chunks (broader recall)
    # Filter verb/adjective endings that look like nouns
    _ko_verb_endings = re.compile(r'(한다|하고|해서|하는|된다|된건|됐다|했다|했어|할수|있다|없다|이다|이야|일까|인데|하면|에서|까지|부터|라고|처럼|같이|대로|이라)$')
    for m in _KO_NOUN_PATTERN.finditer(text):
        noun = m.group(1)
        if len(noun) >= 2 and noun not in _KO_STOPWORDS:
            if _ko_verb_endings.search(noun):
                continue
            _add(noun, "concept", "t1")

    # Tier 1: English words (capitalized = likely entity)
    for m in re.finditer(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b', text):
        word = m.group(1)
        if word.lower() not in _EN_STOPWORDS and len(word) >= 3:
            _add(word, "concept", "t1")

    return entities


def extract_relations(text: str, entities: list[dict]) -> list[dict]:
    """Extract relations between entities from text.

    Heuristic: entities co-occurring in same sentence are related.
    Tries to infer predicate from text between entity mentions.

    Returns list of {"source": str, "target": str, "predicate": str, "confidence": float}.
    """
    if len(entities) < 2:
        return []

    relations = []
    entity_names = [e["name"] for e in entities]

    # Split into sentences (Korean + English)
    sentences = re.split(r'[.!?。]\s*|\n', text)

    for sent in sentences:
        if not sent.strip():
            continue
        # Find which entities appear in this sentence
        present = []
        for ent in entity_names:
            if ent.lower() in sent.lower():
                present.append(ent)

        # Create pairwise relations for co-occurring entities
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                src, tgt = present[i], present[j]
                # Try to extract predicate from text between mentions
                predicate = _extract_predicate(sent, src, tgt)
                confidence = 0.7 if predicate != "related_to" else 0.4
                relations.append({
                    "source": src,
                    "target": tgt,
                    "predicate": predicate,
                    "confidence": confidence,
                })

    # Deduplicate by (source, target, predicate)
    seen = set()
    unique = []
    for r in relations:
        key = (r["source"].lower(), r["target"].lower(), r["predicate"])
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return unique


def _extract_predicate(sentence: str, src: str, tgt: str) -> str:
    """Extract predicate between two entity mentions in a sentence.

    Heuristic: look for Korean predicates (은/는 ... 이다/하다)
    or English verbs between mentions.
    """
    lower = sentence.lower()
    src_idx = lower.find(src.lower())
    tgt_idx = lower.find(tgt.lower())
    if src_idx < 0 or tgt_idx < 0:
        return "related_to"

    # Get text between the two entity mentions
    start = min(src_idx + len(src), tgt_idx + len(tgt))
    end = max(src_idx, tgt_idx)
    if start >= end:
        return "related_to"
    between = sentence[start:end].strip()
    between_clean = re.sub(r'[은는이가을를의에와과도로]', '', between).strip()

    # Korean predicate patterns
    ko_predicates = {
        "좋아": "likes", "싫어": "dislikes",
        "사용": "uses", "쓰": "uses",
        "만들": "created", "생성": "created", "작성": "created",
        "실행": "runs", "설치": "installed",
        "연결": "connected_to", "의존": "depends_on",
        "포함": "contains", "속": "belongs_to",
        "생일": "birthday_is", "이름": "named",
        "살": "lives_in", "거주": "lives_in",
        "직업": "works_as", "일하": "works_at",
    }
    for kw, pred in ko_predicates.items():
        if kw in between:
            return pred

    # English verb extraction
    en_verbs = re.findall(r'\b(is|are|was|has|uses|likes|runs|created|installed|lives|works|depends|contains)\b',
                          between, re.I)
    if en_verbs:
        return en_verbs[0].lower()

    return "related_to"


# ---------------------------------------------------------------------------
# High-level API — used by machina_learning.py integration
# ---------------------------------------------------------------------------

# Singleton graph instance
_graph = GraphMemory()

# Import BFS default from the memory module for use in graph_query signature
from machina_graph_memory import _DEFAULT_MAX_HOPS  # noqa: E402


def graph_ingest(text: str, metadata: dict = None) -> dict:
    """Extract entities and relations from text and add to graph.

    This is the main entry point for automatic graph population.
    Called from memory_save() and auto-memory detection.

    Returns {"entities_added": int, "relations_added": int}.
    """
    if not text or len(text) < 5:
        return {"entities_added": 0, "relations_added": 0}

    try:
        entities = extract_entities(text)
        if not entities:
            return {"entities_added": 0, "relations_added": 0}

        # Add entities
        ent_count = 0
        for ent in entities[:20]:  # cap per-text extraction
            _graph.add_entity(ent["name"], ent["type"], metadata)
            ent_count += 1

        # Extract and add relations
        relations = extract_relations(text, entities)
        rel_count = 0
        for rel in relations[:15]:  # cap per-text
            _graph.add_relation(
                rel["source"], rel["target"],
                rel["predicate"], rel["confidence"],
            )
            rel_count += 1

        return {"entities_added": ent_count, "relations_added": rel_count}
    except Exception as e:
        logger.error(f"[Graph] Ingest error: {e}")
        return {"entities_added": 0, "relations_added": 0}


def graph_query(query: str, max_hops: int = _DEFAULT_MAX_HOPS,
                limit: int = 5) -> str:
    """Query graph memory and return formatted context string.

    Used for LLM context injection alongside BM25 search results.
    """
    try:
        return _graph.format_context(query, limit=limit)
    except Exception as e:
        logger.error(f"[Graph] Query error: {e}")
        return ""


def graph_query_neighbors(name: str, predicate: str = None,
                          limit: int = 10) -> list[dict]:
    """Query direct neighbors of an entity."""
    try:
        return _graph.query_neighbors(name, predicate, limit)
    except Exception as e:
        logger.error(f"[Graph] Neighbor query error: {e}")
        return []


def graph_stats() -> dict:
    """Return graph statistics."""
    try:
        return _graph.get_stats()
    except Exception as e:
        logger.error(f"[Graph] Stats error: {e}")
        return {"entities": 0, "relations": 0, "error": str(e)}


def graph_compact():
    """Force compaction of graph JSONL files."""
    try:
        _graph.compact()
    except Exception as e:
        logger.error(f"[Graph] Manual compaction error: {e}")
