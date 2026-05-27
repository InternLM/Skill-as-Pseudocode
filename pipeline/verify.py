#!/usr/bin/env python3
"""
verify.py — v2 verifier driver.

Produces *evidence profiles* (numeric scores + failure signatures) and
applies a 3-tier decision policy (auto_promote / review / reject). Does
NOT do the binary accept/reject from v1 — that is now derived from the
3-tier decision via `legacy_decision()` if downstream tools still need it.

Output files (in `<lib>/`):
  evidence_reports.json   — per-candidate evidence profile + 3-tier decision
  policy.json             — the DecisionPolicy used (for reproducibility)

For backward compatibility, the records also include legacy fields
(coverage, binding, replacement, risk, decision, reasons) so existing
downstream scripts that read `verifier_reports.json` style continue to
work after a `cp evidence_reports.json verifier_reports.json`.

Usage:
  python3 verify.py --lib-dir results_anthropic
  python3 verify.py --lib-dir results_gos
"""
from __future__ import annotations
import os
import argparse, os, sys, json
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from common import load_json, save_json, Budget
from verify_primitives import embed_text
from sap_core.evidence import build_evidence_profile
from sap_core.decision import (
    DecisionPolicy, decide, policy_to_dict, legacy_decision,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key",  default=None)
    ap.add_argument("--embed-model", default="text-embedding-3-small")
    ap.add_argument("--budget-usd",  type=float, default=1.0)
    ap.add_argument("--lib-dir", required=True)

    # DecisionPolicy parameters (override defaults from CLI)
    ap.add_argument("--hard-risk-max",       type=float, default=0.8)
    ap.add_argument("--hard-min-binding-rate", type=float, default=0.5)
    ap.add_argument("--w-binding",           type=float, default=1.0)
    ap.add_argument("--w-coverage",          type=float, default=0.7)
    ap.add_argument("--w-replacement",       type=float, default=0.5)
    ap.add_argument("--w-risk",              type=float, default=1.0)
    ap.add_argument("--auto-promote-threshold", type=float, default=0.65)
    ap.add_argument("--review-threshold",       type=float, default=0.35)
    ap.add_argument("--soft-min-coverage",      type=float, default=0.30)
    ap.add_argument("--soft-min-replacement",   type=float, default=0.30)
    args = ap.parse_args()

    LIB = ROOT / args.lib_dir

    policy = DecisionPolicy(
        hard_risk_max         = args.hard_risk_max,
        hard_min_binding_rate = args.hard_min_binding_rate,
        w_binding             = args.w_binding,
        w_coverage            = args.w_coverage,
        w_replacement         = args.w_replacement,
        w_risk                = args.w_risk,
        auto_promote_threshold= args.auto_promote_threshold,
        review_threshold      = args.review_threshold,
        soft_min_coverage     = args.soft_min_coverage,
        soft_min_replacement  = args.soft_min_replacement,
    )

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

    reports = []
    n_auto, n_review, n_reject = 0, 0, 0

    for d in drafts:
        contract = d["contract"]
        cand     = cands_by_id[d["candidate_id"]]
        member_units = cand["members"]

        # Embeddings (only if contract is non-degenerate)
        unit_emb = None; contract_emb = None
        if not any(k.startswith("_") for k in contract.keys()):
            unit_texts = []
            for u in member_units:
                parent = parents_by_id.get(u["skill_id"], {})
                proc = parent.get("procedural_units", [])
                unit_full = proc[u["unit_index"]] if u["unit_index"] < len(proc) else None
                body = unit_full["text"] if unit_full else u.get("text_preview", "")
                unit_texts.append(f"{u['title']}: {body[:400]}")
            contract_text = (contract.get("trigger", "") + " " +
                              " ".join(contract.get("preconditions") or []) + " " +
                              " ".join(contract.get("postconditions") or []) + " " +
                              contract.get("rationale", ""))
            try:
                unit_emb = embed_text(client, args.embed_model, unit_texts, budget)
                contract_emb = embed_text(client, args.embed_model, [contract_text], budget)[0]
            except Exception as e:
                print(f"  embed error on {d['candidate_id']}: {e}")

        # 1. Build evidence profile (no decision)
        profile = build_evidence_profile(
            candidate=cand, contract=contract, member_units=member_units,
            parents_by_id=parents_by_id,
            contract_emb=contract_emb, unit_emb=unit_emb,
        )

        # 2. Apply policy → 3-tier decision
        dec = decide(profile, policy)

        # 3. Build the unified report (new schema + legacy fields)
        report = {
            "candidate_id":      profile["candidate_id"],
            "contract_id":       profile["contract_id"],
            "n_units":           profile["n_units"],
            "n_distinct_skills": profile["n_distinct_skills"],

            # New schema
            "evidence_profile":  {
                k: v for k, v in profile.items()
                if k not in ("coverage_detail", "binding_detail",
                              "replacement_detail", "risk_detail")
            },
            "decision":          dec["decision"],
            "promotion_score":   dec["promotion_score"],
            "promotion_score_raw": dec["promotion_score_raw"],
            "decision_reasons":  dec["reasons"],

            # Legacy fields (for backward compat with v1 tools)
            "coverage":          profile.get("coverage_detail", {}),
            "binding":           profile.get("binding_detail", {}),
            "replacement":       profile.get("replacement_detail", {}),
            "risk":              profile.get("risk_detail", {}),
            "decision_legacy":   legacy_decision(dec["decision"],
                                                 include_review_as_accept=False),
            "reasons":           dec["reasons"],
        }
        reports.append(report)

        if dec["decision"] == "auto_promote": n_auto += 1
        elif dec["decision"] == "review":     n_review += 1
        else:                                  n_reject += 1

        score = dec["promotion_score"]
        tier  = dec["decision"]
        marker = ("✓✓" if tier == "auto_promote" else
                   "?? " if tier == "review" else "✗ ")
        cid = profile["contract_id"] or "(no contract)"
        ext = "EXT_FAIL " if profile.get("extraction_failed") else ""
        print(f"  [{profile['candidate_id']}] {cid[:25]:<25s} "
              f"cov={profile['coverage']:.2f} bind={profile['binding_rate']:.2f} "
              f"repl={profile['replacement_rate']:.2f} risk={profile['risk']:.2f} "
              f"score={score:.2f}  {marker} {ext}{tier}")

        # Incremental save (so a crash mid-run preserves progress)
        save_json(reports, LIB / "evidence_reports.json")

    # Save policy
    save_json(policy_to_dict(policy), LIB / "policy.json")

    print()
    print(f"  auto_promote: {n_auto}")
    print(f"  review      : {n_review}")
    print(f"  reject      : {n_reject}")
    print(f"  total       : {len(reports)}")
    print(f"  spend       : ${budget.spent:.4f}")
    print()
    print(f"wrote {LIB / 'evidence_reports.json'}")
    print(f"wrote {LIB / 'policy.json'}")


if __name__ == "__main__":
    main()
