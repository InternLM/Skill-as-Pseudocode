"""
operating_point.py — Calibrate the DecisionPolicy from:
  (a) a small gold-labeled calibration set (Cohen's κ maximization)
  (b) negative controls (false-positive rate constraint)
  (c) user budgets (max review count, max promotions, target recall, ...)

Calibration strategy: grid search over (auto_promote_threshold,
review_threshold) keeping other policy fields fixed at provided
defaults. We use a small grid (e.g. 21 × 21) which is cheap.

Objective:
  - PRIMARY:  fp_rate(auto_promote on negative_controls) ≤ fp_target
              (default 5%)
  - SECONDARY: maximize Cohen's κ on gold (if gold provided),
               OR maximize n_auto_promote subject to fp constraint
  - TERTIARY: stay within user-specified budget for review tier

Outputs an optimized DecisionPolicy + a calibration report.
"""
from __future__ import annotations
import json
import math
from dataclasses import asdict, replace
from itertools import product
from pathlib import Path
from typing import Optional

from .decision import DecisionPolicy, decide, policy_from_dict


# ── Cohen's kappa on accept/reject ─────────────────────────────────────
def cohen_kappa(pred: list[str], gold: list[str], positive_labels=("accept",)) -> float:
    """Cohen's κ on binary accept/reject classification.

    pred and gold are lists of strings. Anything in `positive_labels` is
    treated as 'accept'; otherwise 'reject'.
    """
    if not pred or len(pred) != len(gold): return 0.0
    n = len(pred)

    def to_bin(s): return "accept" if s in positive_labels else "reject"
    pred_b = [to_bin(p) for p in pred]
    gold_b = [to_bin(g) for g in gold]

    # Observed agreement
    n_agree = sum(1 for p, g in zip(pred_b, gold_b) if p == g)
    p_obs = n_agree / n
    # Expected agreement (chance)
    pred_pos = sum(1 for p in pred_b if p == "accept") / n
    gold_pos = sum(1 for g in gold_b if g == "accept") / n
    p_exp = pred_pos * gold_pos + (1 - pred_pos) * (1 - gold_pos)
    if abs(1 - p_exp) < 1e-9: return 1.0 if p_obs == 1.0 else 0.0
    return (p_obs - p_exp) / (1 - p_exp)


# ── Apply policy to a list of profiles ─────────────────────────────────
def apply_policy(profiles: list[dict], policy: DecisionPolicy) -> list[str]:
    return [decide(p, policy)["decision"] for p in profiles]


