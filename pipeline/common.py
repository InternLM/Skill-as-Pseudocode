"""Shared utilities across experiments — IO, budget tracking, math helpers."""
from __future__ import annotations
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict

import numpy as np


# ── IO ───────────────────────────────────────────────────────────────────────

def save_json(obj, path, **kw):
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False, **kw))


def load_json(path):
    return json.loads(Path(path).read_text())


# ── Pricing + budget guard ──────────────────────────────────────────────────

PRICE_PER_MTOK = {
    "gpt-4o-mini":            (0.15, 0.60),
    "gpt-4o":                 (2.50, 10.00),
    "text-embedding-3-small": (0.02, 0.0),
}


def price_call(model: str, in_tok: int, out_tok: int) -> float:
    p_in, p_out = PRICE_PER_MTOK.get(model, (0.0, 0.0))
    return (in_tok * p_in + out_tok * p_out) / 1_000_000


class Budget:
    """Hard-capped spend tracker."""
    def __init__(self, cap_usd: float):
        self.cap = cap_usd
        self.spent = 0.0
        self.by_model: Dict[str, float] = defaultdict(float)
        self.by_phase: Dict[str, float] = defaultdict(float)

    def add(self, model: str, in_tok: int, out_tok: int, phase: str = "") -> float:
        c = price_call(model, in_tok, out_tok)
        self.spent += c
        self.by_model[model] += c
        if phase:
            self.by_phase[phase] += c
        return c

    def tripped(self) -> bool:
        return self.spent >= self.cap

    def summary(self) -> str:
        lines = [f"  spent=${self.spent:.4f} / cap=${self.cap:.2f}"]
        for m, v in sorted(self.by_model.items()):
            lines.append(f"    {m:<30s} ${v:.4f}")
        if self.by_phase:
            lines.append("  by phase:")
            for p, v in sorted(self.by_phase.items()):
                lines.append(f"    {p:<30s} ${v:.4f}")
        return "\n".join(lines)


# ── Cosine similarity ────────────────────────────────────────────────────────

def cos_sim(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Row-wise cosine similarity matrix. a:[n,d], b:[m,d] → [n,m]."""
    a = a / (np.linalg.norm(a, axis=-1, keepdims=True) + 1e-9)
    b = b / (np.linalg.norm(b, axis=-1, keepdims=True) + 1e-9)
    return a @ b.T


# ── Statistical helpers ──────────────────────────────────────────────────────

def qmean(grade: dict) -> float | None:
    """Mean of (coverage, specificity, executability) from a judge grade dict."""
    vals = [grade.get(d) for d in ("coverage", "specificity", "executability")
            if isinstance(grade.get(d), (int, float))]
    return sum(vals) / len(vals) if vals else None


def _phi(x: float) -> float:
    """Normal CDF (Abramowitz & Stegun approx)."""
    a1 =  0.254829592; a2 = -0.284496736; a3 =  1.421413741
    a4 = -1.453152027; a5 =  1.061405429; p  =  0.3275911
    sign = 1 if x >= 0 else -1
    xa = abs(x) / math.sqrt(2)
    tt = 1.0 / (1.0 + p * xa)
    y = 1.0 - (((((a5 * tt + a4) * tt) + a3) * tt + a2) * tt + a1) * tt * math.exp(-xa * xa)
    return 0.5 * (1.0 + sign * y)


def paired_stats(tasks, judged_by_sys, sys_a, sys_b) -> dict:
    """Paired t-test on judge-mean deltas between two systems, task-aligned."""
    deltas = []
    wa = wb = tied = 0
    for t in tasks:
        tid = t["task_id"]
        ga = judged_by_sys[sys_a].get(tid, {}).get("grade", {})
        gb = judged_by_sys[sys_b].get(tid, {}).get("grade", {})
        qa, qb = qmean(ga), qmean(gb)
        if qa is None or qb is None:
            continue
        d = qa - qb
        deltas.append(d)
        if abs(d) < 1e-6: tied += 1
        elif d > 0: wa += 1
        else: wb += 1
    n = len(deltas)
    if n < 2:
        return {"n": n, "mean": 0, "sem": 0, "t": 0, "p_approx": 1.0,
                "wins": (wa, wb, tied)}
    m = sum(deltas) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in deltas) / (n - 1))
    sem = sd / math.sqrt(n) if sd else 0
    t_stat = m / sem if sem else 0
    p_approx = 2 * (1 - _phi(abs(t_stat)))
    return {"n": n, "mean": round(m, 3), "sem": round(sem, 3),
            "t": round(t_stat, 3), "p_approx": round(p_approx, 5),
            "wins": (wa, wb, tied)}


def holm_bonferroni(pvals):
    """Return Holm-step-down adjusted p-values (ordered to match input)."""
    n = len(pvals)
    order = sorted(range(n), key=lambda i: pvals[i])
    adj = [0.0] * n
    running = 0
    for rank, idx in enumerate(order):
        val = (n - rank) * pvals[idx]
        running = max(running, val)
        adj[idx] = min(1.0, running)
    return adj
