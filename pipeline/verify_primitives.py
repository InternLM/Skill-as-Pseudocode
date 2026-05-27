#!/usr/bin/env python3
"""
verify_primitives.py — Phase 8.6: verifier loop for typed callable contracts.

For each draft contract, run four deterministic checks:

  1. Coverage   — sentence-embedding overlap between contract surface
                  text and the source-unit text. Reports recall and
                  precision over keyword tokens, plus embedding cosine
                  on a unit-by-unit basis.

  2. Binding    — for each required input, search every parent skill
                  for evidence that the input can be supplied. Evidence
                  sources: frontmatter description, source-unit text,
                  script argparse/click signatures.

  3. Replacement (proxy for text) — for each parent unit, check that
                  the unit's heuristic verb or object set is consistent
                  with the contract's trigger. This is a weak proxy
                  because text-skill replay is generally not available.

  4. Risk       — static scan over linked scripts: Python AST for
                  network/filesystem/subprocess/secrets; shell regex
                  for curl/wget/eval; JS regex for fetch/exec/spawn.

Output: results_anthropic/verifier_reports.json

The acceptance rule (configurable thresholds):
  - coverage_recall >= τ_rec       (default 0.50)
  - all_required_inputs_bound == True
  - replacement_pass_rate >= ρ      (default 0.50)
  - risk_score < τ_risk             (default 0.7)
"""
from __future__ import annotations
import os
import argparse, ast, json, math, os, re, sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from common import load_json, save_json, Budget

ROOT = Path(__file__).parent
LIB = ROOT / "results_anthropic"

STOP = {"the","a","an","and","or","of","to","in","for","is","it","this","that",
         "with","on","as","at","by","be","are","was","were","its","from","into",
         "you","your","we","our","using","use","via","make","new","create","skill",
         "this","these","those","when","then","not","but","also"}

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text or "")
             if t.lower() not in STOP and len(t) >= 3]


# ── Coverage check ─────────────────────────────────────────────────────
def coverage_check(contract: dict, member_units: list[dict],
                   parents_by_id: dict, qry_emb: np.ndarray | None,
                   unit_emb: np.ndarray | None) -> dict:
    """
    Coverage approximation for text contracts:
      recall   = |contract tokens ∩ ∪ unit tokens| / |contract tokens|
      precision= |contract tokens ∩ ∪ unit tokens| / |unit tokens|
      embed_cos= cosine(contract_text, mean(unit_text)) if embeddings provided

    Plus per-unit cosine sim between contract and each member unit.
    """
    contract_text_parts = [
        contract.get("trigger", ""),
        " ".join(contract.get("preconditions") or []),
        " ".join(contract.get("postconditions") or []),
        contract.get("rationale", ""),
        " ".join(contract.get("input_schema", {}).keys()),
        " ".join((v or {}).get("description", "")
                 for v in contract.get("input_schema", {}).values()),
        " ".join(contract.get("output_schema", {}).keys()),
    ]
    contract_text = " ".join(contract_text_parts)
    contract_tokens = set(tokenize(contract_text))

    union_unit_tokens = set()
    per_unit = []
    for i, u in enumerate(member_units):
        parent = parents_by_id.get(u["skill_id"], {})
        unit_full = parent.get("procedural_units", [])[u["unit_index"]] \
                    if parent.get("procedural_units") else None
        body = unit_full["text"] if unit_full else u.get("text_preview", "")
        unit_tokens = set(tokenize(body + " " + u["title"]))
        union_unit_tokens |= unit_tokens
        per_unit.append({
            "skill_id": u["skill_id"], "title": u["title"],
            "n_unit_tokens": len(unit_tokens),
            "n_overlap_tokens": len(unit_tokens & contract_tokens),
        })

    inter = len(contract_tokens & union_unit_tokens)
    recall    = inter / max(len(contract_tokens), 1)
    precision = inter / max(len(union_unit_tokens), 1)

    embed_cos = None
    if qry_emb is not None and unit_emb is not None:
        # cosine of contract_emb vs mean unit_emb
        a = qry_emb / (np.linalg.norm(qry_emb) + 1e-9)
        u_mean = unit_emb.mean(axis=0)
        u_mean = u_mean / (np.linalg.norm(u_mean) + 1e-9)
        embed_cos = float(np.dot(a, u_mean))

    return {
        "contract_n_tokens": len(contract_tokens),
        "union_unit_n_tokens": len(union_unit_tokens),
        "n_overlap_tokens": inter,
        "coverage_recall":    round(recall, 3),
        "coverage_precision": round(precision, 3),
        "embed_cosine":       round(embed_cos, 3) if embed_cos is not None else None,
        "per_unit": per_unit,
    }


