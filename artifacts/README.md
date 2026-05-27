# Pre-built artifacts

These files are the SaP pipeline's outputs on the public
`skills_500` library, packaged so that downstream users (the agent
runtime, or anyone wanting to inspect the refactored library) can skip
re-running the LLM-dependent stages.

| File                                  | What it is                                                |
| ------------------------------------- | --------------------------------------------------------- |
| `policy.json`                         | Calibrated thresholds (τauto=0.30, τrev=0.10) — 0% FP on synthetic negative controls; promotes 80 children. |
| `refactored_library.json`             | The agent-facing library: parent skeletons with `invoke(κ,args)` placeholders + the inlined child-contract specs. |
| `contracts_accepted_v2.json`          | The 80 promoted child contracts (typed pseudocode, with verifier scores). |
| `evidence_final.json`                 | Verifier scores (Coverage / Binding / Replacement / Risk) for every candidate cluster, before policy is applied. |
| `refactored_library_skillset/`        | Per-skill SKILL.md rewrites (parent_anthropic_*.rewritten.md). Drop into `<GOS_REPO>/data/skillsets/skills_500_refactored/` before re-indexing. |
| `intermediate/`                       | Per-stage JSON outputs for inspection or partial reruns (Stage-1 `parents.json`, Stage-2 `candidates.json`, Stage-3 `contracts_draft.json`, Stage-4 evidence/contract files, calibration `negative_controls/`). |

## Reproducing these

```bash
export SAP_ARTIFACTS=./fresh
export GOS_REPO=path/to/graph-of-skills
cd pipeline/
python3 parse.py
python3 propose_candidates.py --lib-dir $SAP_ARTIFACTS
python3 extract_contracts.py  --lib-dir $SAP_ARTIFACTS   # LLM (~$1)
python3 verify.py             --lib-dir $SAP_ARTIFACTS
python3 negative_controls.py  --lib-dir $SAP_ARTIFACTS --n-per-class 10
python3 calibrate.py          --lib-dir $SAP_ARTIFACTS
python3 skip_repair.py        --lib-dir $SAP_ARTIFACTS
python3 refactor.py           --lib-dir $SAP_ARTIFACTS --llm-bindings
python3 build_skillset.py                                  # writes the skillset
```

Numbers will be near-identical at `temperature=0`, modulo low-level
OpenAI API non-determinism on Stage 3 / BE / RC LLM calls.
