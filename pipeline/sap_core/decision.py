"""
decision.py — 3-tier decision policy from an evidence profile.

Decision tiers:
  - "auto_promote": strong evidence, accept without human review
  - "review":       borderline,    accept after human confirmation
  - "reject":       weak evidence, drop

The policy is parameterised by a `DecisionPolicy` dataclass. Default
values are conservative *initial guesses* that should be replaced by
calibrated values learned from a small gold + negative-control set in
`operating_point.py`.

Hard constraints (always enforced regardless of policy):
  - risk ≥ 0.8 → reject (safety)
  - extraction_failed → reject (LLM refused)

Soft signals (policy-driven, combine into tier):
  - binding_rate     (1.0 means every required input bound on every parent)
  - coverage         (token recall in parent text)
  - replacement_rate
  - risk             (0=safe, 1=risky)

The policy combines these into a "promotion score" and compares against
two thresholds (one for auto_promote, one for review).

Promotion score (in [0, 1]):
  s = w_binding * binding_rate
    + w_coverage * coverage
    + w_replacement * replacement_rate
    - w_risk * risk

With default weights (1.0, 0.7, 0.5, 1.0), a "good" candidate scores
around 2.0 (we normalise by max possible weight sum).
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Literal


Tier = Literal["auto_promote", "review", "reject"]


@dataclass
class DecisionPolicy:
    """Operating-point policy for the 3-tier decision.

    Default values are conservative initial guesses. The
    `operating_point.py` module fits these to a small gold +
    negative-control calibration set.
    """
    # Hard constraints (always enforced)
    hard_risk_max: float = 0.8         # risk ≥ this → reject
    hard_min_binding_rate: float = 0.5 # binding_rate < this → reject

    # Soft weights for the combined promotion score
    w_binding:     float = 1.0
    w_coverage:    float = 0.7
    w_replacement: float = 0.5
    w_risk:        float = 1.0  # subtracted

    # Tier thresholds on the normalised promotion score
    auto_promote_threshold: float = 0.65  # s ≥ this → auto_promote
    review_threshold:       float = 0.35  # s ≥ this & < auto → review

    # Soft requirements (used in tier explanation only)
    soft_min_coverage:    float = 0.30
    soft_min_replacement: float = 0.30

    def max_score(self) -> float:
        """Maximum possible normalised score (binding=1, cov=1, repl=1, risk=0)."""
        return self.w_binding + self.w_coverage + self.w_replacement

    def normalise(self, raw: float) -> float:
        m = self.max_score()
        if m <= 0: return 0.0
        return max(0.0, min(1.0, raw / m))


def _promotion_score(profile: dict, p: DecisionPolicy) -> tuple[float, float]:
    """Compute (raw, normalised) promotion score from the profile."""
    raw = (
        p.w_binding     * profile.get("binding_rate",     0.0) +
        p.w_coverage    * profile.get("coverage",         0.0) +
        p.w_replacement * profile.get("replacement_rate", 0.0) -
        p.w_risk        * profile.get("risk",             0.0)
    )
    return raw, p.normalise(raw)


def decide(profile: dict, policy: DecisionPolicy = None) -> dict:
    """Produce a 3-tier decision + reason list from an evidence profile.

    Returns a dict:
      {
        "decision": "auto_promote" | "review" | "reject",
        "promotion_score": float,
        "promotion_score_raw": float,
        "reasons": [str, ...]
      }
    """
    policy = policy or DecisionPolicy()
    reasons: list[str] = []

    # Hard reject — extraction_failed
    if profile.get("extraction_failed"):
        return {
            "decision": "reject",
            "promotion_score": 0.0,
            "promotion_score_raw": 0.0,
            "reasons": ["extraction_failed: LLM refused to write a contract"],
        }

    risk = profile.get("risk", 0.0)
    binding_rate = profile.get("binding_rate", 0.0)
    coverage = profile.get("coverage", 0.0)
    replacement = profile.get("replacement_rate", 0.0)

    # Hard reject — risk
    if risk >= policy.hard_risk_max:
        return {
            "decision": "reject",
            "promotion_score": 0.0,
            "promotion_score_raw": 0.0,
            "reasons": [f"risk {risk:.2f} ≥ hard limit {policy.hard_risk_max:.2f}"],
        }

    # Hard reject — binding
    if binding_rate < policy.hard_min_binding_rate:
        return {
            "decision": "reject",
            "promotion_score": 0.0,
            "promotion_score_raw": 0.0,
            "reasons": [f"binding_rate {binding_rate:.2f} < hard min "
                         f"{policy.hard_min_binding_rate:.2f}"],
        }

    raw, score = _promotion_score(profile, policy)

    # Soft signal warnings
    soft_reasons = []
    if coverage < policy.soft_min_coverage:
        soft_reasons.append(f"low coverage {coverage:.2f}")
    if replacement < policy.soft_min_replacement:
        soft_reasons.append(f"low replacement {replacement:.2f}")
    if risk > 0.3:
        soft_reasons.append(f"moderate risk {risk:.2f}")

    if score >= policy.auto_promote_threshold:
        tier = "auto_promote"
        reasons = ([f"score {score:.2f} ≥ auto threshold "
                    f"{policy.auto_promote_threshold:.2f}"] + soft_reasons)
    elif score >= policy.review_threshold:
        tier = "review"
        reasons = ([f"score {score:.2f} in review band "
                    f"[{policy.review_threshold:.2f}, "
                    f"{policy.auto_promote_threshold:.2f})"] +
                   soft_reasons)
    else:
        tier = "reject"
        reasons = ([f"score {score:.2f} < review threshold "
                    f"{policy.review_threshold:.2f}"] + soft_reasons)

    return {
        "decision":             tier,
        "promotion_score":      round(score, 4),
        "promotion_score_raw":  round(raw, 4),
        "reasons":              reasons,
    }


def policy_from_dict(d: dict) -> DecisionPolicy:
    """Restore a DecisionPolicy from a dict (e.g. saved JSON)."""
    fields = DecisionPolicy.__dataclass_fields__.keys()
    return DecisionPolicy(**{k: v for k, v in d.items() if k in fields})


def policy_to_dict(p: DecisionPolicy) -> dict:
    return asdict(p)


# Backward-compat helper: map 3-tier → legacy accept/reject string.
def legacy_decision(tier: str, include_review_as_accept: bool = False) -> str:
    """Map 3-tier decision to legacy 'accept'/'reject' string.

    By default 'review' maps to 'reject' (strict). Set
    `include_review_as_accept=True` to fold review into accept (used
    when we want a more permissive recall for downstream evaluation).
    """
    if tier == "auto_promote": return "accept"
    if tier == "review": return "accept" if include_review_as_accept else "reject"
    return "reject"