# ── Binding check ─────────────────────────────────────────────────────
def binding_check(contract: dict, member_units: list[dict],
                  parents_by_id: dict) -> dict:
    """
    For each required input, search per-parent for evidence:
      - parent frontmatter `name` and `description`
      - source unit titles and bodies
      - linked-script argparse/click args (matching by name fuzzy)
    """
    inputs = contract.get("input_schema") or {}
    required_inputs = [(name, spec) for name, spec in inputs.items()
                        if spec.get("required") in (True, "true", 1)]

    distinct_skills = sorted({u["skill_id"] for u in member_units})

    findings = []
    for name, spec in required_inputs:
        # Compound input names like "archive_file" must be split into
        # constituent words so they can match haystack tokens. The greedy
        # TOKEN_RE matches a single underscore-joined token, which would
        # never overlap with running text.
        name_tokens = set(tokenize(name)) | {name.lower()}
        for part in re.split(r"[_\W]+", name.lower()):
            if part and len(part) >= 3 and part not in STOP:
                name_tokens.add(part)
        per_parent = []
        for sid in distinct_skills:
            parent = parents_by_id.get(sid, {})
            haystack = " ".join([
                parent.get("name", ""), parent.get("description", ""),
                " ".join((u["title"] + " " + u["text_preview"])
                          for u in member_units if u["skill_id"] == sid),
            ])
            ht = set(tokenize(haystack))
            text_evidence = bool(name_tokens & ht)

            # Script signature evidence (also split arg names on _ / - / dashes)
            script_evidence = False
            for sc in parent.get("scripts") or []:
                sig = sc.get("signature") or {}
                for arg in sig.get("args", []) or []:
                    arg_name = arg["name"].lstrip("-").lower()
                    arg_tokens = set(tokenize(arg_name))
                    for part in re.split(r"[_\-\W]+", arg_name):
                        if part and len(part) >= 3 and part not in STOP:
                            arg_tokens.add(part)
                    if arg_tokens & name_tokens:
                        script_evidence = True; break
                if script_evidence: break

            per_parent.append({
                "skill_id": sid,
                "text_evidence":   text_evidence,
                "script_evidence": script_evidence,
                "bound": text_evidence or script_evidence,
            })
        n_bound = sum(1 for p in per_parent if p["bound"])
        findings.append({
            "input_name": name,
            "type":       spec.get("type"),
            "n_parents":  len(per_parent),
            "n_bound":    n_bound,
            "all_bound":  n_bound == len(per_parent),
            "per_parent": per_parent,
        })

    all_required_bound = all(f["all_bound"] for f in findings) if findings else True
    return {
        "n_required_inputs": len(required_inputs),
        "all_required_bound": all_required_bound,
        "per_input": findings,
    }


# ── Replacement-proxy check (text) ─────────────────────────────────────
ACTION_VERBS_RE = re.compile(
    r"\b(create|build|generate|extract|convert|edit|modify|read|write|"
    r"validate|verify|check|parse|merge|split|format|compile|render|"
    r"unpack|repack|install|configure|setup|fetch|upload|download|"
    r"summarize|annotate|translate|sort|search|replace|copy|delete|"
    r"select|apply|process|review|design)\b", re.IGNORECASE)


def replacement_proxy(contract: dict, member_units: list[dict],
                      parents_by_id: dict) -> dict:
    """
    Weak proxy for replacement-validity in text libraries.
    A unit is 'replaceable' if its verbal/objectual content overlaps
    with the contract's trigger sentence.
    """
    trigger = contract.get("trigger", "").lower()
    trigger_verbs = set(v.group(1).lower() for v in ACTION_VERBS_RE.finditer(trigger))
    trigger_tokens = set(tokenize(trigger))

    per_unit = []
    n_pass = 0
    for u in member_units:
        parent = parents_by_id.get(u["skill_id"], {})
        unit_full = parent.get("procedural_units", [])[u["unit_index"]] \
                    if parent.get("procedural_units") else None
        body = unit_full["text"] if unit_full else u.get("text_preview", "")
        unit_verbs = set(v.group(1).lower() for v in ACTION_VERBS_RE.finditer(body[:600]))
        unit_tokens = set(tokenize(u["title"] + " " + body[:400]))
        verb_overlap = bool(unit_verbs & trigger_verbs) if trigger_verbs else False
        token_overlap = len(trigger_tokens & unit_tokens) >= 2
        passes = verb_overlap or token_overlap
        if passes: n_pass += 1
        per_unit.append({
            "skill_id": u["skill_id"], "title": u["title"],
            "verb_overlap": verb_overlap,
            "token_overlap": token_overlap,
            "passes": passes,
        })
    rate = n_pass / max(len(member_units), 1)
    return {
        "n_units":             len(member_units),
        "n_pass":              n_pass,
        "replacement_pass_rate": round(rate, 3),
        "per_unit":            per_unit,
    }