# ── Calibration ─────────────────────────────────────────────────────────
def calibrate_policy(
    profiles: list[dict],
    gold_labels: Optional[list[Optional[str]]] = None,
    negative_profiles: Optional[list[dict]] = None,
    base_policy: DecisionPolicy = None,
    fp_target: float = 0.05,
    auto_thresh_grid: list[float] = None,
    review_thresh_grid: list[float] = None,
    treat_review_as_accept: bool = False,
    max_review_budget: Optional[int] = None,
    verbose: bool = False,
) -> dict:
    """Grid-search the (auto, review) thresholds to maximize Cohen's κ
    on gold while keeping fp_rate on negative controls ≤ fp_target.

    Args:
      profiles: list of evidence profiles (with `decision` key from
                a default-policy run; we ignore that and recompute).
      gold_labels: list of "accept" | "reject" | None (one per profile).
                Profiles with None gold are excluded from κ but still
                contribute to FP rate analysis.
      negative_profiles: list of evidence profiles from negative controls.
      base_policy: starting policy (other fields kept; only auto+review
                thresholds varied).
      fp_target: max acceptable FP rate at auto_promote tier on negatives.
      auto_thresh_grid: candidate auto thresholds. Default 0.30..1.00 step 0.05.
      review_thresh_grid: candidate review thresholds. Default 0.15..0.80 step 0.05.
      treat_review_as_accept: if True, review counts as accept in κ.
      max_review_budget: optional hard cap on total review-tier output.

    Returns dict with:
      best_policy: DecisionPolicy
      best_kappa: float
      best_fp_rate: float
      best_n_auto: int
      best_n_review: int
      grid_results: list of all (auto, review, κ, fp, n_auto, n_review, n_reject)
    """
    base = base_policy or DecisionPolicy()
    auto_grid   = auto_thresh_grid   or [round(0.30 + 0.05*i, 3) for i in range(15)]
    review_grid = review_thresh_grid or [round(0.10 + 0.05*i, 3) for i in range(15)]

    # Filter gold labels
    if gold_labels is not None:
        if len(gold_labels) != len(profiles):
            raise ValueError("len(gold_labels) != len(profiles)")
        gold_pairs = [(i, p, g) for i, (p, g) in enumerate(zip(profiles, gold_labels))
                       if g is not None]
    else:
        gold_pairs = []

    accept_labels = {"auto_promote"}
    if treat_review_as_accept:
        accept_labels.add("review")

    rows = []
    for a, r in product(auto_grid, review_grid):
        if r >= a:  # review must be strictly below auto
            continue
        policy = replace(base, auto_promote_threshold=a, review_threshold=r)

        # Apply to candidates + negatives
        cand_decisions = apply_policy(profiles, policy)
        n_auto   = sum(1 for d in cand_decisions if d == "auto_promote")
        n_review = sum(1 for d in cand_decisions if d == "review")
        n_reject = sum(1 for d in cand_decisions if d == "reject")

        # FP on negatives
        if negative_profiles:
            neg_decisions = apply_policy(negative_profiles, policy)
            n_neg = len(neg_decisions)
            fp_auto = sum(1 for d in neg_decisions if d == "auto_promote") / max(n_neg, 1)
            fp_auto_or_review = sum(1 for d in neg_decisions
                                         if d in ("auto_promote", "review")) / max(n_neg, 1)
        else:
            fp_auto = 0.0; fp_auto_or_review = 0.0

        # Cohen's κ vs gold (if available)
        if gold_pairs:
            pred = [cand_decisions[i] for i, _, _ in gold_pairs]
            gold = [g for _, _, g in gold_pairs]
            kappa = cohen_kappa(pred, gold, positive_labels=accept_labels)
        else:
            kappa = None

        rows.append({
            "auto_threshold":  a,
            "review_threshold": r,
            "n_auto":           n_auto,
            "n_review":         n_review,
            "n_reject":         n_reject,
            "fp_auto":          round(fp_auto, 3),
            "fp_auto_or_review": round(fp_auto_or_review, 3),
            "kappa":            kappa if kappa is None else round(kappa, 3),
        })

        if verbose:
            print(f"  auto={a:.2f} rev={r:.2f}  "
                  f"n_auto={n_auto:>3} n_rev={n_review:>3} n_rej={n_reject:>3}  "
                  f"fp={fp_auto:.2%}  κ={kappa if kappa is None else f'{kappa:.3f}'}")

    # Selection: prefer κ if gold available, else max n_auto subject to fp constraint
    def feasible(row):
        if row["fp_auto"] > fp_target: return False
        if max_review_budget is not None and row["n_review"] > max_review_budget:
            return False
        return True

    feasible_rows = [r for r in rows if feasible(r)]
    if not feasible_rows:
        # No feasible row — pick the one with min fp_auto
        best = min(rows, key=lambda r: r["fp_auto"])
    elif any(r["kappa"] is not None for r in feasible_rows):
        # Maximize κ
        best = max(feasible_rows, key=lambda r: r["kappa"] if r["kappa"] is not None else -1)
    else:
        # No gold — maximize n_auto (subject to fp constraint)
        best = max(feasible_rows, key=lambda r: (r["n_auto"], -r["fp_auto"]))

    best_policy = replace(base,
                            auto_promote_threshold=best["auto_threshold"],
                            review_threshold=best["review_threshold"])

    return {
        "best_policy":     best_policy,
        "best_policy_dict": asdict(best_policy),
        "best_kappa":      best["kappa"],
        "best_fp_rate":    best["fp_auto"],
        "best_n_auto":     best["n_auto"],
        "best_n_review":   best["n_review"],
        "best_n_reject":   best["n_reject"],
        "fp_target":       fp_target,
        "feasible_count":  len(feasible_rows),
        "total_grid_count": len(rows),
        "grid_results":    rows,
    }


def build_operating_curve(
    profiles: list[dict],
    negative_profiles: list[dict] = None,
    base_policy: DecisionPolicy = None,
    threshold_grid: list[float] = None,
) -> list[dict]:
    """Vary auto_promote_threshold from 0.0 → 1.0 with review_threshold
    set to a fixed fraction below; return n_promoted vs fp_rate curve.

    Used for paper §4 operating curve plots.
    """
    base = base_policy or DecisionPolicy()
    grid = threshold_grid or [round(0.10 + 0.05*i, 3) for i in range(19)]
    rows = []
    for t in grid:
        # Set review_threshold = t - 0.20 (or floor at 0.05)
        rev = max(0.05, t - 0.20)
        policy = replace(base, auto_promote_threshold=t, review_threshold=rev)
        cand_decisions = apply_policy(profiles, policy)
        n_auto   = sum(1 for d in cand_decisions if d == "auto_promote")
        n_review = sum(1 for d in cand_decisions if d == "review")
        if negative_profiles:
            neg_decisions = apply_policy(negative_profiles, policy)
            fp_auto = (sum(1 for d in neg_decisions if d == "auto_promote") /
                        max(len(neg_decisions), 1))
        else:
            fp_auto = 0.0
        rows.append({
            "auto_threshold":  t,
            "review_threshold": rev,
            "n_auto":           n_auto,
            "n_review":         n_review,
            "fp_auto":          round(fp_auto, 3),
        })
    return rows
