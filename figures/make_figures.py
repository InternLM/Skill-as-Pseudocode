#!/usr/bin/env python3
"""Generate publication figures for the Skill-as-Pseudocode paper.

Produces:
  - alfworld_winloss.pdf   per-task win/loss bar (gos vs cos on 134 games)
  - calibration_curve.pdf  operating curve from calibration sweep
  - ablation_bars.pdf      reward by pipeline configuration (component ablation)

Run: python3 make_figures.py
"""
from __future__ import annotations
import os
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


HERE = Path(__file__).parent
plt.rcParams.update({
    "font.size": 9,
    "axes.titlesize": 10,
    "axes.labelsize": 9,
    "legend.fontsize": 8,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "axes.grid": True,
    "grid.linestyle": ":",
    "grid.color": "#888888",
    "grid.alpha": 0.4,
    "axes.spines.top": False,
    "axes.spines.right": False,
})


# Colors matching PPT palette.
C_GOS = "#1F4E79"      # dark blue
C_COS = "#ED7D31"      # orange
C_GREEN = "#2E861F"
C_RED = "#C00000"
C_GREY = "#808080"


def fig_alfworld_winloss():
    """Bar chart: per-task win/loss of gos vs cos on 134 games."""
    runs_dir = Path(os.environ.get("GOS_REPO","graph-of-skills") + "/results/alfworld/gpt-4o-mini")
    gos = {}
    cos = {}
    for f in sorted((runs_dir / "test_sap_main_seed42_gos_mode_gos").glob("idx_*.json")):
        d = json.loads(f.read_text())
        i = int(f.stem.removeprefix("idx_"))
        gos[i] = 1.0 if d.get("reward") else 0.0
    for f in sorted((runs_dir / "test_sap_main_seed42_sap_mode_sap").glob("idx_*.json")):
        d = json.loads(f.read_text())
        i = int(f.stem.removeprefix("idx_"))
        cos[i] = 1.0 if d.get("reward") else 0.0

    fig, ax = plt.subplots(figsize=(7.0, 1.9))
    indices = sorted(set(gos.keys()) | set(cos.keys()))
    width = 0.42
    x = np.arange(len(indices))
    g_rewards = [gos.get(i, 0.0) for i in indices]
    c_rewards = [cos.get(i, 0.0) for i in indices]
    ax.bar(x - width/2, g_rewards, width, label="GoS (baseline)", color=C_GOS)
    ax.bar(x + width/2, c_rewards, width, label="SaP (Ours)", color=C_COS)
    # Mark winning indices with a colored dot below the bar.
    for k, i in enumerate(indices):
        if gos.get(i) and not cos.get(i):
            ax.annotate("G", xy=(k, -0.06), ha="center", va="top",
                        fontsize=6, color=C_GOS, weight="bold")
        elif cos.get(i) and not gos.get(i):
            ax.annotate("C", xy=(k, -0.06), ha="center", va="top",
                        fontsize=6, color=C_COS, weight="bold")
    ax.set_ylim(-0.1, 1.15)
    ax.set_xlabel("Game index (0--133)")
    ax.set_ylabel("Reward")
    ax.set_title("Per-game win/loss: GoS vs SaP on ALFWorld unseen split (134 games)")
    ax.set_xticks(np.arange(0, len(indices), 10))
    ax.set_xticklabels([str(i) for i in range(0, len(indices), 10)])
    ax.legend(loc="upper right", ncol=2, frameon=False)
    plt.tight_layout()
    fig.savefig(HERE / "alfworld_winloss.pdf", bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {HERE / 'alfworld_winloss.pdf'} "
           f"(gos wins {int(sum(g_rewards))}, cos wins {int(sum(c_rewards))})")


