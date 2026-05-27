#!/usr/bin/env python3
"""
propose_candidates.py — Phase 8.4: propose cross-skill candidate
clusters from Anthropic Skills procedural units.

For each procedural unit we build a *frame*:
  ⟨verb, object_keywords, code_lang, n_bullets, linked_scripts, title⟩

Then:
  - embed (title + first 300 chars + linked-script names)
  - greedy agglomerative clustering by cosine similarity
  - filter: keep clusters with ≥2 distinct parent skills (cross-skill)

Output: results_anthropic/candidates.json — list of candidate clusters,
each with member units, parent skill set, and frame summary.

Cost: ~$0.001 (single embedding pass over 317 units).
"""
from __future__ import annotations
import os
import argparse, json, math, os, re, sys, time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from common import load_json, save_json, Budget

ROOT = Path(__file__).parent
LIB = ROOT / "results_anthropic"  # default; overridable via --lib-dir


# ── Frame extraction (deterministic) ───────────────────────────────────
ACTION_VERBS = {
    "convert", "create", "build", "extract", "edit", "modify",
    "validate", "verify", "check", "parse", "read", "write",
    "merge", "split", "transform", "format", "generate", "render",
    "compile", "package", "unpack", "deploy", "test", "lint",
    "load", "save", "fetch", "upload", "download", "process",
    "analyze", "annotate", "review", "summarize", "translate",
    "filter", "sort", "search", "replace", "copy", "delete",
    "install", "configure", "setup", "init", "register",
}

OBJECT_NOUNS = {
    "docx", "pdf", "pptx", "xlsx", "html", "css", "js", "py",
    "image", "video", "gif", "table", "chart", "diagram", "form",
    "schema", "spec", "template", "theme", "style", "layout",
    "header", "footer", "section", "paragraph", "page",
    "text", "content", "data", "code", "script", "file",
    "document", "spreadsheet", "presentation", "report",
    "email", "message", "comment", "annotation",
    "metadata", "frontmatter", "yaml", "json",
}


def extract_frame(unit: dict, parent_skill: dict) -> dict:
    """Heuristic frame extraction from a procedural unit + its parent skill."""
    title = (unit["title"] or "").lower()
    body  = unit["text"][:600].lower()

    # verb: first action verb in title, fallback to body
    verb = None
    for word in re.findall(r"[a-z]+", title):
        if word in ACTION_VERBS: verb = word; break
    if not verb:
        for word in re.findall(r"[a-z]+", body):
            if word in ACTION_VERBS: verb = word; break

    # objects: noun candidates from title + body
    objs = set()
    for word in re.findall(r"[a-z]+", title + " " + body[:200]):
        if word in OBJECT_NOUNS: objs.add(word)

    # code language hints
    code_langs = sorted({c["lang"] for c in unit.get("code_blocks") or [] if c["lang"]})

    # linked scripts (from local_links to .py/.sh files)
    linked_scripts = []
    for link in unit.get("local_links") or []:
        tgt = link["target"].split("#")[0]
        if tgt.endswith(".py") or tgt.endswith(".sh") or tgt.endswith(".js"):
            linked_scripts.append(tgt)

    return {
        "verb": verb,
        "objects": sorted(objs)[:5],
        "code_langs": code_langs,
        "n_bullets": unit["n_bullets"],
        "n_code_blocks": len(unit.get("code_blocks") or []),
        "linked_scripts": linked_scripts,
        "title": unit["title"],
        "level": unit["level"],
    }


# ── Embedding text builder ──────────────────────────────────────────────
def unit_embed_text(unit: dict, parent: dict, frame: dict) -> str:
    """Compose a compact embedding query from the unit + frame."""
    parts = [
        f"skill: {parent['name']}",
        f"section: {unit['title']}",
    ]
    if frame["verb"]:
        parts.append(f"action: {frame['verb']}")
    if frame["objects"]:
        parts.append(f"objects: {', '.join(frame['objects'])}")
    body_snippet = re.sub(r"\s+", " ", unit["text"][:400]).strip()
    parts.append(f"content: {body_snippet}")
    return "  ".join(parts)


# ── Greedy agglomerative clustering ─────────────────────────────────────
def greedy_cluster(emb: np.ndarray, threshold: float) -> list[list[int]]:
    """
    Single-linkage greedy: for each point in order of decreasing local
    density, attach to the most-similar existing cluster if ≥ threshold,
    else start a new cluster. Operates on cosine similarity.
    """
    n = emb.shape[0]
    a = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)
    sims = a @ a.T
    np.fill_diagonal(sims, -1.0)

    # Order points by their max similarity to any other point (high-density first)
    max_sim = sims.max(axis=1)
    order = np.argsort(-max_sim)

    clusters = []  # list of {indices: [...], centroid: vec}
    for idx in order:
        v = a[idx]
        best_cluster = -1; best_sim = threshold - 1e-9
        for ci, c in enumerate(clusters):
            sim = float(np.dot(c["centroid"], v))
            if sim > best_sim:
                best_sim = sim; best_cluster = ci
        if best_cluster >= 0:
            c = clusters[best_cluster]
            c["indices"].append(int(idx))
            n_old = len(c["indices"]) - 1
            c["centroid"] = (c["centroid"] * n_old + v) / (n_old + 1)
        else:
            clusters.append({"indices": [int(idx)], "centroid": v.copy()})

    return [sorted(c["indices"]) for c in clusters]


