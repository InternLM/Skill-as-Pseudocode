# Evaluation patches for Graph-of-Skills

The three files under this directory are drop-in replacements for the
matching files in a Graph-of-Skills checkout. They add SaP's
hierarchical-only retrieval pool, the substituted-bundle injection at
retrieval time, and the ALFWorld step-budget fix that lets
`SkillRequest` cycles run without consuming the `--max_steps` budget.

| File in this repo                  | Overwrite in the GoS checkout                                                       |
| ---------------------------------- | ----------------------------------------------------------------------------------- |
| `eval/alfworld/skill.py`           | `<GOS_REPO>/evaluation/skill.py`                                                    |
| `eval/alfworld/alfworld_run.py`    | `<GOS_REPO>/evaluation/alfworld_run.py`                                             |
| `eval/skillsbench/query.py`        | `<GOS_REPO>/evaluation/skillsbench/graphskills_assets/query.py`                     |

After patching, the agent recognises a new `--mode sap` flag (in
addition to the existing `gos` and `all_full`). The shell scripts under
`scripts/` already use this convention.

## Building the SaP skillset that the patched runtime expects

The patched `skill.py` reads the refactored library from the path passed
via `--refactored_library` and looks up per-skill rewrites against the
GoS workspace at `<GOS_REPO>/data/skillsets/skills_500_refactored/`.
The release ships the rewrites under `artifacts/refactored_library_skillset/`;
copy them in once:

```bash
mkdir -p $GOS_REPO/data/skillsets/skills_500_refactored
cp artifacts/refactored_library_skillset/*.rewritten.md \
   $GOS_REPO/data/skillsets/skills_500_refactored/
```

Then re-index the workspace with GoS's own tooling:

```bash
cd $GOS_REPO
uv run gos index data/skillsets/skills_500_refactored \
                 --workspace data/gos_workspace/skills_500_refactored_v1 \
                 --clear
```

## Runtime markers used by the patches

The patched runtime detects SaP-promoted children by the YAML frontmatter
marker `_sap_role: child` written by the build script
(`pipeline/build_skillset.py`). Top-level retrieval drops any candidate
carrying that marker, so children remain reachable only through their
wrapping parent's `invoke(κ, args)` placeholder.
