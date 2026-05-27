"""
bindings_pass.py — Phase 4.2: LLM-aware bindings + residual extraction.

For each candidate call-site detected by Phase 4.1 (deterministic), ask
gpt-4o-mini whether the unit is genuinely a call of the child skill and,
if yes, extract per-input bindings + parent-specific residual text.

LLM output (STRICT JSON):
  {
    "should_invoke":     true | false,
    "confidence":        "high" | "medium" | "low",
    "bindings":          {input_name: "source_substring", ...},
    "residual_parent_text": "<text not covered by child>",
    "rationale":         "<one-sentence>"
  }

Deterministic post-checks:
  - All required inputs must have a non-empty binding.
  - Binding value must overlap (token level) with the unit text.
If checks fail, drop the call-site.

Phase 4.2 runs between detect_call_sites (4.1) and
rewrite_parent_skeletons (4.3). Bindings get passed into the rewrite so
each `invoke(child_id, {...})` carries concrete values rather than
placeholder field names.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

# Make sure verifier helpers are importable
sys.path.insert(0, str(Path(__file__).parent.parent))
from verify_primitives import tokenize, STOP


LLM_SYSTEM = """You decide whether a procedural unit (a markdown section
from a parent skill) should be refactored into a call to a child skill,
and if so, extract the precise input bindings.

Output STRICT JSON only, no other text. Schema:
{
  "should_invoke":     boolean,
  "confidence":        "high" | "medium" | "low",
  "bindings":          {input_name: "<source_substring_or_paraphrase>"},
  "residual_parent_text": "<text that the child does NOT cover and must remain in the parent>",
  "rationale":         "<one-sentence explanation>"
}

Decision rules:
  - should_invoke = true iff the unit is genuinely an instance of the
    child skill's procedure, NOT just a section that happens to use
    similar vocabulary.
  - For every REQUIRED input in the contract, you MUST provide a
    binding value (a substring or close paraphrase of unit text).
    If you cannot find a binding for a required input → should_invoke = false.
  - For optional inputs you may give null.
  - residual_parent_text is anything in the unit that the child does
    NOT abstract over (e.g. parent-specific file paths, parameters,
    constraints). Keep it if present, otherwise return "".
  - confidence = high if the unit clearly invokes the child; medium
    if plausible but vocabulary mismatches; low if you are guessing.

Forbidden:
  - Inventing bindings that have no textual basis in the unit.
  - Returning should_invoke=true with missing required bindings.
"""


LLM_USER_TMPL = """CHILD SKILL CONTRACT:
{contract_json}

PARENT SKILL: {parent_name}
PROCEDURAL UNIT (unit_index={unit_index}):
title: {unit_title}
text:
{unit_text}

