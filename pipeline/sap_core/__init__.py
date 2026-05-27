"""sap_core — Skill-as-Pseudocode core library.

Modules:
    evidence           — build the 4-check profile (coverage/binding/replacement/risk)
    decision           — three-tier decision policy (auto_promote/review/reject)
    operating_point    — threshold calibration grid + selection
    negative_controls  — synthetic negative cluster generators
    rewrite            — substitute invoke() placeholders into parents
    bindings_pass      — Binding Extraction (BE) — ground argument bindings per call-site
    rewrite_llm_cleanup — Rewrite Cleanup (RC) — fix residual prose conflicts
    metrics            — utility scoring + reporting helpers
    split              — recursive split of over-wide clusters (disabled by default)
    agent_harness      — agent runner helpers
"""
__version__ = "0.1.0"
