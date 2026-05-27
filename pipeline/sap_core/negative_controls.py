"""
negative_controls.py — generate fake clusters as verifier negative controls.

Three classes of negative control:

  A) Cross-domain: pick random units from DIFFERENT libraries / domains
     and force them into one cluster. Expectation: verifier should
     reject ~100% (sanity check).

  B) Same-domain but semantically distinct: within one library, pick
     units from different (verb, object) frames. Expectation: ≤ 5%
     auto_promote (hard test for our score function).

  C) Near-miss: same verb but different object — e.g. validate-email /
     validate-phone / validate-address packaged together as a fake
     "validate-X" cluster. Expectation: verifier should reject because
     binding evidence will be inconsistent across parents (each parent
     only has its own object word).

Each generated control is a fake `candidate` record matching the
schema in `candidates.json`:

  {
    "candidate_id": "neg_<class>_<n>",
    "n_units": int,
    "n_distinct_skills": int,
    "distinct_skills": [skill_id, ...],
    "canonical_verb": str,
    "canonical_objects": [str, ...],
    "members": [
      {"skill_id": ..., "skill_name": ..., "unit_index": ..., "title": ...,
       "level": ..., "n_bullets": ..., "linked_scripts": [...],
       "text_preview": ...}
    ],
    "_negative_control_class": "A" | "B" | "C"
  }
"""
from __future__ import annotations
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from common import load_json


# ── helpers ────────────────────────────────────────────────────────────
def _unit_record(parent: dict, unit_idx: int) -> dict:
    """Build a candidate `members` entry from a parent + unit index."""
    units = parent.get("procedural_units") or []
    if unit_idx >= len(units):
        return None
    u = units[unit_idx]
    return {
        "skill_id":      parent["skill_id"],
        "skill_name":    parent.get("name", parent["skill_id"]),
        "unit_index":    unit_idx,
        "title":         u.get("title", ""),
        "level":         u.get("level", 2),
        "n_bullets":     u.get("n_bullets", 0),
        "linked_scripts": [],
        "text_preview":  (u.get("text") or "")[:400],
    }


def _verb_object_of(text: str) -> tuple[str, list[str]]:
    """Extract a rough (verb, objects) frame from unit text."""
    verbs_re = re.compile(
        r"\b(create|build|generate|extract|convert|edit|modify|read|write|"
        r"validate|verify|check|parse|merge|split|format|compile|render|"
        r"unpack|repack|install|configure|setup|fetch|upload|download|"
        r"summarize|annotate|translate|sort|search|replace|copy|delete|"
        r"select|apply|process|review|design|navigate|move|pick)\b",
        re.IGNORECASE,
    )
    m = verbs_re.search(text or "")
    verb = m.group(1).lower() if m else "do"
    # Objects: nouns immediately following the verb
    objs: list[str] = []
    if m:
        tail = text[m.end():m.end() + 100]
        for w in re.findall(r"\b([a-zA-Z]{3,15})\b", tail):
            objs.append(w.lower())
            if len(objs) >= 3: break
    return verb, objs


def _domain_of_skill_id(skill_id: str) -> str:
    """Crude domain extraction from skill_id prefix."""
    if skill_id.startswith("gos_"):
        # GoS skill_id often has domain embedded
        if "scienceworld" in skill_id: return "gos_scienceworld"
        if "alfworld" in skill_id:     return "gos_alfworld"
        if "webshop" in skill_id:      return "gos_webshop"
        if "automation" in skill_id:   return "gos_automation"
        if "anthropic" in skill_id:    return "gos_anthropic_skills"
        if any(s in skill_id for s in ("flutter", "code", "qa", "test", "review",
                                           "deploy", "git", "claude", "command")):
            return "gos_software"
        return "gos_other"
    if skill_id.startswith("anthropic_"):
        return "anthropic"
    if skill_id.startswith("stripe_"):
        return "stripe"
    if skill_id.startswith("github_"):
        return "github"
    return "unknown"


def _build_fake_candidate(class_label: str, idx: int,
                            members: list[dict]) -> dict:
    """Wrap a member list into a candidate dict."""
    distinct_skills = sorted({m["skill_id"] for m in members})
    # canonical_verb / objects: pick the most common across members
    verb_counts = defaultdict(int)
    object_counts = defaultdict(int)
    for m in members:
        v, objs = _verb_object_of(m.get("text_preview", "") + " " + m.get("title", ""))
        verb_counts[v] += 1
        for o in objs:
            object_counts[o] += 1
    verb = max(verb_counts.items(), key=lambda x: x[1])[0] if verb_counts else "do"
    objs = [o for o, _ in sorted(object_counts.items(),
                                       key=lambda x: -x[1])[:3]]
    return {
        "candidate_id":         f"neg_{class_label}_{idx:03d}",
        "n_units":              len(members),
        "n_distinct_skills":    len(distinct_skills),
        "distinct_skills":      distinct_skills,
        "canonical_verb":       verb,
        "canonical_objects":    objs,
        "members":              members,
        "_negative_control_class": class_label,
    }