# ── Embedding API ────────────────────────────────────────────────────────
def embed_batch(client, model, texts, budget):
    r = client.embeddings.create(model=model, input=texts)
    budget.add(model, r.usage.prompt_tokens, 0, phase="embed_text_units")
    return np.array([d.embedding for d in r.data], dtype=np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key",  default=None)
    ap.add_argument("--embed-model", default="text-embedding-3-small")
    ap.add_argument("--threshold",   type=float, default=0.65,
                    help="Cosine similarity threshold for clustering")
    ap.add_argument("--min-parents", type=int, default=2,
                    help="Min distinct parent skills per cluster")
    ap.add_argument("--budget-usd",  type=float, default=0.5)
    ap.add_argument("--lib-dir", default=None,
                    help="Library dir under exp1/, e.g. results_gos")
    args = ap.parse_args()
    global LIB
    if args.lib_dir:
        LIB = ROOT / args.lib_dir
    LIB.mkdir(parents=True, exist_ok=True)

    from openai import OpenAI
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
    kwargs = {"api_key": api_key}
    if args.base_url: kwargs["base_url"] = args.base_url
    client = OpenAI(**kwargs)
    budget = Budget(args.budget_usd)

    parents = load_json(LIB / "parents.json")

    # Flatten units across skills
    units = []  # global list with provenance
    for s in parents:
        for ui, u in enumerate(s["procedural_units"]):
            frame = extract_frame(u, s)
            units.append({
                "global_id": len(units),
                "skill_id":  s["skill_id"],
                "skill_name": s["name"],
                "unit_index": ui,
                "title":      u["title"],
                "level":      u["level"],
                "text_preview": u["text"][:300],
                "frame":      frame,
                "n_bullets":  u["n_bullets"],
                "code_langs": frame["code_langs"],
                "linked_scripts": frame["linked_scripts"],
            })
    print(f"flattened {len(units)} procedural units across {len(parents)} skills")

    # Embed
    embed_texts = [unit_embed_text(parents[
        next(i for i, p in enumerate(parents) if p["skill_id"] == u["skill_id"])
        ]["procedural_units"][u["unit_index"]],
                                       parents[
        next(i for i, p in enumerate(parents) if p["skill_id"] == u["skill_id"])
        ],
                                       u["frame"]) for u in units]
    print(f"embedding {len(embed_texts)} units...")
    BATCH = 64
    embs = []
    for i in range(0, len(embed_texts), BATCH):
        embs.append(embed_batch(client, args.embed_model, embed_texts[i:i+BATCH], budget))
    emb = np.vstack(embs)
    print(f"embed budget: ${budget.spent:.4f}")

    # Cluster
    clusters_idx = greedy_cluster(emb, threshold=args.threshold)
    print(f"raw clusters: {len(clusters_idx)}")

    # Filter: keep only clusters with ≥ min_parents distinct parent skills
    candidates = []
    for cluster_indices in clusters_idx:
        if len(cluster_indices) < 2: continue
        member_units = [units[i] for i in cluster_indices]
        distinct_skills = sorted({u["skill_id"] for u in member_units})
        if len(distinct_skills) < args.min_parents: continue

        # Aggregate frame info across cluster
        verbs = Counter(u["frame"]["verb"] for u in member_units if u["frame"]["verb"])
        all_objects = Counter(o for u in member_units for o in u["frame"]["objects"])
        canonical_verb = verbs.most_common(1)[0][0] if verbs else None
        canonical_objs = [o for o, _ in all_objects.most_common(5)]

        candidates.append({
            "candidate_id": f"text_cand_{len(candidates):03d}",
            "n_units":           len(member_units),
            "n_distinct_skills": len(distinct_skills),
            "distinct_skills":   distinct_skills,
            "canonical_verb":    canonical_verb,
            "canonical_objects": canonical_objs,
            "members": [{"skill_id": u["skill_id"], "skill_name": u["skill_name"],
                          "unit_index": u["unit_index"],
                          "title": u["title"], "level": u["level"],
                          "n_bullets": u["n_bullets"],
                          "linked_scripts": u["linked_scripts"],
                          "text_preview": u["text_preview"]}
                         for u in member_units],
        })

    candidates.sort(key=lambda c: (-c["n_distinct_skills"], -c["n_units"]))
    save_json(candidates, LIB / "candidates.json")

    # Report
    print()
    print(f"cross-skill candidate clusters (≥{args.min_parents} parent skills): {len(candidates)}")
    print()
    print(f"{'cand_id':<18} {'n_units':>8} {'n_skills':>9} {'verb':<10} {'objects':<25} {'sample title':<40}")
    print("-" * 110)
    for c in candidates[:30]:
        sample_title = c["members"][0]["title"][:40]
        objs = ",".join(c["canonical_objects"][:3])[:25]
        print(f"{c['candidate_id']:<18} {c['n_units']:>8} {c['n_distinct_skills']:>9} "
              f"{(c['canonical_verb'] or '?'):<10} {objs:<25} {sample_title}")


if __name__ == "__main__":
    main()
