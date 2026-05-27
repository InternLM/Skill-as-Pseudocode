#!/usr/bin/env python3
"""
skip_repair.py — produce v2 'accepted'/'review' manifests
WITHOUT running repair.

Useful when:
  - API quota is depleted so repair_v2 cannot run
  - You want to test the downstream pipeline (optimizer / rewrite /
    agent harness) on the initial verifier output, skipping repair
  - You want to compare "no repair" baseline vs "repair-improved" set

This script reads `evidence_reports.json` and `contracts_draft.json`,
filters by tier, and writes:
  contracts_accepted_v2.json   (auto_promote tier only)
  contracts_review_v2.json     (review tier only)
  evidence_final.json          (same as evidence_reports.json)

Usage:
  python3 skip_repair.py --lib-dir results_anthropic
  python3 skip_repair.py --lib-dir results_gos
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from common import load_json, save_json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib-dir", required=True)
    args = ap.parse_args()
    LIB = ROOT / args.lib_dir

    drafts = load_json(LIB / "contracts_draft.json")
    candidates = load_json(LIB / "candidates.json")
    reports = load_json(LIB / "evidence_reports.json")

    drafts_by_id = {d["candidate_id"]: d for d in drafts}
    cands_by_id  = {c["candidate_id"]: c for c in candidates}

    def build_record(r):
        cid = r["candidate_id"]
        draft = drafts_by_id.get(cid, {})
        cand  = cands_by_id.get(cid, {})
        return {
            "candidate_id":       cid,
            "n_distinct_skills":  r.get("n_distinct_skills",
                                          cand.get("n_distinct_skills", 0)),
            "n_units":            r.get("n_units",
                                          cand.get("n_units", 0)),
            "distinct_skills":    cand.get("distinct_skills", []),
            "final_round":        0,                       # no repair
            "final_tier":         r["decision"],
            "final_contract":     draft.get("contract", {}),
            "final_profile":      r.get("evidence_profile", {}),
            "final_reasons":      r.get("decision_reasons",
                                          r.get("reasons", [])),
            "rounds_attempted":   0,
        }

    auto   = [build_record(r) for r in reports if r["decision"] == "auto_promote"]
    review = [build_record(r) for r in reports if r["decision"] == "review"]

    save_json(auto,   LIB / "contracts_accepted_v2.json")
    save_json(review, LIB / "contracts_review_v2.json")

    # evidence_final is just a copy of evidence_reports (no repair)
    save_json(reports, LIB / "evidence_final.json")

    print(f"  auto_promote: {len(auto)}")
    print(f"  review:       {len(review)}")
    print(f"wrote {LIB / 'contracts_accepted_v2.json'}")
    print(f"wrote {LIB / 'contracts_review_v2.json'}")
    print(f"wrote {LIB / 'evidence_final.json'}")


if __name__ == "__main__":
    main()
