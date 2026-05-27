#!/bin/bash
# SkillsBench SaP run via Harbor + per-task Docker containers, gpt-5-codex.
#
# Requires:
#   GOS_REPO — path to graph-of-skills checkout
#   harbor   — installed (https://github.com/...)
#   OPENAI_API_KEY (and OPENAI_BASE_URL if using proxy)
set -e

GOS_REPO=${GOS_REPO:?set GOS_REPO=path/to/graph-of-skills}
HARBOR=${HARBOR:-$HOME/.local/bin/harbor}
CONFIG=${SAP_HARBOR_CONFIG:-evaluation/skillsbench/experiments/configs/sap_full84.yaml}

cd "$GOS_REPO"
echo "=== SkillsBench full 84 (SaP mode, gpt-5-codex) ==="
date
"$HARBOR" run -c "$CONFIG" --yes > /tmp/sap_skillsbench.log 2>&1
echo "harbor exit=$?"
date
tail -25 /tmp/sap_skillsbench.log
