"""
rewrite_llm_cleanup.py — Phase 5.5: LLM-aware cleanup pass on the
deterministic rewrite output.

PROBLEM IT FIXES
----------------
`rewrite_parent_skeletons` only replaces procedural units that appear
as members of a call-site (i.e. units the candidate cluster
identified). Other units in the same parent — Workflow, Example,
Bundled Resources, etc. — are kept verbatim.

When the original library contains content that CONFLICTS with the
child contract (e.g. GoS skills_500 alfworld-object-transporter
keeps `Action: put X in/on Y` in the Example block while the actual
ALFWorld env accepts only `move X to Y`), the conflicting text
survives the rewrite and misleads the agent.

THIS PASS
---------
For each parent that has at least one invoke() placeholder, pass:
  - the existing (deterministic) rewritten markdown
  - the list of children invoked, with their contract (id, trigger,
    inputs, outputs, postconditions)

to gpt-4o-mini with a strict prompt:

  Identify any remaining text in the rewrite that DESCRIBES the same
  operation as a child's contract. Replace such text with
  `invoke(child_id, args)`. Also remove or fix any examples that
  contradict the child's contract.

  Keep parent-specific text (text that does NOT correspond to any
  child contract). Output ONLY the cleaned markdown.

The LLM output is validated (must still contain at least one
`invoke(` reference per existing call-site child) before we overwrite
the rewritten_text.

Cost: one LLM call per rewritten parent (~301 parents on skills_500).
Avg input 3-5 kB, output 2-3 kB, gpt-4o-mini → ~$0.003/parent → total
<$1 on skills_500.
"""
from __future__ import annotations
import json, re
from typing import Optional


SYSTEM_PROMPT = """\
You are a skill-library refactoring assistant.

GOAL: clean up a markdown skill document that has been partially rewritten to invoke shared child sub-skills. The deterministic rewrite has already replaced some procedural units with `invoke(child_id, {...})` placeholders, but other parts of the document may still describe the same child operations in their own words — including examples, workflows, and resource lists. When the original text conflicts with the child contract (different syntax, wrong action verb, stale parameters), the conflict will mislead a downstream agent.

RULES (strict):
1. Identify any text in the document that describes an operation already covered by a child contract.
2. For such text, replace it with `invoke(child_id, args_inferred_from_text)`.
3. KEEP parent-specific text (text that does not correspond to any child contract).
4. If an example or workflow line contradicts a child contract (different verb, different parameter format), REMOVE or REWRITE that line.
5. DO NOT introduce new content that isn't in the original. You may shorten, but not invent.
6. DO NOT change `invoke(child_id, ...)` lines that the deterministic pass already inserted.
7. Output ONLY the cleaned markdown body. No commentary, no code-fence wrappers.

The cleaned document must still reference each invoked child at least once."""


USER_TMPL = """\
PARENT SKILL: {parent_id} ({parent_name})

CHILD CONTRACTS INVOKED IN THIS PARENT:
{children_block}

DETERMINISTIC REWRITE TO CLEAN UP:
---
{rewritten_text}
---

Output the cleaned markdown body only.
"""


def _render_children_block(children: list[dict]) -> str:
    """children: list of {child_skill_id, contract: {id, trigger, input_schema,
    output_schema, preconditions, postconditions}}.
    """
    lines = []
    for ch in children:
        contract = ch.get("contract") or ch
        cid = ch.get("child_skill_id") or contract.get("id", "?")
        trigger = contract.get("trigger", "") or ""
        inputs = contract.get("input_schema") or {}
        outputs = contract.get("output_schema") or {}
        post = contract.get("postconditions") or []
        lines.append(f"\n- child_id: `{cid}`")
        lines.append(f"  trigger: {trigger[:200]}")
        if inputs and isinstance(inputs, dict):
            inp_keys = list(inputs.get("properties", inputs).keys()) if isinstance(
                inputs.get("properties", inputs), dict) else []
            if inp_keys:
                lines.append(f"  inputs: {{ {', '.join(inp_keys)} }}")
        if outputs and isinstance(outputs, dict):
            out_keys = list(outputs.get("properties", outputs).keys()) if isinstance(
                outputs.get("properties", outputs), dict) else []
            if out_keys:
                lines.append(f"  outputs: {{ {', '.join(out_keys)} }}")
        if post:
            lines.append(f"  postconditions: {'; '.join(str(p)[:80] for p in post[:3])}")
    return "\n".join(lines) or "(none)"