# ── Risk check ────────────────────────────────────────────────────────
RISK_PATTERNS_PY = [
    ("network",   re.compile(r"\b(requests|urllib|httpx|socket|urlopen|fetch)\b")),
    ("filesystem",re.compile(r"\b(open|os\.remove|shutil|Path\(.+\)\.unlink|rmtree)\b")),
    ("subprocess",re.compile(r"\b(subprocess|os\.system|os\.popen|Popen)\b")),
    ("eval_exec", re.compile(r"\b(eval|exec)\(")),
    ("secrets",   re.compile(r"\b(API_KEY|SECRET|TOKEN|PASSWORD)\b")),
]
RISK_PATTERNS_SH = [
    ("network",   re.compile(r"\b(curl|wget|nc|netcat)\b")),
    ("filesystem",re.compile(r"\brm\b\s+-r")),
    ("subprocess",re.compile(r"\$\(|`")),
    ("eval_exec", re.compile(r"\beval\b")),
]
RISK_PATTERNS_JS = [
    ("network",   re.compile(r"\b(fetch|axios|http\.|XMLHttpRequest)\b")),
    ("filesystem",re.compile(r"\b(fs\.unlink|rm\(|rmdir)\b")),
    ("subprocess",re.compile(r"\b(child_process|execSync|spawn)\b")),
    ("eval_exec", re.compile(r"\beval\(")),
]


def risk_check(contract: dict, parents_by_id: dict) -> dict:
    """Static scan of linked resources for unsafe operations."""
    resources = contract.get("resources") or []
    flags = defaultdict(int)
    n_scanned = 0
    for res in resources:
        # Look for the script in any of the source parents
        # (resources can be relative paths)
        for sid, p in parents_by_id.items():
            for sc in p.get("scripts") or []:
                if sc["path"].endswith(res) or sc["path"] == res:
                    src_path = Path(f"/tmp/anthropic-skills/skills/"
                                      f"{sid.replace('anthropic_', '')}") / sc["path"]
                    if not src_path.exists(): continue
                    src = src_path.read_text(errors="ignore")
                    n_scanned += 1
                    patterns = (RISK_PATTERNS_PY if sc["type"] == "py" else
                                RISK_PATTERNS_SH if sc["type"] in ("sh","bash") else
                                RISK_PATTERNS_JS)
                    for label, pat in patterns:
                        if pat.search(src):
                            flags[label] += 1
                    break
    n_flags = sum(flags.values())
    risk_score = min(1.0, 0.2 * n_flags) if n_scanned > 0 else 0.0
    return {
        "n_resources_listed":  len(resources),
        "n_scripts_scanned":   n_scanned,
        "flags":               dict(flags),
        "risk_score":          round(risk_score, 3),
    }


# ── Embed contract for cosine ─────────────────────────────────────────
def embed_text(client, model, texts, budget):
    r = client.embeddings.create(model=model, input=texts)
    budget.add(model, r.usage.prompt_tokens, 0, phase="embed_verifier")
    return np.array([d.embedding for d in r.data], dtype=np.float32)


