"""
evidence.py — Evidence profile schema + computation.

A verifier now emits an *evidence profile* (numeric scores + failure
signatures) instead of a binary accept/reject. The 3-tier decision is
made downstream in `decision.py` from this profile, parameterised by an
operating-point policy.

Profile schema (per candidate):

  {
    "candidate_id":      str,
    "contract_id":       str | None,
    "n_units":           int,
    "n_distinct_skills": int,
    "extraction_failed": bool,            # LLM refused to write a contract

    # Scalar numbers (each in [0,1] unless noted)
    "coverage":          float,           # token recall in parent text
    "coverage_embed":    float | None,    # cosine in embedding space
    "binding_rate":      float,           # fraction of required inputs that are
                                          # bound on EVERY source parent
    "min_binding_rate":  float,           # fraction of (input × parent) pairs bound
    "replacement_rate":  float,
    "risk":              float,           # 0=safe, 1=high risk
    "contract_tokens":   int,
    "parents_covered":   int,

    # Failure signature: structured reason(s) the candidate would be a
    # rejected at default policy. Used by the split module.
    "failure_signature": {
      "kind": "binding" | "coverage" | "replacement" | "risk" |
              "extraction" | None,
      "unbound_inputs": [input_name, ...],  # for binding failures
      "unbound_parents": [skill_id, ...],   # for binding failures
      "low_recall_parents": [skill_id, ...],# for coverage failures (per-unit < τ)
      "risk_flags": [label, ...],           # for risk failures
    },

    # Detailed sub-reports (kept for debugging + repair input)
    "coverage_detail":     {...},
    "binding_detail":      {...},
    "replacement_detail":  {...},
    "risk_detail":         {...},
  }

The verifier in this module reuses the existing `coverage_check`,
`binding_check`, `replacement_proxy`, `risk_check` functions from
`verify_primitives.py` and packages the results into the profile schema.
"""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from verify_primitives import (
    coverage_check, binding_check, replacement_proxy, risk_check,
    tokenize, embed_text,
)


def _extract_failure_signature(coverage, binding, replacement, risk) -> dict:
    """Build a structured failure signature from the sub-check outputs.

    The 'kind' field picks ONE primary failure mode (priority order:
    risk > binding > coverage > replacement). The detail fields list
    every input/parent that contributed to the failure so the split
    module can group cluster members accordingly.
    """
    sig = {
        "kind": None,
        "unbound_inputs": [],
        "unbound_parents": [],
        "low_recall_parents": [],
        "risk_flags": [],
    }
    # Risk hard fail
    if risk.get("risk_score", 0) >= 0.7:
        sig["kind"] = "risk"
        sig["risk_flags"] = list((risk.get("flags") or {}).keys())
        return sig
    # Binding
    if not binding.get("all_required_bound", True):
        sig["kind"] = "binding"
        for f in binding.get("per_input", []):
            if not f.get("all_bound"):
                sig["unbound_inputs"].append(f["input_name"])
                for pp in f.get("per_parent", []):
                    if not pp.get("bound"):
                        if pp["skill_id"] not in sig["unbound_parents"]:
                            sig["unbound_parents"].append(pp["skill_id"])
        return sig
    # Coverage (use per-unit detail if present to find low-recall parents)
    if coverage.get("coverage_recall", 0) < 0.5:
        sig["kind"] = "coverage"
        # Compute per-parent recall (min across units)
        per_parent_overlap = {}
        per_parent_total = {}
        for u in coverage.get("per_unit", []) or []:
            sid = u.get("skill_id")
            per_parent_overlap[sid] = max(per_parent_overlap.get(sid, 0),
                                             u.get("n_overlap_tokens", 0))
            per_parent_total[sid] = max(per_parent_total.get(sid, 1),
                                           u.get("n_unit_tokens", 1))
        contract_n = coverage.get("contract_n_tokens", 0) or 1
        for sid, ov in per_parent_overlap.items():
            recall = ov / contract_n
            if recall < 0.3:
                sig["low_recall_parents"].append(sid)
        return sig
    # Replacement
    if replacement.get("replacement_pass_rate", 0) < 0.5:
        sig["kind"] = "replacement"
        return sig
    return sig


def build_evidence_profile(candidate: dict, contract: dict,
                            member_units: list, parents_by_id: dict,
                            contract_emb=None, unit_emb=None) -> dict:
    """Compute the full evidence profile for one (candidate, contract) pair.

    Returns the profile dict described at the top of this module. If
    the contract is `_extraction_failed`, returns a profile with
    `extraction_failed=True` and all scores set to 0.
    """
    candidate_id = candidate.get("candidate_id")

    if any(k.startswith("_") for k in contract.keys()):
        return {
            "candidate_id":      candidate_id,
            "contract_id":       None,
            "n_units":           candidate.get("n_units", len(member_units)),
            "n_distinct_skills": candidate.get("n_distinct_skills", 0),
            "extraction_failed": True,
            "coverage":          0.0,
            "coverage_embed":    None,
            "binding_rate":      0.0,
            "min_binding_rate":  0.0,
            "replacement_rate":  0.0,
            "risk":              0.0,
            "contract_tokens":   0,
            "parents_covered":   candidate.get("n_distinct_skills", 0),
            "failure_signature": {
                "kind": "extraction",
                "unbound_inputs": [],
                "unbound_parents": [],
                "low_recall_parents": [],
                "risk_flags": [],
            },
        }

    cov   = coverage_check(contract, member_units, parents_by_id,
                              contract_emb, unit_emb)
    bind  = binding_check(contract, member_units, parents_by_id)
    repl  = replacement_proxy(contract, member_units, parents_by_id)
    risk  = risk_check(contract, parents_by_id)

    # Binding scalars:
    per_input = bind.get("per_input", [])
    n_inputs = len(per_input)
    binding_rate = (
        sum(1 for f in per_input if f.get("all_bound")) / n_inputs
        if n_inputs > 0 else 1.0
    )
    n_pairs = sum(f.get("n_parents", 0) for f in per_input)
    n_pairs_bound = sum(f.get("n_bound", 0) for f in per_input)
    min_binding_rate = n_pairs_bound / n_pairs if n_pairs > 0 else 1.0

    sig = _extract_failure_signature(cov, bind, repl, risk)

    return {
        "candidate_id":      candidate_id,
        "contract_id":       contract.get("id"),
        "n_units":           candidate.get("n_units", len(member_units)),
        "n_distinct_skills": candidate.get("n_distinct_skills", 0),
        "extraction_failed": False,
        "coverage":          float(cov.get("coverage_recall", 0.0)),
        "coverage_embed":    cov.get("embed_cosine"),
        "binding_rate":      round(binding_rate, 4),
        "min_binding_rate":  round(min_binding_rate, 4),
        "replacement_rate":  float(repl.get("replacement_pass_rate", 0.0)),
        "risk":              float(risk.get("risk_score", 0.0)),
        "contract_tokens":   int(cov.get("contract_n_tokens", 0)),
        "parents_covered":   candidate.get("n_distinct_skills", 0),
        "failure_signature": sig,
        # detailed sub-reports for downstream use (repair, split)
        "coverage_detail":     cov,
        "binding_detail":      bind,
        "replacement_detail":  repl,
        "risk_detail":         risk,
    }
