# Skill-as-Pseudocode (SaP)

**Refactoring Skill Libraries to Pseudocode for LLM Agents.**

This repository contains the reference implementation of SaP — a verified
pipeline that converts free-form markdown skill libraries into typed
pseudocode contracts, plus the evaluation hooks used to measure SaP against
the [Graph-of-Skills](https://github.com/...) retrieval baseline on ALFWorld
and SkillsBench.

> If you use this code, please cite our paper. *(BibTeX entry to be added.)*

---

## Repository layout

```
sap/
├── pipeline/            5+2-stage SaP pipeline (Parser → Proposer → Extractor
│   ├── parse.py         → Verifier → Refactor + Binding Extraction, Rewrite
│   ├── propose_candidates.py    Cleanup).  Each Stage is a CLI script.
│   ├── extract_contracts.py
│   ├── verify.py
│   ├── negative_controls.py
│   ├── calibrate.py
│   ├── skip_repair.py
│   ├── refactor.py
│   ├── build_skillset.py        Builds the per-skill SKILL.md library used
│   │                            by the evaluation runtime.
│   └── sap_core/        Library code: evidence profile, decision policy,
│                        BE/RC passes, rewrite logic.
│
├── eval/                Drop-in modifications to the Graph-of-Skills
│   ├── alfworld/        evaluation runtime.  See eval/README.md for how
│   │   ├── skill.py     to apply them.
│   │   └── alfworld_run.py
│   └── skillsbench/
│       └── query.py
│
├── analysis/            Post-hoc analysis (multi-seed aggregation,
│   ├── multiseed.py     per-task-type breakdown).
│   └── pertask_type.py
│
├── figures/             Scripts that regenerate the paper figures.
│   └── make_figures.py
│
├── scripts/             Shell drivers for the three main runs.
│   ├── run_alfworld_sap.sh
│   ├── run_alfworld_gos_baseline.sh
│   └── run_skillsbench_sap.sh
│
├── artifacts/           Pre-computed pipeline outputs so you can skip the
│   ├── policy.json                       (calibrated thresholds)
│   ├── refactored_library.json           (the SaP-refactored library used by
│   │                                      the agent at run time)
│   ├── contracts_accepted_v2.json        (the 80 promoted child contracts)
│   ├── evidence_final.json               (verifier scores per candidate)
│   ├── refactored_library_skillset/      (per-skill SKILL.md rewrites that
│   │                                      replace the GoS data/skillsets/...)
│   └── intermediate/                     (per-stage JSON outputs + negative
│                                          controls)
│
├── requirements.txt
├── LICENSE
└── README.md
```

---

## Quick start

```bash
# 1. Clone the upstream evaluation repo (Graph-of-Skills) next to this one
git clone https://github.com/.../graph-of-skills.git
export GOS_REPO=$(pwd)/graph-of-skills

# 2. Apply the SaP eval patches
#    (overwrite three files in the GoS checkout with the versions in eval/)
cp eval/alfworld/skill.py             $GOS_REPO/evaluation/skill.py
cp eval/alfworld/alfworld_run.py      $GOS_REPO/evaluation/alfworld_run.py
cp eval/skillsbench/query.py          $GOS_REPO/evaluation/skillsbench/graphskills_assets/query.py

# 3. Install the modified GoS library + this pipeline's deps
cd $GOS_REPO && uv sync && cd -
pip install -r requirements.txt

# 4. Make sure the SaP refactored skillset is in place where GoS expects it
mkdir -p $GOS_REPO/data/skillsets/skills_500_refactored
cp artifacts/refactored_library_skillset/*.rewritten.md $GOS_REPO/data/skillsets/skills_500_refactored/
# (then re-index via GoS's own tooling — see eval/README.md)

# 5. Use the pre-built artifacts and run the agent
export OPENAI_API_KEY=sk-...
export SAP_ARTIFACTS=$(pwd)/artifacts
bash scripts/run_alfworld_sap.sh
```

To reproduce the pipeline from scratch (replace the artifacts):

```bash
# Pipeline starts from $GOS_REPO/data/skillsets/skills_500
export GOS_REPO=...        # path to graph-of-skills
export SAP_ARTIFACTS=./out # where to write outputs

cd pipeline/
python3 parse.py                                   # Stage 1
python3 propose_candidates.py --lib-dir $SAP_ARTIFACTS  # Stage 2
python3 extract_contracts.py  --lib-dir $SAP_ARTIFACTS  # Stage 3 (LLM)
python3 verify.py             --lib-dir $SAP_ARTIFACTS  # Stage 4 (4 deterministic checks)
python3 negative_controls.py  --lib-dir $SAP_ARTIFACTS --n-per-class 10
python3 calibrate.py          --lib-dir $SAP_ARTIFACTS
python3 skip_repair.py        --lib-dir $SAP_ARTIFACTS
python3 refactor.py           --lib-dir $SAP_ARTIFACTS --llm-bindings
python3 build_skillset.py                          # writes the SKILL.md library
```

Total LLM cost for the full pipeline on `skills_500` is approximately
$1.30 against `gpt-4o-mini` at temperature 0.

---

## Pre-built artifacts

For the impatient, `artifacts/` ships everything the agent needs at run
time so you can skip the pipeline entirely:

| File                                      | Source           | What it is                                            |
| ----------------------------------------- | ---------------- | ----------------------------------------------------- |
| `policy.json`                             | calibrate.py     | Calibrated thresholds (τauto=0.30, τrev=0.10)         |
| `refactored_library.json`                 | refactor.py      | Library used by the agent at retrieval time           |
| `contracts_accepted_v2.json`              | verify.py        | The 80 promoted child contracts                       |
| `evidence_final.json`                     | verify.py        | Verifier scores (Coverage/Binding/Replacement/Risk) per candidate |
| `refactored_library_skillset/*.md`        | build_skillset.py | Per-skill markdown rewrites (drop into GoS data dir) |
| `intermediate/`                           | (per stage)      | Stage-1 to Stage-3 outputs for inspection             |

---

## Environment

The pipeline uses a handful of environment variables (with sensible
defaults) so it can be relocated without code edits:

| Variable        | Default            | Meaning                                                                 |
| --------------- | ------------------ | ----------------------------------------------------------------------- |
| `GOS_REPO`      | `graph-of-skills`  | Path to the Graph-of-Skills checkout (with SaP eval patches applied).   |
| `SAP_ARTIFACTS` | `artifacts`        | Where the pipeline reads/writes per-stage JSON outputs.                 |
| `OPENAI_API_KEY` | —                 | Required for Stage 3 / 4.5 / 6 LLM calls and for the agent.             |
| `OPENAI_BASE_URL` | —                | Optional, for an OpenAI-compatible proxy.                               |

---
