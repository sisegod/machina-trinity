#!/usr/bin/env python3
"""Machina Graph Memory — GraphMemory class (JSONL-backed in-memory graph).

Entities: {id, name, type, aliases, first_seen_ms, last_seen_ms, mention_count, metadata}
Relations: {id, source_id, target_id, predicate, weight, first_seen_ms, last_seen_ms, mention_count}

Adjacency list: entity_id -> [(neighbor_id, relation_id, predicate, weight)]

Storage: work/memory/entities.jsonl + work/memory/relations.jsonl
"""

import fcntl
import hashlib
import json
import logging
import math
import os
import time
from collections import defaultdict

from machina_shared import _jsonl_append, _jsonl_read, MEM_DIR, BM25Okapi

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ENTITIES_FILE = MEM_DIR / "entities.jsonl"
RELATIONS_FILE = MEM_DIR / "relations.jsonl"

# Time decay: half-life 30 days -> lambda = ln(2)/30
_DECAY_LAMBDA = math.log(2) / 30.0  # per-day decay constant
_DECAY_FLOOR = 0.05  # minimum weight (never fully forget)

# Graph limits (anti-explosion)
_MAX_ENTITIES = 5000
_MAX_RELATIONS = 20000
_MAX_RELATIONS_PER_ENTITY = 50
_COMPACTION_THRESHOLD = 200  # compact after N appends since last compaction

# BFS defaults
_DEFAULT_MAX_HOPS = 2
_DEFAULT_BEAM_WIDTH = 10


