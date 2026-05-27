#!/usr/bin/env python3
"""
analyze_pertask_type.py — per-task-type breakdown for ALFWorld 134-game.

ALFWorld unseen split has 6 canonical task types
(pick_and_place_simple, pick_clean_then_place_in_recep,
 pick_cool_then_place_in_recep, pick_heat_then_place_in_recep,
 look_at_obj_in_light, pick_two_obj_and_place).

We report success counts per (mode, task_type), and ask: which task
types does each retrieval mode help the most?
"""
from __future__ import annotations
import os
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(os.environ.get("GOS_REPO","graph-of-skills") + "/results/alfworld/gpt-4o-mini")

# Map run dir suffix → label for the table
RUNS = {
    "test_main_134_mode_gos":                       "gos",
    "test_main_134_mode_sap":              "cos (default)",
    "test_main_134_calibrated_mode_sap":   "cos (calibrated)",
}


def task_type(name: str) -> str:
    """Extract canonical task type from ALFWorld game name."""
    # e.g. "pick_and_place_simple-Mug-None-Desk-308/trial_..."
    return name.split("/")[0].split("-")[0]


def main():
    # collect per-run, per-idx rewards + types
    results: dict[str, dict[int, dict]] = defaultdict(dict)
    for run_dir, label in RUNS.items():
        d = ROOT / run_dir
        if not d.exists():
            print(f"# skip missing: {run_dir}")
            continue
        for f in sorted(d.glob("idx_*.json")):
            idx = int(f.stem.removeprefix("idx_"))
            data = json.loads(f.read_text())
            r = 1.0 if data.get("reward") else 0.0
            tt = task_type(data.get("name", ""))
            results[label][idx] = {"reward": r, "task_type": tt}

    # Aggregate per (mode, task_type)
    by_type: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for label, run in results.items():
        for idx, info in run.items():
            by_type[info["task_type"]][label].append(info["reward"])

    # Print per-task-type table
    print()
    print(f"{'task_type':<35} | ", end="")
    print(" | ".join(f"{l:<18}" for l in RUNS.values()))
    print("-" * (35 + 3 + 21 * len(RUNS)))
    # also pool counts for "all"
    pool_n = defaultdict(int)
    pool_succ = defaultdict(int)
    for tt in sorted(by_type):
        n_each = {label: len(rs) for label, rs in by_type[tt].items()}
        # all modes saw the same set of games per type
        n = max(n_each.values()) if n_each else 0
        cells = []
        for label in RUNS.values():
            rs = by_type[tt].get(label, [])
            if rs:
                succ = int(sum(rs))
                pool_n[label] += len(rs)
                pool_succ[label] += succ
                cells.append(f"{succ:>2}/{len(rs):>2} ({succ/max(len(rs),1):.2f})")
            else:
                cells.append("   --   ")
        print(f"{tt:<35} | " + " | ".join(f"{c:<18}" for c in cells))

    # Pooled "all"
    print("-" * (35 + 3 + 21 * len(RUNS)))
    cells = []
    for label in RUNS.values():
        succ = pool_succ[label]
        n = pool_n[label]
        cells.append(f"{succ:>2}/{n:>3} ({succ/max(n,1):.3f})")
    print(f"{'all (134)':<35} | " + " | ".join(f"{c:<18}" for c in cells))


if __name__ == "__main__":
    main()