Decide. Return the JSON object only."""


def _llm_decide(client, model, budget, contract, parent_name,
                  unit_title, unit_text, unit_index):
    user = LLM_USER_TMPL.format(
        contract_json=json.dumps(contract, indent=2, ensure_ascii=False)[:4000],
        parent_name=parent_name,
        unit_index=unit_index,
        unit_title=unit_title,
        unit_text=unit_text[:2000],
    )
    try:
        r = client.chat.completions.create(
            model=model, temperature=0.0, max_tokens=800,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": LLM_SYSTEM},
                      {"role": "user", "content": user}])
        if budget is not None and hasattr(r, "usage"):
            budget.add(model, r.usage.prompt_tokens, r.usage.completion_tokens,
                        phase="bindings_pass")
        try:
            return json.loads(r.choices[0].message.content)
        except json.JSONDecodeError:
            return {"_parse_error": True}
    except Exception as e:
        return {"_api_error": str(e)}


def _check(decision: dict, contract: dict, unit_text: str) -> tuple[bool, list]:
    reasons = []
    if not isinstance(decision, dict):
        return False, ["non-dict response"]
    if "_api_error" in decision: return False, [f"_api_error: {decision['_api_error']}"]
    if "_parse_error" in decision: return False, ["_parse_error"]
    if not decision.get("should_invoke"):
        return False, [f"LLM should_invoke=False"]

    bindings = decision.get("bindings") or {}
    inputs = contract.get("input_schema") or {}
    # Handle both flat dict and JSON-schema {properties: ...} shapes
    if "properties" in inputs and isinstance(inputs["properties"], dict):
        inputs = inputs["properties"]
    required = [(name, spec) for name, spec in inputs.items()
                  if isinstance(spec, dict) and spec.get("required") in (True, "true", 1)]
    if not required:
        # Treat all as required if none marked
        required = list(inputs.items())

    unit_tokens = set(tokenize(unit_text.lower()))
    for name, _spec in required:
        b = bindings.get(name)
        if b is None or (isinstance(b, str) and not b.strip()):
            reasons.append(f"missing binding for '{name}'")
            continue
        b_tokens = set(tokenize(str(b).lower())) - STOP
        if not (b_tokens & unit_tokens):
            reasons.append(f"binding '{b}' for '{name}' has no token overlap")
    return (len(reasons) == 0), reasons


def run_bindings_pass(client, model, budget,
                       call_sites: list[dict],
                       child_skills: list[dict],
                       parents_by_id: dict,
                       include_dropped_call_sites: list[dict] = None,
                       verbose: bool = True) -> tuple[list[dict], list[dict], list[dict]]:
    """Run Phase 4.2 over the given call_sites (and optionally
    dropped_call_sites, to try to recover false negatives).

    Returns (kept_sites_with_bindings, dropped_sites, decisions_log).
    """
    contracts_by_child_id = {}
    for c in child_skills:
        cid = c.get("child_skill_id") or c.get("contract", {}).get("id")
        if cid:
            contracts_by_child_id[cid] = c.get("contract", {})

    parent_name_by_id = {pid: p.get("name", pid) for pid, p in parents_by_id.items()}

    sites_to_check = list(call_sites)
    if include_dropped_call_sites:
        for cs in include_dropped_call_sites:
            sites_to_check.append({**cs, "_was_dropped": True})

    kept_sites = []
    decisions_log = []
    n_confirmed = n_recovered = n_overruled = 0

    for i, cs in enumerate(sites_to_check):
        contract = contracts_by_child_id.get(cs["child_skill_id"], {})
        if not contract:
            if verbose:
                print(f"  skip {cs['child_skill_id']}: no contract")
            continue
        parent_name = parent_name_by_id.get(cs["parent_id"], cs["parent_id"])
        decision = _llm_decide(
            client, model, budget, contract, parent_name,
            cs["original_unit_title"], cs["original_unit_text"], cs["unit_index"],
        )
        ok, reasons = _check(decision, contract, cs["original_unit_text"])
        was_dropped = cs.get("_was_dropped", False)
        decisions_log.append({
            "child_skill_id": cs["child_skill_id"],
            "parent_id":      cs["parent_id"],
            "unit_index":     cs["unit_index"],
            "was_dropped_by_strict_filter": was_dropped,
            "llm_decision":   decision,
            "deterministic_check": {"ok": ok, "reasons": reasons},
        })

        if ok:
            new_cs = dict(cs)
            new_cs["bindings"] = decision.get("bindings") or {}
            new_cs["residual_parent_text"] = decision.get("residual_parent_text") or ""
            new_cs["verification"] = {
                "binding": "pass",
                "confidence": decision.get("confidence", "low"),
            }
            new_cs.pop("_was_dropped", None)
            kept_sites.append(new_cs)
            if was_dropped:
                n_recovered += 1
            else:
                n_confirmed += 1
        else:
            if not was_dropped:
                n_overruled += 1

        if verbose and (i + 1) % 20 == 0:
            print(f"  bindings progress: {i+1}/{len(sites_to_check)} "
                   f"confirmed={n_confirmed} recovered={n_recovered} overruled={n_overruled}")

    final_dropped = [cs for cs in call_sites
                      if not any(k["child_skill_id"] == cs["child_skill_id"] and
                                  k["parent_id"] == cs["parent_id"] and
                                  k["unit_index"] == cs["unit_index"]
                                  for k in kept_sites)]

    print(f"  bindings pass done: confirmed={n_confirmed} "
           f"recovered={n_recovered} overruled={n_overruled} "
           f"final_kept={len(kept_sites)}")
    return kept_sites, final_dropped, decisions_log