class GraphMemory:
    """In-memory graph built from JSONL files.

    Entities: {id, name, type, aliases, first_seen_ms, last_seen_ms, mention_count, metadata}
    Relations: {id, source_id, target_id, predicate, weight, first_seen_ms, last_seen_ms, mention_count}

    Adjacency list: entity_id -> [(neighbor_id, relation_id, predicate, weight)]
    """

    def __init__(self):
        self._entities: dict[str, dict] = {}  # id -> entity
        self._name_index: dict[str, str] = {}  # lowercase name -> entity id
        self._relations: dict[str, dict] = {}  # id -> relation
        self._adj: dict[str, list] = defaultdict(list)  # entity_id -> [(neighbor_id, rel_id, pred, weight)]
        self._loaded = False
        self._append_count = 0  # track appends for compaction trigger
        self._last_compaction = 0.0

    def _entity_id(self, name: str) -> str:
        """Generate deterministic entity ID from name."""
        return hashlib.sha256(name.lower().strip().encode()).hexdigest()[:16]

    def _relation_id(self, src_id: str, tgt_id: str, predicate: str) -> str:
        """Generate deterministic relation ID."""
        key = f"{src_id}:{tgt_id}:{predicate}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def load(self):
        """Load graph from JSONL files into memory."""
        if self._loaded:
            return
        self._entities.clear()
        self._name_index.clear()
        self._relations.clear()
        self._adj.clear()

        # Load entities
        if ENTITIES_FILE.exists():
            entries = _jsonl_read(ENTITIES_FILE)
            for e in entries:
                eid = e.get("id", "")
                if not eid:
                    continue
                # Later entries override earlier ones (compacted state)
                self._entities[eid] = e
                name_key = e.get("name", "").lower().strip()
                if name_key:
                    self._name_index[name_key] = eid
                # Also index aliases
                for alias in e.get("aliases", []):
                    self._name_index[alias.lower().strip()] = eid

        # Load relations
        if RELATIONS_FILE.exists():
            entries = _jsonl_read(RELATIONS_FILE)
            for r in entries:
                rid = r.get("id", "")
                if not rid:
                    continue
                self._relations[rid] = r

        # Build adjacency list
        self._rebuild_adjacency()
        self._loaded = True

        ent_count = len(self._entities)
        rel_count = len(self._relations)
        if ent_count > 0 or rel_count > 0:
            logger.info(f"[Graph] Loaded {ent_count} entities, {rel_count} relations")

    def _rebuild_adjacency(self):
        """Rebuild adjacency list from relations."""
        self._adj.clear()
        for rid, r in self._relations.items():
            src = r.get("source_id", "")
            tgt = r.get("target_id", "")
            pred = r.get("predicate", "related_to")
            weight = r.get("weight", 1.0)
            if src and tgt:
                self._adj[src].append((tgt, rid, pred, weight))
                self._adj[tgt].append((src, rid, pred, weight))  # bidirectional

    def add_entity(self, name: str, etype: str = "concept",
                   metadata: dict = None) -> str:
        """Add or update an entity. Returns entity ID.

        Conflict resolution: if entity exists, update last_seen and increment count.
        """
        self.load()
        name_key = name.lower().strip()
        existing_id = self._name_index.get(name_key)

        now_ms = int(time.time() * 1000)

        if existing_id and existing_id in self._entities:
            # UPDATE: increment mention count, refresh timestamp
            ent = self._entities[existing_id]
            ent["last_seen_ms"] = now_ms
            ent["mention_count"] = ent.get("mention_count", 1) + 1
            if metadata:
                ent.setdefault("metadata", {}).update(metadata)
            # Persist update
            _jsonl_append(ENTITIES_FILE, ent)
            self._append_count += 1
            return existing_id

        # Enforce entity limit
        if len(self._entities) >= _MAX_ENTITIES:
            self._prune_entities()

        # ADD: new entity
        eid = self._entity_id(name)
        ent = {
            "id": eid,
            "name": name.strip(),
            "type": etype,
            "aliases": [],
            "first_seen_ms": now_ms,
            "last_seen_ms": now_ms,
            "mention_count": 1,
            "metadata": metadata or {},
        }
        self._entities[eid] = ent
        self._name_index[name_key] = eid
        _jsonl_append(ENTITIES_FILE, ent)
        self._append_count += 1
        self._maybe_compact()
        return eid

    def add_relation(self, source_name: str, target_name: str,
                     predicate: str = "related_to",
                     confidence: float = 0.7) -> str:
        """Add or strengthen a relation. Returns relation ID.

        Conflict resolution: if relation exists, strengthen weight and refresh timestamp.
        """
        self.load()

        # Ensure both entities exist
        src_id = self._name_index.get(source_name.lower().strip())
        tgt_id = self._name_index.get(target_name.lower().strip())
        if not src_id:
            src_id = self.add_entity(source_name)
        if not tgt_id:
            tgt_id = self.add_entity(target_name)

        rid = self._relation_id(src_id, tgt_id, predicate)
        now_ms = int(time.time() * 1000)

        if rid in self._relations:
            # UPDATE: strengthen weight, refresh timestamp
            rel = self._relations[rid]
            rel["last_seen_ms"] = now_ms
            rel["mention_count"] = rel.get("mention_count", 1) + 1
            # Strengthen weight: approach 1.0 asymptotically
            old_w = rel.get("weight", 0.5)
            rel["weight"] = min(1.0, old_w + (1.0 - old_w) * 0.2)
            _jsonl_append(RELATIONS_FILE, rel)
            self._append_count += 1
            return rid

        # Enforce relation limits
        if len(self._relations) >= _MAX_RELATIONS:
            self._prune_relations()
        src_rels = len(self._adj.get(src_id, []))
        if src_rels >= _MAX_RELATIONS_PER_ENTITY:
            self._prune_entity_relations(src_id)

        # ADD: new relation
        rel = {
            "id": rid,
            "source_id": src_id,
            "target_id": tgt_id,
            "source_name": source_name.strip(),
            "target_name": target_name.strip(),
            "predicate": predicate,
            "weight": confidence,
            "first_seen_ms": now_ms,
            "last_seen_ms": now_ms,
            "mention_count": 1,
        }
        self._relations[rid] = rel
        self._adj[src_id].append((tgt_id, rid, predicate, confidence))
        self._adj[tgt_id].append((src_id, rid, predicate, confidence))
        _jsonl_append(RELATIONS_FILE, rel)
        self._append_count += 1
        self._maybe_compact()
        return rid

    def _time_decay_weight(self, last_seen_ms: int) -> float:
        """Calculate time decay factor: W = max(floor, e^(-lambda * days_ago))."""
        now_ms = int(time.time() * 1000)
        days_ago = (now_ms - last_seen_ms) / (1000 * 86400)
        if days_ago <= 0:
            return 1.0
        return max(_DECAY_FLOOR, math.exp(-_DECAY_LAMBDA * days_ago))

    def query_subgraph(self, seed_names: list[str],
                       max_hops: int = _DEFAULT_MAX_HOPS,
                       beam_width: int = _DEFAULT_BEAM_WIDTH,
                       min_weight: float = 0.1) -> dict:
        """Multi-hop BFS traversal from seed entities.

        Returns {
            "entities": [entity_dicts],
            "relations": [relation_dicts],
            "paths": [[entity_name, predicate, entity_name, ...]],
        }
        """
        self.load()

        # Resolve seed names to IDs
        seed_ids = set()
        for name in seed_names:
            eid = self._name_index.get(name.lower().strip())
            if eid:
                seed_ids.add(eid)

        if not seed_ids:
            return {"entities": [], "relations": [], "paths": []}

        visited_entities = set(seed_ids)
        visited_relations = set()
        paths = []
        frontier = [(eid, [self._entities[eid]["name"]]) for eid in seed_ids
                     if eid in self._entities]

        for hop in range(max_hops):
            next_frontier = []
            for eid, path in frontier:
                neighbors = self._adj.get(eid, [])
                # Score neighbors by: relation_weight * time_decay
                scored = []
                for nid, rid, pred, base_weight in neighbors:
                    if nid in visited_entities:
                        continue
                    rel = self._relations.get(rid, {})
                    decay = self._time_decay_weight(rel.get("last_seen_ms", 0))
                    score = base_weight * decay
                    if score >= min_weight:
                        scored.append((nid, rid, pred, score, path))

                # Beam pruning: keep top-K neighbors
                scored.sort(key=lambda x: -x[3])
                for nid, rid, pred, score, parent_path in scored[:beam_width]:
                    visited_entities.add(nid)
                    visited_relations.add(rid)
                    ent = self._entities.get(nid, {})
                    new_path = parent_path + [pred, ent.get("name", nid)]
                    paths.append(new_path)
                    next_frontier.append((nid, new_path))

            frontier = next_frontier
            if not frontier:
                break

        # Collect results
        result_entities = [self._entities[eid] for eid in visited_entities
                          if eid in self._entities]
        result_relations = [self._relations[rid] for rid in visited_relations
                           if rid in self._relations]

        return {
            "entities": result_entities,
            "relations": result_relations,
            "paths": paths,
        }

    def query_entity(self, name: str) -> dict | None:
        """Look up a single entity by name."""
        self.load()
        eid = self._name_index.get(name.lower().strip())
        if eid and eid in self._entities:
            return dict(self._entities[eid])
        return None

    def query_neighbors(self, name: str, predicate: str = None,
                        limit: int = 10) -> list[dict]:
        """Get direct neighbors of an entity, optionally filtered by predicate."""
        self.load()
        eid = self._name_index.get(name.lower().strip())
        if not eid:
            return []

        neighbors = []
        for nid, rid, pred, weight in self._adj.get(eid, []):
            if predicate and pred != predicate:
                continue
            rel = self._relations.get(rid, {})
            decay = self._time_decay_weight(rel.get("last_seen_ms", 0))
            ent = self._entities.get(nid, {})
            if ent:
                neighbors.append({
                    "entity": ent.get("name", ""),
                    "type": ent.get("type", ""),
                    "predicate": pred,
                    "weight": round(weight * decay, 3),
                    "mention_count": rel.get("mention_count", 1),
                })
        neighbors.sort(key=lambda x: -x["weight"])
        return neighbors[:limit]

    def search_entities(self, query: str, limit: int = 5) -> list[dict]:
        """BM25 search over entity names and metadata."""
        self.load()
        if not self._entities:
            return []

        ent_list = list(self._entities.values())
        texts = []
        for e in ent_list:
            parts = [e.get("name", "")]
            parts.extend(e.get("aliases", []))
            parts.append(e.get("type", ""))
            meta = e.get("metadata", {})
            if isinstance(meta, dict):
                parts.extend(str(v) for v in meta.values())
            texts.append(" ".join(parts))

        bm25 = BM25Okapi()
        bm25.index(texts)
        hits = bm25.query(query, top_k=limit)

        results = []
        for idx, score in hits:
            if score < 0.05:
                continue
            ent = dict(ent_list[idx])
            ent["_search_score"] = round(score, 3)
            results.append(ent)
        return results

    def format_context(self, query: str, limit: int = 5) -> str:
        """Generate compact text context from graph for LLM injection.

        1. Search entities matching query
        2. Expand 1-hop neighbors
        3. Format as readable text
        """
        self.load()
        if not self._entities:
            return ""

        # Search for relevant entities
        matched = self.search_entities(query, limit=limit)
        if not matched:
            return ""

        seed_names = [e["name"] for e in matched]
        subgraph = self.query_subgraph(seed_names, max_hops=1, beam_width=5)

        lines = []
        # Entity facts
        for ent in subgraph["entities"][:limit]:
            name = ent.get("name", "")
            etype = ent.get("type", "")
            count = ent.get("mention_count", 1)
            if etype and etype != "concept":
                lines.append(f"{name} ({etype}, x{count})")
            elif count > 1:
                lines.append(f"{name} (x{count})")

        # Relation summaries
        seen_rels = set()
        for rel in subgraph["relations"][:limit * 2]:
            src = rel.get("source_name", "")
            tgt = rel.get("target_name", "")
            pred = rel.get("predicate", "related_to")
            key = f"{src}-{pred}-{tgt}"
            if key not in seen_rels:
                seen_rels.add(key)
                lines.append(f"{src} → {pred} → {tgt}")

        if not lines:
            return ""

        return "[graph] " + " | ".join(lines[:8])

    # --- Pruning & Compaction ---

    def _prune_entities(self):
        """Remove lowest-weight entities when limit exceeded."""
        if len(self._entities) < _MAX_ENTITIES:
            return
        # Score by: mention_count * time_decay
        scored = []
        for eid, ent in self._entities.items():
            decay = self._time_decay_weight(ent.get("last_seen_ms", 0))
            score = ent.get("mention_count", 1) * decay
            scored.append((eid, score))
        scored.sort(key=lambda x: x[1])

        # Remove bottom 20%
        remove_count = len(scored) // 5
        for eid, _ in scored[:remove_count]:
            name_key = self._entities[eid].get("name", "").lower().strip()
            self._name_index.pop(name_key, None)
            del self._entities[eid]
            # Remove associated relations
            for nid, rid, pred, w in list(self._adj.get(eid, [])):
                self._relations.pop(rid, None)
            self._adj.pop(eid, None)

        self._rebuild_adjacency()
        logger.info(f"[Graph] Pruned {remove_count} entities (limit={_MAX_ENTITIES})")

    def _prune_relations(self):
        """Remove lowest-weight relations when limit exceeded."""
        if len(self._relations) < _MAX_RELATIONS:
            return
        scored = []
        for rid, rel in self._relations.items():
            decay = self._time_decay_weight(rel.get("last_seen_ms", 0))
            score = rel.get("weight", 0.5) * decay * rel.get("mention_count", 1)
            scored.append((rid, score))
        scored.sort(key=lambda x: x[1])

        remove_count = len(scored) // 5
        for rid, _ in scored[:remove_count]:
            del self._relations[rid]

        self._rebuild_adjacency()
        logger.info(f"[Graph] Pruned {remove_count} relations (limit={_MAX_RELATIONS})")

    def _prune_entity_relations(self, eid: str):
        """Prune weakest relations for a specific entity."""
        neighbors = self._adj.get(eid, [])
        if len(neighbors) < _MAX_RELATIONS_PER_ENTITY:
            return
        scored = []
        for nid, rid, pred, weight in neighbors:
            rel = self._relations.get(rid, {})
            decay = self._time_decay_weight(rel.get("last_seen_ms", 0))
            score = weight * decay
            scored.append((rid, score))
        scored.sort(key=lambda x: x[1])

        remove_count = len(scored) // 3
        for rid, _ in scored[:remove_count]:
            self._relations.pop(rid, None)

        self._rebuild_adjacency()

    def _maybe_compact(self):
        """Trigger compaction if append count exceeds threshold."""
        if self._append_count >= _COMPACTION_THRESHOLD:
            self.compact()

    def compact(self):
        """Compact JSONL files by writing only current state.

        This deduplicates multiple updates to the same entity/relation.
        """
        try:
            MEM_DIR.mkdir(parents=True, exist_ok=True)

            # Compact entities
            if self._entities:
                tmp_ent = ENTITIES_FILE.with_suffix(".jsonl.tmp")
                with open(tmp_ent, "w", encoding="utf-8") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    try:
                        for ent in self._entities.values():
                            f.write(json.dumps(ent, ensure_ascii=False) + "\n")
                        f.flush()
                        os.fsync(f.fileno())
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
                os.replace(str(tmp_ent), str(ENTITIES_FILE))

            # Compact relations
            if self._relations:
                tmp_rel = RELATIONS_FILE.with_suffix(".jsonl.tmp")
                with open(tmp_rel, "w", encoding="utf-8") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    try:
                        for rel in self._relations.values():
                            f.write(json.dumps(rel, ensure_ascii=False) + "\n")
                        f.flush()
                        os.fsync(f.fileno())
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
                os.replace(str(tmp_rel), str(RELATIONS_FILE))

            self._append_count = 0
            self._last_compaction = time.time()
            ent_c = len(self._entities)
            rel_c = len(self._relations)
            logger.info(f"[Graph] Compacted: {ent_c} entities, {rel_c} relations")
        except Exception as e:
            logger.error(f"[Graph] Compaction error: {e}")

    def get_stats(self) -> dict:
        """Return graph statistics."""
        self.load()
        return {
            "entities": len(self._entities),
            "relations": len(self._relations),
            "entity_types": dict(
                sorted(
                    ((t, sum(1 for e in self._entities.values() if e.get("type") == t))
                     for t in set(e.get("type", "?") for e in self._entities.values())),
                    key=lambda x: -x[1]
                )[:10]
            ),
            "avg_degree": round(
                sum(len(v) for v in self._adj.values()) / max(len(self._adj), 1), 1
            ),
            "append_count": self._append_count,
        }
