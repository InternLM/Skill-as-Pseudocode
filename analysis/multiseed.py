#!/usr/bin/env python3
"""
multiseed.py — aggregate multi-seed ALFWorld 134-game results.

Reads per-game rewards from
  $GOS_REPO/results/alfworld/gpt-4o-mini/test_{exp_name}_mode_{mode}/idx_*.json
for SEEDS × {mode}. The default exp_name patterns match the
release run scripts:
  GoS baseline:  test_sap_main_seed{N}_gos_mode_gos
  SaP main:      test_sap_main_seed{N}_sap_mode_sap

Override via env vars SAP_EXP_GOS_TEMPLATE / SAP_EXP_SAP_TEMPLATE
(use `{seed}` as the placeholder).

Computes: per-seed wins, mean ± std across seeds, paired McNemar
(sap vs gos) per-seed and pooled, win-set overlap.
"""
from __future__ import annotations
import os
import glob
import json
import math
from pathlib import Path
from statistics import mean, pstdev

ROOT = Path(os.environ.get("GOS_REPO","graph-of-skills") + "/results/alfworld/gpt-4o-mini")
SEEDS = [int(s) for s in os.environ.get("SAP_SEEDS", "42,7,99").split(",")]
MODES = ["gos", "sap"]

EXP_TEMPLATE = {
    "gos": os.environ.get("SAP_EXP_GOS_TEMPLATE", "test_sap_main_seed{seed}_gos_mode_gos"),
    "sap": os.environ.get("SAP_EXP_SAP_TEMPLATE", "test_sap_main_seed{seed}_sap_mode_sap"),
}


def load_run(mode: str, seed: int) -> dict[int, float] | None:
    d = ROOT / EXP_TEMPLATE[mode].format(seed=seed)
    if not d.exists():
        return None
    rewards = {}
    for f in sorted(d.glob("idx_*.json")):
        idx = int(f.stem.removeprefix("idx_"))
        data = json.loads(f.read_text())
        rewards[idx] = 1.0 if data.get("reward") else 0.0
    return rewards if rewards else None


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value: P(X<=min(b,c) | X~Bin(b+c, 0.5))*2."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p_one = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * p_one)


def main():
    runs: dict[tuple[str, int], dict[int, float]] = {}
    for mode in MODES:
        for seed in SEEDS:
            r = load_run(mode, seed)
            if r is None:
                print(f"# missing: mode={mode} seed={seed}")
                continue
            runs[(mode, seed)] = r
            mean_r = sum(r.values()) / len(r)
            n_succ = int(sum(r.values()))
            print(f"# loaded mode={mode} seed={seed} "
                   f"n={len(r)} mean_R={mean_r:.3f} succ={n_succ}")

    print()
    print("# === Per-mode mean ± std across seeds ===")
    for mode in MODES:
        rewards = []
        succs = []
        for seed in SEEDS:
            if (mode, seed) not in runs:
                continue
            r = runs[(mode, seed)]
            rewards.append(sum(r.values()) / len(r))
            succs.append(int(sum(r.values())))
        if not rewards:
            continue
        m = mean(rewards)
        s = pstdev(rewards) if len(rewards) > 1 else 0.0
        print(f"  {mode}: mean_R = {m:.3f} ± {s:.3f}  "
               f"(succ counts per seed: {succs}, n_seeds={len(rewards)})")

    print()
    print("# === Paired McNemar test (cos vs gos) ===")
    # Per-seed McNemar
    for seed in SEEDS:
        g = runs.get(("gos", seed))
        c = runs.get(("sap", seed))
        if not g or not c:
            continue
        common = set(g) & set(c)
        # b = cos wins, gos loses;  c = gos wins, cos loses
        b = sum(1 for i in common if c[i] >= 0.5 and g[i] < 0.5)
        cc = sum(1 for i in common if g[i] >= 0.5 and c[i] < 0.5)
        p = mcnemar_exact(b, cc)
        sig = " (p<0.05)" if p < 0.05 else ""
        print(f"  seed={seed}: cos_wins_gos_lost = {b}, "
               f"gos_wins_cos_lost = {cc}, p = {p:.3f}{sig}")

    # Pooled McNemar (across all seeds, each game treated as independent)
    pooled_b = 0
    pooled_c = 0
    for seed in SEEDS:
        g = runs.get(("gos", seed))
        c = runs.get(("sap", seed))
        if not g or not c:
            continue
        common = set(g) & set(c)
        pooled_b += sum(1 for i in common if c[i] >= 0.5 and g[i] < 0.5)
        pooled_c += sum(1 for i in common if g[i] >= 0.5 and c[i] < 0.5)
    pooled_p = mcnemar_exact(pooled_b, pooled_c)
    pooled_sig = " (p<0.05)" if pooled_p < 0.05 else ""
    print(f"  pooled (n_seeds × 134): b = {pooled_b}, c = {pooled_c}, "
           f"p = {pooled_p:.3f}{pooled_sig}")

    print()
    print("# === Win-set overlap per seed ===")
    for seed in SEEDS:
        g = runs.get(("gos", seed))
        c = runs.get(("sap", seed))
        if not g or not c:
            continue
        g_wins = {i for i, v in g.items() if v >= 0.5}
        c_wins = {i for i, v in c.items() if v >= 0.5}
        both = g_wins & c_wins
        g_only = g_wins - c_wins
        c_only = c_wins - g_wins
        print(f"  seed={seed}: gos_wins={len(g_wins)} cos_wins={len(c_wins)} "
               f"both={len(both)} gos_only={len(g_only)} cos_only={len(c_only)}")

    print()
    print("# === Per-game vote across seeds (majority-vote reward) ===")
    # For each game idx, compute fraction of seeds in which each mode won.
    all_idx = set()
    for r in runs.values():
        all_idx |= set(r)
    for mode in MODES:
        n_majority = 0
        for idx in sorted(all_idx):
            wins = [runs[(mode, seed)].get(idx, 0.0) >= 0.5
                     for seed in SEEDS if (mode, seed) in runs]
            if wins and sum(wins) > len(wins) / 2:
                n_majority += 1
        print(f"  {mode}: majority-vote wins = {n_majority} / {len(all_idx)}")


if __name__ == "__main__":
    main()
