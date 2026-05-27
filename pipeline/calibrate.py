#!/usr/bin/env python3
"""
calibrate.py — calibrate the v2 DecisionPolicy on:

  (a) a small gold-labeled calibration set (Cohen's κ maximization)
  (b) negative controls (false-positive rate constraint)
  (c) optional user budgets

Also produces the operating curve (Phase 2.3) used for paper §4 plots.

Inputs:
  - evidence_reports.json (real candidates, from verify.py)
  - negative_controls/evidence_reports.json (negative controls)
  - OPTIONAL: gold_calibration.json
      [{"candidate_id": "text_cand_001", "gold_decision": "accept"}, ...]
      Anything not present is treated as unlabeled (excluded from κ).

Outputs (in <lib>/calibration/):
  - calibration_report.json
  - calibration_report.txt
  - operating_curve.json
  - policy_calibrated.json  (apply this in subsequent runs via --policy)

Usage:
  python3 calibrate.py --lib-dir results_anthropic
  python3 calibrate.py --lib-dir results_anthropic \\
        --gold results_anthropic/gold_calibration.json
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from common import load_json, save_json
from sap_core.decision import DecisionPolicy, policy_to_dict
from sap_core.operating_point import (
    calibrate_policy, build_operating_curve, cohen_kappa, apply_policy,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib-dir", required=True)
    ap.add_argument("--negative-controls-dir", default=None,
                     help="defaults to <lib>/negative_controls/")
    ap.add_argument("--gold", default=None,
                     help="path to gold_calibration.json. "
                          "Each entry: {candidate_id, gold_decision}.")
    ap.add_argument("--fp-target", type=float, default=0.05)
    ap.add_argument("--max-review-budget", type=int, default=None)
    ap.add_argument("--treat-review-as-accept", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    LIB = ROOT / args.lib_dir
    NEG_DIR = (Path(args.negative_controls_dir)
               if args.negative_controls_dir
               else LIB / "negative_controls")
    OUT = LIB / "calibration"
    OUT.mkdir(parents=True, exist_ok=True)

    # 1. Load candidate evidence profiles
    reports = load_json(LIB / "evidence_reports.json")
    profiles = [r["evidence_profile"] for r in reports]
    cand_ids = [r["candidate_id"]   for r in reports]

    print(f"loaded {len(profiles)} candidates from {LIB.name}")

    # 2. Load negative-control profiles (if any)
    neg_profiles = []
    if (NEG_DIR / "evidence_reports.json").exists():
        neg_reports = load_json(NEG_DIR / "evidence_reports.json")
        neg_profiles = [r["evidence_profile"] for r in neg_reports]
        print(f"loaded {len(neg_profiles)} negative controls from {NEG_DIR.name}")
    else:
        print(f"WARN: no negative controls found at {NEG_DIR}")

    # 3. Load gold (if any)
    gold_labels = [None] * len(profiles)
    if args.gold and Path(args.gold).exists():
        gold = load_json(args.gold)
        gold_by_id = {g["candidate_id"]: g["gold_decision"] for g in gold
                       if "candidate_id" in g and "gold_decision" in g}
        n_gold = 0
        for i, cid in enumerate(cand_ids):
            if cid in gold_by_id:
                gold_labels[i] = gold_by_id[cid]
                n_gold += 1
        print(f"loaded gold for {n_gold} / {len(profiles)} candidates")
    else:
        print("no gold annotations provided — calibration will use n_auto + "
              "FP constraint only (no κ)")

    # 4. Calibrate
    print("\nCalibrating policy (grid search)...")
    result = calibrate_policy(
        profiles=profiles,
        gold_labels=gold_labels,
        negative_profiles=neg_profiles,
        fp_target=args.fp_target,
        treat_review_as_accept=args.treat_review_as_accept,
        max_review_budget=args.max_review_budget,
        verbose=args.verbose,
    )

    best_policy = result["best_policy"]
    save_json(policy_to_dict(best_policy), OUT / "policy_calibrated.json")
    save_json({k: v for k, v in result.items() if k != "best_policy"},
                OUT / "calibration_report.json")

    # 5. Operating curve
    print("\nBuilding operating curve...")
    curve = build_operating_curve(profiles, neg_profiles,
                                       base_policy=best_policy)
    save_json(curve, OUT / "operating_curve.json")

    # 6. Text report
    L = ["=" * 80, "  Policy Calibration Report", "=" * 80, ""]
    L.append(f"  Library:           {LIB.name}")
    L.append(f"  Candidates:        {len(profiles)}")
    L.append(f"  Gold labels:       {sum(1 for g in gold_labels if g is not None)}")
    L.append(f"  Negative controls: {len(neg_profiles)}")
    L.append(f"  FP target:         {args.fp_target*100:.0f}%")
    L.append("")
    L.append(f"  Best (auto_threshold, review_threshold) = "
              f"({best_policy.auto_promote_threshold:.2f}, "
              f"{best_policy.review_threshold:.2f})")
    L.append("")
    L.append(f"  Result on candidates:")
    L.append(f"    n_auto:    {result['best_n_auto']}")
    L.append(f"    n_review:  {result['best_n_review']}")
    L.append(f"    n_reject:  {result['best_n_reject']}")
    L.append(f"  FP rate (auto on negatives):  {result['best_fp_rate']*100:.1f}%")
    if result["best_kappa"] is not None:
        L.append(f"  Cohen's κ (gold subset):      {result['best_kappa']:.3f}")
    L.append("")
    L.append(f"  Feasible grid cells: {result['feasible_count']} / "
              f"{result['total_grid_count']}")
    L.append("")
    L.append("  Operating Curve (auto_threshold → n_auto, fp_auto):")
    for row in curve:
        L.append(f"    auto={row['auto_threshold']:.2f} rev={row['review_threshold']:.2f}  "
                  f"n_auto={row['n_auto']:>3} n_review={row['n_review']:>3} "
                  f"fp={row['fp_auto']*100:.1f}%")
    text = "\n".join(L)
    (OUT / "calibration_report.txt").write_text(text)
    print()
    print(text)
    print(f"\nwrote {OUT / 'policy_calibrated.json'}")
    print(f"wrote {OUT / 'calibration_report.txt'}")
    print(f"wrote {OUT / 'operating_curve.json'}")


if __name__ == "__main__":
    main()