# ── A. Cross-domain controls ──────────────────────────────────────────
def generate_cross_domain(parents: list[dict], n_clusters: int = 10,
                            members_per_cluster: int = 3,
                            random_state: int = 42) -> list[dict]:
    """For each fake cluster, pick `members_per_cluster` units from
    DIFFERENT domains. Expectation: verifier rejects ~100%."""
    rng = random.Random(random_state)
    # Group parents by domain
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for p in parents:
        by_domain[_domain_of_skill_id(p["skill_id"])].append(p)
    if len([d for d, ps in by_domain.items() if len(ps) > 0]) < 2:
        return []  # not enough domains
    domains = list(by_domain.keys())

    out = []
    attempts = 0
    while len(out) < n_clusters and attempts < n_clusters * 20:
        attempts += 1
        # Pick members_per_cluster distinct domains
        if len(domains) < members_per_cluster:
            picked_domains = rng.choices(domains, k=members_per_cluster)
        else:
            picked_domains = rng.sample(domains, members_per_cluster)
        members = []
        seen_skills = set()
        for dom in picked_domains:
            ps = [p for p in by_domain[dom] if (p.get("procedural_units") or [])
                   and p["skill_id"] not in seen_skills]
            if not ps: continue
            p = rng.choice(ps)
            u_idx = rng.randrange(len(p["procedural_units"]))
            m = _unit_record(p, u_idx)
            if m:
                members.append(m)
                seen_skills.add(p["skill_id"])
        if len(members) >= 2:
            out.append(_build_fake_candidate("A_cross_domain", len(out), members))
    return out


