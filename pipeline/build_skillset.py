#!/usr/bin/env python3
"""
build_skillset.py — convert the v2-pipeline refactored library
artifacts into a Skills-Bench-compatible skillset directory.

Inputs:
  - ${SAP_ARTIFACTS:-artifacts}/refactored/
        parent_<anthropic_<name>>.rewritten.md   (301 rewritten parents)
        refactored_library.json                  (49 promoted child contracts)

Source library:
  - ${GOS_REPO:-graph-of-skills}/data/skillsets/skills_500/
        <500 subdirs>/SKILL.md (+ references/, scripts/)

Output (new skillset dir):
  - ${GOS_REPO:-graph-of-skills}/data/skillsets/skills_500_refactored/
        <each promoted child as its own dir with SKILL.md>
        <each rewritten parent overwrites the SKILL.md, others copied verbatim>

Strategy:
  1. Symlink-copy every skills_500/<name>/ into skills_500_refactored/<name>/
     (preserves scripts/, references/).
  2. For each rewritten parent, replace SKILL.md with the rewritten body
     (keep the original frontmatter so retrieval metadata is preserved).
  3. Add a new dir per promoted child containing a SKILL.md built from the
     contract (trigger / preconditions / postconditions etc).
"""
from __future__ import annotations
import os
import json, re, shutil
from pathlib import Path

REFACTORED_DIR = Path(os.environ.get("SAP_ARTIFACTS","artifacts") + "/refactored")
SRC_LIB        = Path(os.environ.get("GOS_REPO","graph-of-skills") + "/data/skillsets/skills_500")
DST_LIB        = Path(os.environ.get("GOS_REPO","graph-of-skills") + "/data/skillsets/skills_500_refactored")

REWRITTEN_PREFIX = "parent_anthropic_"
REWRITTEN_SUFFIX = ".rewritten.md"


def parse_frontmatter(text: str) -> tuple[str, str]:
    """Split a SKILL.md into (frontmatter_yaml, body). Returns ("",text) if absent."""
    if not text.startswith("---"):
        return "", text
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return "", text
    return m.group(1), m.group(2)


def render_child_block_for_parent(parent_id: str, lib_json: dict) -> str:
    """Build the 'Child contracts referenced' block for a parent: for every
    invoke(child, args) placeholder the parent contains, inline the child's
    trigger / I/O schema / pre/post conditions AND the concrete benchmark
    action template (call_sites.original_unit_text). Returns empty string
    if the parent has no invoke()s.
    """
    call_sites = [cs for cs in lib_json.get("call_sites", []) if cs.get("parent_id") == parent_id]
    if not call_sites:
        return ""
    from collections import defaultdict
    by_child = defaultdict(list)
    for cs in call_sites:
        by_child[cs["child_skill_id"]].append(cs)
    contract_lookup = {}
    for c in lib_json.get("child_skills", []):
        cid = c.get("child_skill_id") or c.get("contract", {}).get("id", "")
        if cid:
            contract_lookup[cid] = c.get("contract", {})
    out = [
        "## Child contracts referenced",
        ("_The skill body below contains `invoke(<child_id>, args)` placeholders. "
         "Each child contract's trigger, I/O schema, pre/postconditions, and the "
         "concrete benchmark action template (the procedural text that the "
         "`invoke` replaces) are listed here. **Emit the concrete actions; the "
         "environment does NOT execute `invoke(...)` directly.**_\n"),
    ]
    for cid, sites in by_child.items():
        contract = contract_lookup.get(cid, {})
        out.append(f"### child: `{cid}`")
        if contract.get("trigger"):
            out.append(f"- trigger: {contract['trigger']}")
        inputs = contract.get("input_schema") or {}
        if inputs:
            props = inputs.get("properties") or inputs
            keys = list(props.keys()) if isinstance(props, dict) else []
            if keys:
                out.append(f"- inputs: {{ " + ", ".join(keys) + " }")
        outputs = contract.get("output_schema") or {}
        if outputs:
            props = outputs.get("properties") or outputs
            keys = list(props.keys()) if isinstance(props, dict) else []
            if keys:
                out.append(f"- outputs: {{ " + ", ".join(keys) + " }")
        if contract.get("preconditions"):
            out.append("- preconditions: " + "; ".join(str(p)[:80] for p in contract["preconditions"][:3]))
        if contract.get("postconditions"):
            out.append("- postconditions: " + "; ".join(str(p)[:80] for p in contract["postconditions"][:3]))
        for cs in sites:
            orig = (cs.get("original_unit_text") or "").strip()
            if not orig:
                continue
            bindings = cs.get("bindings") or {}
            title = cs.get("original_unit_title") or "<section>"
            out.append(f"- replaces parent section \"{title}\" — concrete action template:")
            for L in orig.split("\n")[:8]:
                out.append(f"    {L}")
            if bindings:
                out.append("    bindings: " + ", ".join(f"{k}={v}" for k, v in bindings.items()))
        out.append("")
    return "\n".join(out) + "\n"


