#!/bin/bash
# Clean GoS baseline: 3 seeds × 134 games on the raw (un-refactored) skills_500.
#
# Requires:
#   GOS_REPO — path to graph-of-skills checkout (with the eval/ patches applied)
#   OPENAI_API_KEY (and optionally OPENAI_BASE_URL for proxy)
set -e

GOS_REPO=${GOS_REPO:?set GOS_REPO=path/to/graph-of-skills}
cd "$GOS_REPO"
export ALFWORLD_TEMPERATURE=0

echo "=== GoS baseline (clean): 3 seeds × 134 games on raw skills_500 ==="
date

for SEED in 42 7 99; do
    export ALFWORLD_SEED=$SEED
    echo
    echo ">>> mode=gos seed=$SEED on raw lib $(date '+%H:%M:%S')"
    PYTHONUNBUFFERED=1 uv run python -u -m evaluation.alfworld_run \
        --mode gos --model gpt-4o-mini --use_skill \
        --gos_workspace data/gos_workspace/skills_500_v1 \
        --skills_dir data/skillsets/skills_500 \
        --split test --max_games 134 --max_steps 30 --max_workers 3 \
        --exp_name gos_baseline_seed${SEED} \
        > /tmp/gos_baseline_${SEED}.log 2>&1
    grep "Avg R=" /tmp/gos_baseline_${SEED}.log | tail -1
    echo "<<< gos seed=$SEED done $(date '+%H:%M:%S')"
done

echo
echo "=== DONE ==="
date
