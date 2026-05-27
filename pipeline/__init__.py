"""Skill-as-Pseudocode (SaP) pipeline.

Stages:
    Stage 1: parse.py                — markdown SKILL.md → parents.json
    Stage 2: propose_candidates.py   — procedural units → candidate clusters
    Stage 3: extract_contracts.py    — clusters → draft typed contracts (LLM)
    Stage 4: verify.py               — 4-check deterministic verifier
    Stage 4.5: negative_controls.py  — generate synthetic negatives for calibration
    Stage 4.6: calibrate.py          — choose (τauto, τrev) at 0% FP
    Stage 5: skip_repair.py          — apply calibrated policy
    Stage 6: refactor.py             — Binding Extraction (BE) + Rewrite Cleanup (RC)
    Stage 7: build_skillset.py       — emit per-skill SKILL.md library
"""
