#!/bin/bash
# SaP main run on ALFWorld 134 unseen-split games × 3 seeds × {gos, sap}.
#
# Requires:
#   GOS_REPO      — path to graph-of-skills checkout (with the eval/ patches applied)
#   SAP_ARTIFACTS — path to refactored_library.json (the SaP pre-built artifact)
#   OPENAI_API_KEY (and optionally OPENAI_BASE_URL for proxy)
#
# Output: graph-of-skills/results/alfworld/gpt-4o-mini/test_*_seed*_*/idx_*.json
set -e

GOS_REPO=${GOS_REPO:?set GOS_REPO=path/to/graph-of-skills}
SAP_ARTIFACTS=${SAP_ARTIFACTS:?set SAP_ARTIFACTS=path/to/artifacts}
REFLIB="$SAP_ARTIFACTS/refactored_library.json"

cd "$GOS_REPO"
export ALFWORLD_TEMPERATURE=0

echo "=== SaP main: 134 games × 3 seeds × {gos baseline, SaP} at max_steps=30 ==="
date

for SEED in 42 7 99; do
    export ALFWORLD_SEED=$SEED

    echo
    echo ">>> mode=gos seed=$SEED ms=30 134g $(date '+%H:%M:%S')"
    PYTHONUNBUFFERED=1 uv run python -u -m evaluation.alfworld_run \
        --mode gos --model gpt-4o-mini --use_skill \
        --gos_workspace data/gos_workspace/skills_500_refactored_v1 \
        --skills_dir data/skillsets/skills_500_refactored \
        --split test --max_games 134 --max_steps 30 --max_workers 4 \
        --exp_name sap_main_seed${SEED}_gos \
        > /tmp/sap_main_gos_${SEED}.log 2>&1
    grep "Avg R=" /tmp/sap_main_gos_${SEED}.log | tail -1
    echo "<<< gos seed=$SEED done $(date '+%H:%M:%S')"

    echo
    echo ">>> mode=sap seed=$SEED ms=30 134g $(date '+%H:%M:%S')"
    PYTHONUNBUFFERED=1 uv run python -u -m evaluation.alfworld_run \
        --mode sap --refactored_library "$REFLIB" \
        --model gpt-4o-mini --use_skill \
        --gos_workspace data/gos_workspace/skills_500_refactored_v1 \
        --skills_dir data/skillsets/skills_500_refactored \
        --split test --max_games 134 --max_steps 30 --max_workers 4 \
        --exp_name sap_main_seed${SEED}_sap \
        > /tmp/sap_main_sap_${SEED}.log 2>&1
    grep "Avg R=" /tmp/sap_main_sap_${SEED}.log | tail -1
    echo "<<< sap seed=$SEED done $(date '+%H:%M:%S')"
done

echo
echo "=== DONE ==="
date