def cleanup_one_parent(client, model: str, budget,
                         parent_id: str, parent_name: str,
                         rewritten_text: str,
                         children_invoked: list[dict],
                         max_chars: int = 12000) -> dict:
    """Return {success: bool, cleaned_text: str, error?: str}."""
    # Skip if text is too short — likely nothing to clean
    if len(rewritten_text.strip()) < 200:
        return {"success": True, "cleaned_text": rewritten_text, "skipped": True}
    if not children_invoked:
        return {"success": True, "cleaned_text": rewritten_text, "skipped": True}

    user = USER_TMPL.format(
        parent_id=parent_id,
        parent_name=parent_name,
        children_block=_render_children_block(children_invoked),
        rewritten_text=rewritten_text[:max_chars],
    )

    try:
        r = client.chat.completions.create(
            model=model,
            temperature=0.0,
            max_tokens=4096,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
        )
        if budget is not None and hasattr(r, "usage"):
            budget.add(model, r.usage.prompt_tokens, r.usage.completion_tokens,
                        phase="parent_rewrite_cleanup")
        cleaned = r.choices[0].message.content or ""
        cleaned = cleaned.strip()
        # Strip any accidental code-fence wrapping
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)
            cleaned = cleaned.strip()
    except Exception as e:
        return {"success": False, "error": str(e), "cleaned_text": rewritten_text}

    # Sanity: cleaned must still mention each child at least once.
    child_ids = [ch.get("child_skill_id") or ch.get("contract", {}).get("id")
                  for ch in children_invoked]
    child_ids = [cid for cid in child_ids if cid]
    missing = [cid for cid in child_ids if f"invoke({cid}" not in cleaned and cid not in cleaned]
    if missing:
        # LLM dropped some child references — reject the cleanup
        return {
            "success": False,
            "error": f"cleaned text dropped invoke references for: {missing}",
            "cleaned_text": rewritten_text,
        }
    # Sanity: cleaned must not be empty / drastically shorter
    if len(cleaned) < max(len(rewritten_text) * 0.3, 100):
        return {
            "success": False,
            "error": f"cleaned text too short ({len(cleaned)} vs original {len(rewritten_text)})",
            "cleaned_text": rewritten_text,
        }
    return {"success": True, "cleaned_text": cleaned}


def run_cleanup_pass(client, model: str, budget,
                      parents_rewritten: list[dict],
                      call_sites: list[dict],
                      child_skills: list[dict],
                      verbose: bool = True) -> dict:
    """For each rewritten parent, run cleanup_one_parent. Return stats +
    in-place updates parents_rewritten[i].rewritten_text on success.
    """
    # Map child_skill_id → full child record (for trigger / inputs)
    child_by_id = {}
    for ch in child_skills:
        contract = ch.get("contract") or {}
        cid = ch.get("child_skill_id") or contract.get("id")
        if cid:
            child_by_id[cid] = ch

    # Map parent_id → list of invoked child records
    children_per_parent: dict[str, list[dict]] = {}
    for cs in call_sites:
        pid = cs.get("parent_id")
        cid = cs.get("child_skill_id")
        if pid and cid and cid in child_by_id:
            children_per_parent.setdefault(pid, [])
            if child_by_id[cid] not in children_per_parent[pid]:
                children_per_parent[pid].append(child_by_id[cid])

    stats = {"n_total": 0, "n_cleaned": 0, "n_skipped": 0, "n_failed": 0,
              "failures": []}
    for pr in parents_rewritten:
        pid = pr["parent_id"]
        children = children_per_parent.get(pid, [])
        stats["n_total"] += 1
        result = cleanup_one_parent(
            client, model, budget,
            parent_id=pid,
            parent_name=pr.get("name", pid),
            rewritten_text=pr.get("rewritten_text", ""),
            children_invoked=children,
        )
        if result.get("skipped"):
            stats["n_skipped"] += 1
            continue
        if result["success"]:
            pr["rewritten_text"] = result["cleaned_text"]
            pr["cleanup_applied"] = True
            stats["n_cleaned"] += 1
        else:
            pr["cleanup_applied"] = False
            stats["n_failed"] += 1
            stats["failures"].append({"parent_id": pid, "error": result.get("error")})
        if verbose and stats["n_total"] % 20 == 0:
            print(f"  cleanup progress: {stats['n_total']}/{len(parents_rewritten)} "
                   f"cleaned={stats['n_cleaned']} skipped={stats['n_skipped']} "
                   f"failed={stats['n_failed']}")

    return stats
