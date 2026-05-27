"""
split.py — Recursive split of failed candidates into smaller sub-clusters.

When a candidate cluster fails the verifier, instead of discarding it
entirely, we try to find a sub-cluster that DOES pass. Three split
strategies are implemented:

  1. Failure-signature split (3.1):
       group members by the failure reason at the per-parent level:
         - which parents bound vs didn't
         - which parents had low coverage
       members with the same "failure column pattern" are likely the
       same true sub-procedure; split into those groups.

  2. Contract-compatibility graph (3.2):
       build a graph where nodes = members, edges = "compatible"
       (same canonical_verb AND ≥ 1 shared canonical_object).
       Apply connected components; each component is a sub-cluster.

  3. Stricter re-clustering (3.3):
       within the cluster, re-cluster member embeddings at a tighter
       cosine threshold (e.g. 0.80 vs the original 0.65). Returns the
       largest connected component.

Stop conditions (from doc §5.2):
  - sub-cluster covers < 2 parents → stop
  - max recursion depth = 2 → stop
  - split produced no improvement (evidence score didn't go up) → stop
  - risk too high → reject immediately

Each split function takes a Candidate (the v2 schema) and returns a
list of sub-Candidates. Empty list means "could not split further".
"""
from __future__ import annotations
import re
from collections import defaultdict
from typing import Optional


# ── helper: minimum cluster size policy ────────────────────────────────
MIN_PARENTS_PER_SUBCLUSTER = 2     # below this, sub-cluster discarded


def _make_sub_candidate(parent_cand: dict, sub_members: list[dict],
                          tag: str, depth: int) -> Optional[dict]:
    """Build a sub-candidate from a subset of the parent cluster's members.

    Returns None if the resulting cluster has < MIN_PARENTS_PER_SUBCLUSTER
    distinct skills.
    """
    distinct_skills = sorted({m["skill_id"] for m in sub_members})
    if len(distinct_skills) < MIN_PARENTS_PER_SUBCLUSTER:
        return None
    # Recompute canonical_verb / canonical_objects from sub-members
    # (simple: use most common verb/object across members)
    return {
        "candidate_id":         f"{parent_cand['candidate_id']}__{tag}_d{depth}",
        "n_units":              len(sub_members),
        "n_distinct_skills":    len(distinct_skills),
        "distinct_skills":      distinct_skills,
        "canonical_verb":       parent_cand.get("canonical_verb", "do"),
        "canonical_objects":    parent_cand.get("canonical_objects", []),
        "members":              sub_members,
        "_split_from":          parent_cand["candidate_id"],
        "_split_tag":           tag,
        "_split_depth":         depth,
    }


# ── 3.1 Failure-signature split ────────────────────────────────────────
def split_by_failure_signature(candidate: dict, evidence_profile: dict,
                                  depth: int = 1) -> list[dict]:
    """Group cluster members by per-parent failure pattern.

    The signature for each parent is a tuple of:
      (parent_in_binding_failed, parent_in_low_coverage)

    Parents with the same signature are grouped together. Each
    sub-cluster contains the members from one group, plus the units
    from any parent that succeeded (which we can pair with either
    group for re-verification).
    """
    sig = evidence_profile.get("failure_signature") or {}
    unbound_parents     = set(sig.get("unbound_parents") or [])
    low_recall_parents  = set(sig.get("low_recall_parents") or [])

    # Compute per-parent signature
    members = candidate["members"]
    parent_signatures: dict[str, tuple[bool, bool]] = {}
    for m in members:
        sid = m["skill_id"]
        parent_signatures[sid] = (
            sid in unbound_parents,
            sid in low_recall_parents,
        )

    # Group parents by signature
    sig_to_parents: dict[tuple, list[str]] = defaultdict(list)
    for sid, s in parent_signatures.items():
        sig_to_parents[s].append(sid)

    # Each group becomes a sub-cluster
    sub_candidates = []
    for s, sids in sig_to_parents.items():
        sub_members = [m for m in members if m["skill_id"] in set(sids)]
        # Tag describes the signature
        tag = "sig"
        if s[0]: tag += "_unbound"
        if s[1]: tag += "_lowcov"
        if not (s[0] or s[1]): tag += "_ok"
        sub = _make_sub_candidate(candidate, sub_members,
                                       tag=tag, depth=depth)
        if sub: sub_candidates.append(sub)

    # If we only got one sub-cluster identical to the parent → no progress
    if len(sub_candidates) <= 1 and \
        sub_candidates and sub_candidates[0]["n_distinct_skills"] == \
        candidate.get("n_distinct_skills", 0):
        return []
    return sub_candidates


# ── 3.2 Contract-compatibility graph ──────────────────────────────────
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+")
VERB_RE = re.compile(
    r"\b(create|build|generate|extract|convert|edit|modify|read|write|"
    r"validate|verify|check|parse|merge|split|format|compile|render|"
    r"unpack|repack|install|configure|setup|fetch|upload|download|"
    r"summarize|annotate|translate|sort|search|replace|copy|delete|"
    r"select|apply|process|review|design|navigate|move|pick|focus|"
    r"prepare|execute|find|locate|update)\b", re.IGNORECASE)


def _frame_of_unit(unit: dict) -> tuple[str, set[str]]:
    """Extract (verb, object_set) from a unit's title + body."""
    text = (unit.get("title", "") + " " +
            unit.get("text_preview", ""))
    m = VERB_RE.search(text)
    verb = m.group(1).lower() if m else "do"
    objs = set()
    if m:
        tail = text[m.end():m.end() + 200]
        for w in TOKEN_RE.findall(tail.lower())[:8]:
            if len(w) >= 3 and w not in {"the", "and", "for", "with",
                                            "from", "into", "this", "that"}:
                objs.add(w)
    return verb, objs


