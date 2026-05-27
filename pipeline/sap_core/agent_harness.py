"""
agent_harness.py — 5-baseline agent execution harness.

Per doc §3.3, the main experiment compares:

  1. Flat                — agent sees full parent SKILL.md text.
  2. Flat-summary        — agent sees an LLM-summarised parent.
  3. Add-child-only      — agent sees full parent SKILL.md + child
                            contracts, but parent is NOT rewritten.
  4. Unverified factorized — clustering + LLM child + parent rewrite,
                              but NO verifier (uses ALL initial cluster
                              candidates, regardless of evidence profile).
  5. Ours                — parent skeleton with invoke() placeholders
                            + verified child contracts (auto_promote tier).

Each baseline takes:
  - task: {user_prompt, intended_child_skill, gold_artifacts, ...}
  - resources: prebuilt context strings produced by build_resources()

and returns a system + user message pair ready to send to gpt-4o-mini.

Each baseline must reference the SAME underlying library; the only
variable is HOW the library is presented.

The driver script (exp_agent_main.py) ties everything together: loads
all 5 system contexts, iterates over tasks, calls the LLM, computes
metrics, and writes per-task + aggregated reports.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Optional


# ── Shared system instruction ─────────────────────────────────────────
SYSTEM_INSTRUCTION = """You are an LLM agent that helps the user accomplish
tasks using the skills described below.

Output instructions:
  - Be substantive. Don't refuse reasonable requests.
  - When the task aligns with a child skill (an `invoke(child_id, {...})`
    contract), produce that call AND fill in all REQUIRED inputs with
    values grounded in the user's prompt.
  - When you produce outputs, name each output field declared in the
    child skill's `output_schema` so the calling pipeline can find them.
  - Keep responses focused. Do not add unrelated commentary.