# ── B. Same-domain semantically-distinct controls ────────────────────
def generate_same_domain_distinct(parents: list[dict], n_clusters: int = 10,
                                     members_per_cluster: int = 3,
                                     random_state: int = 43) -> list[dict]:
    """Within one domain, pick units with DIFFERENT (verb, object) frames.
    Expectation: ≤ 5% auto_promote."""
    rng = random.Random(random_state)
    by_domain: dict[str, list[tuple[dict, int, str, list[str]]]] = defaultdict(list)
    for p in parents:
        units = p.get("procedural_units") or []
        for i, u in enumerate(units):
            verb, objs = _verb_object_of((u.get("text", "") or "") + " " +
                                            (u.get("title", "") or ""))
            by_domain[_domain_of_skill_id(p["skill_id"])].append(
                (p, i, verb, objs))

    out = []
    for dom, units in by_domain.items():
        if len(units) < members_per_cluster * 3: continue
        # group by verb
        by_verb: dict[str, list] = defaultdict(list)
        for u in units:
            by_verb[u[2]].append(u)
        distinct_verbs = [v for v, lst in by_verb.items() if len(lst) > 0]
        if len(distinct_verbs) < members_per_cluster: continue

        attempts = 0
        target = max(1, n_clusters // max(len(by_domain), 1) + 1)
        while target > 0 and attempts < 50:
            attempts += 1
            picked_verbs = rng.sample(distinct_verbs,
                                          min(members_per_cluster, len(distinct_verbs)))
            members = []
            seen_skills = set()
            for v in picked_verbs:
                vs = [u for u in by_verb[v] if u[0]["skill_id"] not in seen_skills]
                if not vs: continue
                p, i, verb, objs = rng.choice(vs)
                m = _unit_record(p, i)
                if m:
                    members.append(m)
                    seen_skills.add(p["skill_id"])
            if len(members) >= 2:
                out.append(_build_fake_candidate("B_same_domain_distinct",
                                                       len(out), members))
                target -= 1
            if len(out) >= n_clusters: break
        if len(out) >= n_clusters: break
    return out[:n_clusters]


# ── C. Near-miss controls ────────────────────────────────────────────
def generate_near_miss(parents: list[dict], n_clusters: int = 10,
                          members_per_cluster: int = 3,
                          random_state: int = 44) -> list[dict]:
    """Same verb but DIFFERENT objects (e.g. validate email / phone / address).

    Strategy: group units by their canonical_verb, then within each
    verb group, sub-group by (first object word). Sample one unit from
    each of `members_per_cluster` different object sub-groups within
    the same verb group.

    Expectation: verifier should reject because contract inputs (the
    'X' in validate-X) cannot bind consistently to every parent's
    object-specific evidence.
    """
    rng = random.Random(random_state)
    # (verb, first_object) → list of (parent, unit_idx)
    verb_object_units: dict[tuple[str, str], list[tuple[dict, int]]] = defaultdict(list)
    for p in parents:
        for i, u in enumerate(p.get("procedural_units") or []):
            verb, objs = _verb_object_of((u.get("text", "") or "") + " " +
                                            (u.get("title", "") or ""))
            if not objs: continue
            verb_object_units[(verb, objs[0])].append((p, i))

    # Group by verb
    verb_to_objects: dict[str, list[str]] = defaultdict(list)
    for (v, o), lst in verb_object_units.items():
        if lst:
            verb_to_objects[v].append(o)

    eligible_verbs = [v for v, objs in verb_to_objects.items()
                       if len(set(objs)) >= members_per_cluster]
    if not eligible_verbs:
        return []
    out = []
    attempts = 0
    while len(out) < n_clusters and attempts < n_clusters * 20:
        attempts += 1
        v = rng.choice(eligible_verbs)
        objs = list(set(verb_to_objects[v]))
        picked_objs = rng.sample(objs, members_per_cluster)
        members = []
        seen_skills = set()
        for o in picked_objs:
            lst = [(p, i) for (p, i) in verb_object_units[(v, o)]
                    if p["skill_id"] not in seen_skills]
            if not lst: continue
            p, i = rng.choice(lst)
            m = _unit_record(p, i)
            if m:
                members.append(m)
                seen_skills.add(p["skill_id"])
        if len(members) >= 2:
            out.append(_build_fake_candidate("C_near_miss", len(out), members))
    return out


# ── D. Swapped-contract controls (bypasses LLM filter) ──────────────
def generate_swapped_contracts(real_candidates: list[dict],
                                 real_drafts: list[dict],
                                 n_clusters: int = 10,
                                 random_state: int = 45) -> list[tuple[dict, dict]]:
    """Pair a REAL contract draft with a DIFFERENT cluster's members.

    Returns a list of (fake_candidate, swapped_contract_draft) pairs.
    These are designed to test the verifier (not the LLM extractor):
    the contract is well-formed (LLM wrote it for a real case), but
    when applied to the wrong members, binding/coverage/replacement
    should fail.

    The driver script that uses this saves the contract as the
    candidate's contracts_draft.json entry, so the LLM extraction step
    is bypassed entirely.
    """
    rng = random.Random(random_state)
    # Filter drafts to those with a non-degenerate contract
    valid_drafts = [d for d in real_drafts
                       if isinstance(d.get("contract"), dict)
                       and not any(k.startswith("_") for k in d["contract"].keys())]
    if not valid_drafts or len(real_candidates) < 2:
        return []

    cand_by_id = {c["candidate_id"]: c for c in real_candidates}

    out = []
    attempts = 0
    while len(out) < n_clusters and attempts < n_clusters * 20:
        attempts += 1
        # Pick a draft (the contract to use)
        d = rng.choice(valid_drafts)
        # Pick a DIFFERENT candidate's members
        other_cands = [c for c in real_candidates
                          if c["candidate_id"] != d["candidate_id"]
                          and c["n_distinct_skills"] >= 2]
        if not other_cands: continue
        other = rng.choice(other_cands)

        members = other["members"]
        fake_id = f"neg_D_swap_{len(out):03d}"
        fake_cand = {
            "candidate_id":      fake_id,
            "n_units":           other["n_units"],
            "n_distinct_skills": other["n_distinct_skills"],
            "distinct_skills":   other["distinct_skills"],
            "canonical_verb":    other.get("canonical_verb", "do"),
            "canonical_objects": other.get("canonical_objects", []),
            "members":           members,
            "_negative_control_class": "D_swapped_contract",
            "_swap_source_contract_from": d["candidate_id"],
            "_swap_member_cluster":       other["candidate_id"],
        }
        fake_draft = {
            "candidate_id":      fake_id,
            "n_units":           other["n_units"],
            "n_distinct_skills": other["n_distinct_skills"],
            "distinct_skills":   other["distinct_skills"],
            "contract":          d["contract"],  # contract from elsewhere
            "extraction_time_s": 0.0,
            "_swapped_from":     d["candidate_id"],
        }
        out.append((fake_cand, fake_draft))
    return out


# ── Top-level driver ─────────────────────────────────────────────────
def generate_all_negative_controls(parents_path: Path,
                                     n_per_class: int = 10,
                                     members_per_cluster: int = 3,
                                     random_state: int = 42) -> list[dict]:
    parents = load_json(parents_path)
    A = generate_cross_domain(parents, n_clusters=n_per_class,
                                  members_per_cluster=members_per_cluster,
                                  random_state=random_state)
    B = generate_same_domain_distinct(parents, n_clusters=n_per_class,
                                          members_per_cluster=members_per_cluster,
                                          random_state=random_state + 1)
    C = generate_near_miss(parents, n_clusters=n_per_class,
                              members_per_cluster=members_per_cluster,
                              random_state=random_state + 2)
    return A + B + C