def child_contract_to_skill_md(contract: dict, child_id: str) -> str:
    """Render a promoted child contract as a SKILL.md ready for the GoS indexer.
    Marked with `_sap_role: child` in frontmatter so query-time retrieval can
    filter it out of the top-level retrieval pool — children are designed to
    be reached via the parent's invoke() expansion, not selected directly."""
    desc = contract.get("rationale") or contract.get("trigger") or ""
    body_parts = []
    body_parts.append(f"# {child_id}\n")
    if contract.get("trigger"):
        body_parts.append(f"**Trigger**: {contract['trigger']}\n")
    if contract.get("rationale"):
        body_parts.append(f"**Rationale**: {contract['rationale']}\n")

    def fmt_schema_block(label: str, schema: dict):
        if not schema:
            return ""
        props = schema.get("properties", {})
        if not props:
            return ""
        lines = [f"\n## {label}\n"]
        for k, v in props.items():
            t = v.get("type", "?") if isinstance(v, dict) else "?"
            d = v.get("description", "") if isinstance(v, dict) else ""
            lines.append(f"- `{k}` ({t}): {d}")
        return "\n".join(lines) + "\n"

    body_parts.append(fmt_schema_block("Inputs", contract.get("input_schema", {})))
    body_parts.append(fmt_schema_block("Outputs", contract.get("output_schema", {})))

    if contract.get("preconditions"):
        body_parts.append("\n## Preconditions\n" + "\n".join(f"- {p}" for p in contract["preconditions"]) + "\n")
    if contract.get("postconditions"):
        body_parts.append("\n## Postconditions\n" + "\n".join(f"- {p}" for p in contract["postconditions"]) + "\n")
    if contract.get("side_effects"):
        body_parts.append("\n## Side effects\n" + "\n".join(f"- {s}" for s in contract["side_effects"]) + "\n")
    if contract.get("resources"):
        body_parts.append("\n## Resources\n" + "\n".join(f"- {r}" for r in contract["resources"]) + "\n")

    body = "\n".join(p for p in body_parts if p)
    # YAML frontmatter (name + description) so the parser indexes correctly.
    # `_sap_role: child` marker tells query.py to skip this entry at top-level retrieval.
    fm = f"name: {child_id}\ndescription: {desc[:300].replace(chr(10),' ')}\n_sap_role: child"
    return f"---\n{fm}\n---\n{body}"


def main():
    if DST_LIB.exists():
        shutil.rmtree(DST_LIB)
    DST_LIB.mkdir(parents=True)

    # 1. Copy each src skill dir verbatim
    n_copy = 0
    for src in SRC_LIB.iterdir():
        if not src.is_dir():
            continue
        dst = DST_LIB / src.name
        shutil.copytree(src, dst, dirs_exist_ok=False, symlinks=False)
        n_copy += 1
    print(f"copied {n_copy} skill dirs")

    # 2. Replace SKILL.md for each rewritten parent
    # Inject the "Child contracts referenced" block immediately after the
    # frontmatter so that (a) cat'ing the file shows it before any 8K
    # shell-output truncation kicks in and (b) the retrieval payload's first
    # ~1-2K chars contain executable action templates.
    lib_json = json.loads((REFACTORED_DIR / "refactored_library.json").read_text())
    n_rewritten = 0
    for rw in REFACTORED_DIR.glob(f"{REWRITTEN_PREFIX}*{REWRITTEN_SUFFIX}"):
        skill_name = rw.name[len(REWRITTEN_PREFIX):-len(REWRITTEN_SUFFIX)]
        target_dir = DST_LIB / skill_name
        if not target_dir.exists():
            print(f"  skip rewrite (no skill dir): {skill_name}")
            continue
        # Preserve original frontmatter (name + description for retrieval),
        # tag with `_sap_role: parent_rewritten` so downstream tools can
        # distinguish parents that contain invoke() placeholders.
        orig = (target_dir / "SKILL.md").read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(orig)
        if fm:
            fm = fm.rstrip() + "\n_sap_role: parent_rewritten"
        # Build child-contracts-referenced block from call_sites (concrete
        # action templates + contract specs). Parent id in lib uses the
        # "anthropic_" prefix convention.
        child_block = render_child_block_for_parent(f"anthropic_{skill_name}", lib_json)
        new_body = rw.read_text(encoding="utf-8")
        body_with_child_block = (child_block + "\n" + new_body) if child_block else new_body
        new_content = f"---\n{fm}\n---\n{body_with_child_block}" if fm else body_with_child_block
        (target_dir / "SKILL.md").write_text(new_content, encoding="utf-8")
        n_rewritten += 1
    print(f"rewrote {n_rewritten} parent SKILL.md files")

    # 3. Add each promoted child as a new skill dir
    n_children = 0
    for child in lib_json.get("child_skills", []):
        contract = child.get("contract", {})
        # Use contract.id (cleaner name) — falls back to child_skill_id
        child_id = (contract.get("id") or child.get("child_skill_id") or "").strip()
        if not child_id:
            continue
        child_dir = DST_LIB / child_id
        if child_dir.exists():
            # Name collision with an existing parent — prefix to disambiguate
            child_dir = DST_LIB / f"cos-child-{child_id}"
        # Some contract ids appear multiple times across children
        # (e.g., LLM gave the same id to two clusters). Add numeric
        # suffix to make the directory unique.
        if child_dir.exists():
            base = child_dir.name
            i = 2
            while (DST_LIB / f"{base}-{i}").exists():
                i += 1
            child_dir = DST_LIB / f"{base}-{i}"
        child_dir.mkdir(parents=True, exist_ok=False)
        skill_md = child_contract_to_skill_md(contract, child_dir.name)
        (child_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
        n_children += 1
    print(f"added {n_children} child contract skill dirs")

    print(f"\nTotal dirs in refactored library: {len(list(DST_LIB.iterdir()))}")


if __name__ == "__main__":
    main()