def fig_calibration_curve():
    """Operating curve: n_auto vs auto_threshold at FP <= 5%."""
    rpt = Path("${SAP_ROOT:-.}/exp1/results_skills500/calibration.calibrated_backup/operating_curve.json")
    data = json.loads(rpt.read_text())
    # data is a list of {auto, review, n_auto, n_review, n_reject, fp_auto, kappa?}
    pts = sorted(data, key=lambda r: r["auto_threshold"])
    autos = [r["auto_threshold"] for r in pts]
    n_auto = [r["n_auto"] for r in pts]
    n_review = [r["n_review"] for r in pts]
    fp = [r["fp_auto"] * 100 for r in pts]

    fig, ax1 = plt.subplots(figsize=(4.4, 2.6))
    ax1.plot(autos, n_auto, marker="o", color=C_COS, label="$n_{\\rm auto}$")
    ax1.plot(autos, n_review, marker="s", color=C_GREEN, label="$n_{\\rm review}$",
              alpha=0.7)
    # Highlight stricter (0.65) and main (0.30) operating points.
    ax1.axvline(0.30, color=C_COS, linestyle="--", alpha=0.5)
    ax1.axvline(0.65, color=C_GREY, linestyle="--", alpha=0.5)
    ax1.text(0.30, max(n_auto)*0.95, "main\n(0.30)",
             ha="center", va="top", fontsize=7, color=C_COS)
    ax1.text(0.65, max(n_auto)*0.45, "stricter\n(0.65)",
             ha="center", va="top", fontsize=7, color=C_GREY)
    ax1.set_xlabel(r"auto-promote threshold $\tau_{\rm auto}$")
    ax1.set_ylabel("# candidates (of 149)")
    ax1.set_title("Calibration operating curve on skills_500",
                   fontsize=10)
    ax1.legend(loc="upper right", frameon=False)
    # Secondary axis: FP rate (always 0 here, but keep for completeness)
    ax2 = ax1.twinx()
    ax2.plot(autos, fp, marker="^", color=C_RED, alpha=0.6,
              label="FP\\% on neg.")
    ax2.set_ylabel("FP\\% on neg. controls", color=C_RED)
    ax2.tick_params(axis="y", colors=C_RED)
    ax2.set_ylim(-1, 10)
    ax2.spines["right"].set_visible(True)
    ax2.spines["right"].set_color(C_RED)
    ax2.spines["top"].set_visible(False)
    ax2.grid(False)
    plt.tight_layout()
    fig.savefig(HERE / "calibration_curve.pdf", bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {HERE / 'calibration_curve.pdf'}")


def fig_component_ablation():
    """Bar chart: reward by pipeline configuration."""
    data = [
        ("Deterministic\nonly", 0.050, C_RED),
        ("+ RC\ncleanup", 0.100, C_COS),
        ("+ BE + RC\n(Full)", 0.150, C_GREEN),
        ("(ref.)\nGoS", 0.150, C_GOS),
    ]
    fig, ax = plt.subplots(figsize=(4.4, 2.4))
    labels = [d[0] for d in data]
    vals = [d[1] for d in data]
    cols = [d[2] for d in data]
    x = np.arange(len(data))
    bars = ax.bar(x, vals, color=cols, width=0.55)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width()/2, v + 0.003,
                f"{int(v*20)}/20", ha="center", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Avg reward")
    ax.set_ylim(0, 0.20)
    ax.set_title("Pipeline component ablation (ALFWorld 20-game subset)",
                  fontsize=10)
    plt.tight_layout()
    fig.savefig(HERE / "ablation_bars.pdf", bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {HERE / 'ablation_bars.pdf'}")


def fig_per_task_type():
    """Bar chart: per-task-type success rate (single seed, 134 games per mode)."""
    runs_dir = Path(os.environ.get("GOS_REPO","graph-of-skills") + "/results/alfworld/gpt-4o-mini")
    runs = {
        "GoS": ["test_sap_main_seed42_gos_mode_gos"],
        "SaP (Ours)": ["test_sap_main_seed42_sap_mode_sap"],
    }
    from collections import defaultdict

    def task_type(name): return name.split("/")[0].split("-")[0]

    rate = defaultdict(dict)
    counts = defaultdict(int)
    for label, subs in runs.items():
        by_tt = defaultdict(list)
        for sub in subs:
            d = runs_dir / sub
            if not d.exists():
                continue
            for f in sorted(d.glob("idx_*.json")):
                data = json.loads(f.read_text())
                tt = task_type(data.get("name", ""))
                r = 1.0 if data.get("reward") else 0.0
                by_tt[tt].append(r)
                if label == "GoS":
                    counts[tt] += 1
        for tt, rs in by_tt.items():
            rate[tt][label] = sum(rs) / len(rs)

    types = sorted(rate.keys(), key=lambda t: -counts[t])
    labels = list(runs.keys())
    cols = [C_GOS, C_COS]

    fig, ax = plt.subplots(figsize=(6.5, 2.8))
    x = np.arange(len(types))
    width = 0.36
    for i, label in enumerate(labels):
        vals = [rate[tt].get(label, 0.0) for tt in types]
        ax.bar(x + (i - 0.5) * width, vals, width, label=label, color=cols[i])
    ax.set_xticks(x)
    ax.set_xticklabels([t.replace("_", "\n") for t in types],
                        fontsize=7, rotation=0)
    ax.set_ylabel("Success rate")
    ax.set_title("Per-task-type success rate on ALFWorld 134-game (single seed)",
                  fontsize=10)
    ax.legend(loc="upper right", frameon=False, fontsize=8)
    ax.set_ylim(0, 0.35)
    plt.tight_layout()
    fig.savefig(HERE / "per_task_type.pdf", bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {HERE / 'per_task_type.pdf'}")


if __name__ == "__main__":
    try:
        fig_alfworld_winloss()
    except Exception as e:
        print(f"alfworld winloss failed: {e}")
    try:
        fig_calibration_curve()
    except Exception as e:
        print(f"calibration curve failed: {e}")
    try:
        fig_component_ablation()
    except Exception as e:
        print(f"ablation bars failed: {e}")
    try:
        fig_per_task_type()
    except Exception as e:
        print(f"per task type failed: {e}")