def _connected_components(nodes: list[int], edges: list[tuple[int, int]]) -> list[list[int]]:
    """Standard union-find connected components."""
    parent = {n: n for n in nodes}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[ra] = rb
    for a, b in edges:
        union(a, b)
    groups: dict[int, list[int]] = defaultdict(list)
    for n in nodes:
        groups[find(n)].append(n)
    return list(groups.values())


def split_by_compatibility_graph(candidate: dict, depth: int = 1) -> list[dict]:
    """Split using a compatibility graph.

    Nodes: candidate members (indexed)
    Edges: (i, j) iff:
      - same canonical_verb
      - AND ≥ 1 shared object word

    Connected components → sub-clusters.
    """
    members = candidate["members"]
    if len(members) < 3:
        return []  # too small to split

    frames = [_frame_of_unit(m) for m in members]
    nodes = list(range(len(members)))
    edges = []
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            vi, oi = frames[i]
            vj, oj = frames[j]
            if vi == vj and (oi & oj):
                edges.append((i, j))

    components = _connected_components(nodes, edges)
    sub_candidates = []
    for ci, comp in enumerate(components):
        sub_members = [members[i] for i in comp]
        sub = _make_sub_candidate(candidate, sub_members,
                                       tag=f"compat{ci}", depth=depth)
        if sub: sub_candidates.append(sub)

    if len(sub_candidates) <= 1:
        return []
    return sub_candidates


# ── 3.3 Stricter re-clustering ────────────────────────────────────────
def split_by_stricter_recluster(candidate: dict, depth: int = 1,
                                   embedding_lookup: Optional[dict] = None,
                                   stricter_threshold: float = 0.80) -> list[dict]:
    """Re-cluster members at a tighter cosine threshold.

    Requires embeddings. If `embedding_lookup` is None, falls back to
    text-token Jaccard similarity as a proxy.
    """
    import numpy as np
    members = candidate["members"]
    if len(members) < 3: return []
    n = len(members)

    if embedding_lookup is not None:
        emb_keys = [(m["skill_id"], m["unit_index"]) for m in members]
        # Build similarity matrix
        embs = []
        for k in emb_keys:
            if k in embedding_lookup:
                embs.append(embedding_lookup[k])
            else:
                embs.append(None)
        if any(e is None for e in embs):
            # Some embeddings missing — fall back to Jaccard
            embedding_lookup = None
        else:
            E = np.array(embs)
            Enorm = E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)
            sims = Enorm @ Enorm.T
            edges = []
            for i in range(n):
                for j in range(i + 1, n):
                    if sims[i][j] >= stricter_threshold:
                        edges.append((i, j))
            comps = _connected_components(list(range(n)), edges)
            subs = []
            for ci, comp in enumerate(comps):
                sub_members = [members[i] for i in comp]
                sub = _make_sub_candidate(candidate, sub_members,
                                               tag=f"strict{ci}",
                                               depth=depth)
                if sub: subs.append(sub)
            if len(subs) <= 1: return []
            return subs

    # Jaccard fallback
    def toks(m):
        text = m.get("title", "") + " " + m.get("text_preview", "")
        return set(TOKEN_RE.findall(text.lower()))
    token_sets = [toks(m) for m in members]
    edges = []
    for i in range(n):
        for j in range(i + 1, n):
            a, b = token_sets[i], token_sets[j]
            if not a or not b: continue
            jac = len(a & b) / max(len(a | b), 1)
            if jac >= 0.30:   # Jaccard equivalent of cosine 0.80 is rough
                edges.append((i, j))
    comps = _connected_components(list(range(n)), edges)
    subs = []
    for ci, comp in enumerate(comps):
        sub_members = [members[i] for i in comp]
        sub = _make_sub_candidate(candidate, sub_members,
                                       tag=f"strictjac{ci}",
                                       depth=depth)
        if sub: subs.append(sub)
    if len(subs) <= 1: return []
    return subs


# ── 3.4 Recursive split orchestrator ──────────────────────────────────
class SplitConfig:
    def __init__(self,
                 max_depth: int = 2,
                 strategies: list[str] = None,
                 stricter_threshold: float = 0.80,
                 min_score_improvement: float = 0.05):
        self.max_depth = max_depth
        # Use `is None` so an explicit empty list (no_split baseline)
        # is preserved instead of being replaced with the default.
        if strategies is None:
            self.strategies = ["failure_signature",
                                 "compatibility_graph",
                                 "stricter_recluster"]
        else:
            self.strategies = list(strategies)
        self.stricter_threshold = stricter_threshold
        self.min_score_improvement = min_score_improvement


def split_one_step(candidate: dict, evidence_profile: dict,
                      config: SplitConfig, depth: int = 1,
                      embedding_lookup: Optional[dict] = None) -> list[dict]:
    """Run one pass of all configured split strategies and return the
    *union* of sub-candidates produced.

    Sub-candidates are unique-tagged by strategy so the orchestrator can
    later compare them.
    """
    out = []
    if "failure_signature" in config.strategies:
        out.extend(split_by_failure_signature(candidate, evidence_profile,
                                                   depth=depth))
    if "compatibility_graph" in config.strategies:
        out.extend(split_by_compatibility_graph(candidate, depth=depth))
    if "stricter_recluster" in config.strategies:
        out.extend(split_by_stricter_recluster(
            candidate, depth=depth,
            embedding_lookup=embedding_lookup,
            stricter_threshold=config.stricter_threshold,
        ))
    return out