# ── Acceptance rule ───────────────────────────────────────────────────
def decide(coverage: dict, binding: dict, replacement: dict, risk: dict,
            tau_rec: float, rho: float, tau_risk: float) -> tuple[bool, list[str]]:
    reasons = []
    if coverage["coverage_recall"] < tau_rec:
        reasons.append(f"coverage_recall {coverage['coverage_recall']} < {tau_rec}")
    if not binding["all_required_bound"]:
        reasons.append("not all required inputs bound on every parent")
    if replacement["replacement_pass_rate"] < rho:
        reasons.append(f"replacement_pass_rate "
                        f"{replacement['replacement_pass_rate']} < {rho}")
    if risk["risk_score"] >= tau_risk:
        reasons.append(f"risk_score {risk['risk_score']} >= {tau_risk}")
    return (len(reasons) == 0), reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key",  default=None)
    ap.add_argument("--embed-model", default="text-embedding-3-small")
    ap.add_argument("--budget-usd",  type=float, default=0.5)
    ap.add_argument("--tau-recall",   type=float, default=0.50)
    ap.add_argument("--rho",          type=float, default=0.50)
    ap.add_argument("--tau-risk",     type=float, default=0.70)
    ap.add_argument("--lib-dir", default=None)
    args = ap.parse_args()
    global LIB
    if args.lib_dir:
        LIB = ROOT / args.lib_dir

    from openai import OpenAI
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
    kwargs = {"api_key": api_key}
    if args.base_url: kwargs["base_url"] = args.base_url
    client = OpenAI(**kwargs)
    budget = Budget(args.budget_usd)

    drafts     = load_json(LIB / "contracts_draft.json")
    candidates = load_json(LIB / "candidates.json")
    parents    = load_json(LIB / "parents.json")
    parents_by_id = {p["skill_id"]: p for p in parents}
    cands_by_id   = {c["candidate_id"]: c for c in candidates}

    # Pre-compute embeddings for each candidate's units + contract once.
    reports = []
    for d in drafts:
        contract = d["contract"]
        if any(k.startswith("_") for k in contract.keys() if k.startswith("_")):
            reports.append({"candidate_id": d["candidate_id"],
                             "decision": "reject",
                             "reason": "extraction_failed"})
            continue

        cand = cands_by_id[d["candidate_id"]]
        member_units = cand["members"]

        # Build texts for embedding
        unit_texts = []
        for u in member_units:
            parent = parents_by_id.get(u["skill_id"], {})
            unit_full = parent.get("procedural_units", [])[u["unit_index"]] \
                        if parent.get("procedural_units") else None
            body = unit_full["text"] if unit_full else u.get("text_preview", "")
            unit_texts.append(f"{u['title']}: {body[:400]}")
        contract_text = (contract.get("trigger","") + " " +
                          " ".join(contract.get("preconditions") or []) + " " +
                          " ".join(contract.get("postconditions") or []) + " " +
                          contract.get("rationale",""))

        try:
            unit_emb = embed_text(client, args.embed_model, unit_texts, budget)
            contract_emb = embed_text(client, args.embed_model, [contract_text], budget)[0]
        except Exception as e:
            print(f"  embed error on {d['candidate_id']}: {e}")
            unit_emb = None; contract_emb = None

        cov   = coverage_check(contract, member_units, parents_by_id,
                                 contract_emb, unit_emb)
        bind  = binding_check(contract, member_units, parents_by_id)
        repl  = replacement_proxy(contract, member_units, parents_by_id)
        risk  = risk_check(contract, parents_by_id)
        accepted, reasons = decide(cov, bind, repl, risk,
                                      args.tau_recall, args.rho, args.tau_risk)

        report = {
            "candidate_id": d["candidate_id"],
            "contract_id":  contract.get("id"),
            "n_units":      cand["n_units"],
            "n_distinct_skills": cand["n_distinct_skills"],
            "coverage":     cov,
            "binding":      bind,
            "replacement":  repl,
            "risk":         risk,
            "decision":     "accept" if accepted else "reject",
            "reasons":      reasons,
        }
        reports.append(report)
        save_json(reports, LIB / "verifier_reports.json")

        cov_r = cov["coverage_recall"]; cov_e = cov["embed_cosine"]
        print(f"  [{d['candidate_id']}] {contract.get('id'):<25} "
              f"cov(rec/emb)={cov_r:.2f}/{cov_e if cov_e else 'n/a'}  "
              f"bind={bind['all_required_bound']}  "
              f"repl={repl['replacement_pass_rate']:.2f}  "
              f"risk={risk['risk_score']}  →  {'ACCEPT' if accepted else 'reject: ' + ', '.join(reasons[:2])}")

    n_accept = sum(1 for r in reports if r.get("decision") == "accept")
    print()
    print(f"verified contracts: {n_accept} / {len(reports)} accepted; budget ${budget.spent:.4f}")


if __name__ == "__main__":
    main()
