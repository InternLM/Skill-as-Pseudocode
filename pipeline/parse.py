#!/usr/bin/env python3
"""
parse.py — adapt _parser_base.py for the
graph-of-skills `skills_500` library (500 skills, includes 37 alfworld-*).

Same SKILL.md format (YAML frontmatter + markdown body + scripts/ dir).

Output: results_skills500/parents.json
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

SKILLS_500_DIR = Path(os.environ.get("GOS_REPO","graph-of-skills") + "/data/skillsets/skills_500")
OUT_DIR        = Path(__file__).parent / "results_skills500"
OUT_DIR.mkdir(parents=True, exist_ok=True)

import _parser_base as ap
ap.REPO        = Path("dummy/not-used")
ap.SKILLS_DIR  = SKILLS_500_DIR
ap.OUT         = OUT_DIR

if __name__ == "__main__":
    ap.main()