The available skills are described below."""


# ── Resource builders for each baseline ───────────────────────────────
def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars: return text
    return text[:max_chars] + f"\n\n[... truncated, {len(text) - max_chars} chars omitted ...]"


def build_flat_resource(parents: list[dict],
                          max_chars_per_parent: int = 20000) -> str:
    """Baseline 1: full parent SKILL.md text for every parent.

    For each parent, concatenate all procedural_units with their
    markdown headings. Truncate per-parent at max_chars_per_parent.
    """
    out = []
    for p in parents:
        name = p.get("name", p["skill_id"])
        desc = p.get("description", "")
        per_parent = [f"## SKILL: {name}\n\n", f"_{desc}_\n\n"]
        for u in p.get("procedural_units", []):
            h = "#" * u.get("level", 3) + " " + u.get("title", "")
            per_parent.append(f"{h}\n{u.get('text', '')}\n\n")
        block = _truncate("".join(per_parent), max_chars_per_parent)
        out.append(block)
        out.append("\n\n---\n\n")
    return "".join(out)


def build_flat_summary_resource(parent_summaries: dict) -> str:
    """Baseline 2: LLM-summarised parents.

    Requires parent_summaries dict {skill_id: summary_text} produced
    by a pre-running summarisation pass (exp_summarise_parents.py).
    If not provided, falls back to the parent's description field.
    """
    out = []
    for sid, summary in parent_summaries.items():
        out.append(f"## SKILL: {sid}\n\n{summary}\n\n---\n\n")
    return "".join(out)


def build_add_child_only_resource(parents: list[dict],
                                       child_contracts: list[dict],
                                       max_chars_per_parent: int = 20000) -> str:
    """Baseline 3: full parent SKILL.md + child contracts (but parent
    NOT rewritten)."""
    flat_text = build_flat_resource(parents, max_chars_per_parent)
    child_text = ["\n## AVAILABLE CHILD SKILLS\n\n"]
    for c in child_contracts:
        cid = c.get("id") or c.get("child_skill_id", "?")
        child_text.append(f"### child: `{cid}`\n")
        child_text.append(f"trigger: {c.get('trigger', '')}\n\n")
        child_text.append("input_schema:\n")
        for name, spec in (c.get("input_schema") or {}).items():
            req = "(required)" if spec.get("required") else "(optional)"
            desc = spec.get("description", "")
            child_text.append(f"  - {name} {req}: {desc}\n")
        child_text.append("output_schema:\n")
        for name, spec in (c.get("output_schema") or {}).items():
            desc = (spec.get("description") if isinstance(spec, dict) else "") or ""
            child_text.append(f"  - {name}: {desc}\n")
        child_text.append("\n")
    return flat_text + "".join(child_text)


def build_unverified_resource(parents_rewritten_unverified: list[dict],
                                  unverified_child_contracts: list[dict],
                                  all_parents: list[dict] = None,
                                  max_chars_per_parent: int = 20000) -> str:
    """Baseline 4: parent rewrite using UNVERIFIED child contracts.

    Includes ALL parents (rewritten + untouched) for fair comparison
    with Flat / Ours.
    """
    rewritten_ids = {pr["parent_id"] for pr in parents_rewritten_unverified}
    out = ["\n## SKILL LIBRARY (factored, UNVERIFIED contracts)\n\n"]
    for pr in parents_rewritten_unverified:
        out.append(f"### parent: {pr['parent_id']}  [factored]\n\n")
        out.append(_truncate(pr.get("rewritten_text", ""),
                                max_chars_per_parent) + "\n\n")
    if all_parents:
        for p in all_parents:
            if p["skill_id"] in rewritten_ids: continue
            per_parent = [f"### parent: {p['skill_id']}\n\n",
                            f"_{p.get('description', '')}_\n\n"]
            for u in p.get("procedural_units", []):
                h = "#" * u.get("level", 3) + " " + u.get("title", "")
                per_parent.append(f"{h}\n{u.get('text', '')}\n\n")
            out.append(_truncate("".join(per_parent), max_chars_per_parent))
            out.append("\n---\n\n")
    out.append("\n## AVAILABLE CHILD SKILLS (UNVERIFIED — may have unbound inputs)\n\n")
    for c in unverified_child_contracts:
        cid = c.get("id") or "?"
        out.append(f"### child: `{cid}`\n")
        out.append(f"trigger: {c.get('trigger', '')}\n\n")
        out.append("input_schema:\n")
        for name, spec in (c.get("input_schema") or {}).items():
            req = "(required)" if spec.get("required") else "(optional)"
            out.append(f"  - {name} {req}\n")
        out.append("\n")
    return "".join(out)


def build_ours_resource(parents_rewritten: list[dict],
                          verified_child_contracts: list[dict],
                          all_parents: list[dict] = None,
                          max_chars_per_parent: int = 20000) -> str:
    """Baseline 5: rewritten parents + verified child contracts.

    Each parent_rewritten has invoke() placeholders inline; each
    child contract has fully-specified inputs/outputs.

    `all_parents` is the full parents.json list — for parents that
    have no call-sites, we include their original SKILL.md text so
    that the agent has full library coverage (not just the rewritten
    subset). This makes the comparison vs Flat baseline fair.
    """
    rewritten_ids = {pr["parent_id"] for pr in parents_rewritten}
    out = ["\n## SKILL LIBRARY (factored)\n\n"]
    # Rewritten parents first (with invoke() placeholders)
    for pr in parents_rewritten:
        out.append(f"### parent: {pr['parent_id']}  [factored]\n\n")
        out.append(_truncate(pr.get("rewritten_text", ""),
                                max_chars_per_parent) + "\n\n")
    # Untouched parents (full original SKILL.md) — apply same truncation
    # as the Flat baseline for fair comparison.
    if all_parents:
        for p in all_parents:
            if p["skill_id"] in rewritten_ids: continue
            per_parent = [f"### parent: {p['skill_id']}\n\n",
                            f"_{p.get('description', '')}_\n\n"]
            for u in p.get("procedural_units", []):
                h = "#" * u.get("level", 3) + " " + u.get("title", "")
                per_parent.append(f"{h}\n{u.get('text', '')}\n\n")
            out.append(_truncate("".join(per_parent), max_chars_per_parent))
            out.append("\n---\n\n")
    out.append("\n## VERIFIED CHILD SKILL CONTRACTS\n\n")
    for c in verified_child_contracts:
        cid = c.get("id") or "?"
        out.append(f"### child: `{cid}`\n")
        out.append(f"trigger: {c.get('trigger', '')}\n\n")
        out.append("input_schema:\n")
        for name, spec in (c.get("input_schema") or {}).items():
            req = "(required)" if spec.get("required") else "(optional)"
            desc = spec.get("description", "")
            out.append(f"  - {name} {req}: {desc}\n")
        out.append("output_schema:\n")
        for name, spec in (c.get("output_schema") or {}).items():
            desc = (spec.get("description") if isinstance(spec, dict) else "") or ""
            out.append(f"  - {name}: {desc}\n")
        prec = c.get("preconditions") or []
        if prec:
            out.append("preconditions:\n")
            for p in prec:
                out.append(f"  - {p}\n")
        postc = c.get("postconditions") or []
        if postc:
            out.append("postconditions:\n")
            for p in postc:
                out.append(f"  - {p}\n")
        out.append("\n")
    return "".join(out)


# ── Build messages for a given system + task ──────────────────────────
def make_messages(system_name: str, resource_text: str,
                    user_prompt: str) -> list[dict]:
    """Standard message format for any baseline.

    The system message contains the SYSTEM_INSTRUCTION plus the
    resource (parent text, contracts, etc.). User message is the
    task prompt verbatim.
    """
    system = (SYSTEM_INSTRUCTION + "\n\n" + resource_text).strip()
    return [
        {"role": "system", "content": system},
        {"role": "user",   "content": user_prompt},
    ]


# ── Resource dispatch ─────────────────────────────────────────────────
BASELINE_NAMES = ["Flat", "Flat-summary", "Add-child-only",
                   "Unverified-factorized", "Ours"]


def all_resources(parents: list[dict],
                    parent_summaries: Optional[dict],
                    parents_rewritten_ours: list[dict],
                    verified_child_contracts: list[dict],
                    parents_rewritten_unverified: Optional[list[dict]] = None,
                    unverified_child_contracts: Optional[list[dict]] = None,
                    max_chars_per_parent: int = 20000) -> dict:
    """Build the resource strings for all 5 baselines at once.

    Returns dict {baseline_name: resource_text}. Each is passed into
    make_messages() at run time. All 5 baselines see the SAME library
    coverage; the variable is HOW each parent is presented.
    """
    out = {}
    out["Flat"] = build_flat_resource(parents, max_chars_per_parent)
    out["Flat-summary"] = (build_flat_summary_resource(parent_summaries or {})
                            if parent_summaries
                            else build_flat_resource(parents, max_chars_per_parent // 4))
    out["Add-child-only"] = build_add_child_only_resource(
        parents, verified_child_contracts, max_chars_per_parent)
    out["Unverified-factorized"] = build_unverified_resource(
        parents_rewritten_unverified or parents_rewritten_ours,
        unverified_child_contracts or verified_child_contracts,
        all_parents=parents,
        max_chars_per_parent=max_chars_per_parent,
    )
    out["Ours"] = build_ours_resource(parents_rewritten_ours,
                                          verified_child_contracts,
                                          all_parents=parents,
                                          max_chars_per_parent=max_chars_per_parent)
    return out
