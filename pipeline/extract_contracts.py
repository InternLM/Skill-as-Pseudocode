#!/usr/bin/env python3
"""
extract_contracts.py — Phase 8.5: extract typed callable contracts
from candidate clusters using an LLM with strict JSON schema.

For each candidate cluster, prompt the LLM with:
  - the cluster's member units (titles + text + linked scripts)
  - the parent-skill frontmatter (name + description)
  - any script signatures (argparse/click) extracted by the parser
  - a strict type ontology

Output (per candidate): a draft contract JSON with all required fields.
The verifier loop in the next stage gates final acceptance.

Output: results_anthropic/contracts_draft.json
"""
from __future__ import annotations
import os
import argparse, json, os, re, sys, time
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from common import load_json, save_json, Budget

ROOT = Path(__file__).parent
LIB = ROOT / "results_anthropic"


SYSTEM_PROMPT = """You extract a reusable child-skill contract from repeated
sub-procedures observed across multiple parent skills.

Return STRICT JSON matching the schema EXACTLY. Output only JSON, no prose.

JSON schema:
{
  "id": "<short identifier in lowercase-kebab-case>",
  "trigger": "<one-sentence: when to invoke this child skill>",
  "input_schema": {
    "<input_name>": {
      "type": "string|integer|number|boolean|file|json|table|image|document|list|enum|object",
      "required": true|false,
      "description": "<short description>"
    }
  },
  "output_schema": {
    "<output_name>": {
      "type": "string|integer|number|boolean|file|json|table|image|document|list|enum|object",
      "description": "<short description>"
    }
  },
  "preconditions": ["<condition 1>", "<condition 2>"],
  "postconditions": ["<condition 1>", "<condition 2>"],
  "resources": ["<path/to/script.py or similar>", ...],
  "side_effects": ["<filesystem|network|api|user_visible_output|none>", ...],
  "rationale": "<one-sentence explanation of why this is a coherent child skill>"
}

CONSTRAINTS:
  - Do NOT invent required inputs that have no evidence in the source units.
  - Every required input must be plausibly bindable in at least 2 of the
    parent skills (mention in source unit text or frontmatter).
  - The output_schema must reflect what the source units actually produce,
    or what the linked scripts return. If the cluster does not have a
    coherent shared output, return {"_extraction_failed": true,
    "_reason": "<short reason>"} instead of forcing a contract.
  - side_effects must be explicit. "none" is allowed only if no script,
    no file write, no network call is implied.
  - resources should list only paths that already appear in source units
    or are referenced via local_links.

If the cluster is too heterogeneous to support a single child contract,
return {"_extraction_failed": true, "_reason": "..."}."""


USER_TMPL = """A repeated sub-procedure has been mined across {n_parents}
parent skills. Below are the source units and the parent-skill metadata.

PARENTS:
{parents_block}

SCRIPT SIGNATURES (from static analysis):
{scripts_block}

SOURCE UNITS (all members of the candidate cluster):
{units_block}

Extract the typed child-skill contract as JSON, or return
{{"_extraction_failed": true, ...}} if the cluster is incoherent.
"""


def render_parents_block(parent_skills: list[dict]) -> str:
    L = []
    for p in parent_skills:
        L.append(f"- name: {p['name']}")
        L.append(f"  description: {(p['description'] or '')[:300]}")
    return "\n".join(L)


def render_scripts_block(parents_with_scripts: list[dict]) -> str:
    L = []
    for p in parents_with_scripts:
        for sc in p.get("scripts", []):
            sig = sc.get("signature", {})
            args = sig.get("args", []) if sig else []
            if not args: continue
            L.append(f"- {p['skill_id']}/{sc['path']} ({sc['type']}):")
            for a in args[:8]:
                req = "required" if a.get("required") else "optional"
                L.append(f"    {a['name']} ({req}, type={a.get('type')})  "
                          f"help={a.get('help')!r:.80}")
    return "\n".join(L) if L else "(no scripts with extracted argparse/click signatures)"


def render_units_block(member_units: list[dict], parents_by_id: dict) -> str:
    L = []
    for i, u in enumerate(member_units):
        parent = parents_by_id.get(u["skill_id"], {})
        # Get the actual unit body from parents.json
        unit_full = parent.get("procedural_units", [])[u["unit_index"]] \
                    if parent.get("procedural_units") else None
        body = unit_full["text"][:600] if unit_full else u.get("text_preview", "")
        L.append(f"  --- unit {i+1}: parent={u['skill_name']}, title={u['title']!r}")
        L.append(f"    text: {body[:600]}")
        if u.get("linked_scripts"):
            L.append(f"    linked_scripts: {u['linked_scripts']}")
    return "\n".join(L)


def extract_contract(client, model, budget, candidate, parents_by_id):
    distinct_skills = candidate["distinct_skills"]
    parent_skills = [parents_by_id[sid] for sid in distinct_skills if sid in parents_by_id]
    parents_block = render_parents_block(parent_skills)
    scripts_block = render_scripts_block(parent_skills)
    units_block   = render_units_block(candidate["members"], parents_by_id)

    user = USER_TMPL.format(n_parents=len(distinct_skills),
                              parents_block=parents_block,
                              scripts_block=scripts_block,
                              units_block=units_block[:8000])
    try:
        r = client.chat.completions.create(
            model=model, temperature=0.0, max_tokens=1500,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user",   "content": user}])
        budget.add(model, r.usage.prompt_tokens, r.usage.completion_tokens,
                    phase="contract_extract")
        try:
            return json.loads(r.choices[0].message.content)
        except json.JSONDecodeError as e:
            return {"_parse_error": str(e),
                    "_raw": r.choices[0].message.content[:500]}
    except Exception as e:
        return {"_api_error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key",  default=None)
    ap.add_argument("--model",    default="gpt-4o-mini")
    ap.add_argument("--budget-usd", type=float, default=1.0)
    ap.add_argument("--lib-dir", default=None)
    args = ap.parse_args()
    global LIB
    if args.lib_dir:
        LIB = ROOT / args.lib_dir

    from openai import OpenAI
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
    kwargs = {"api_key": api_key}
    if args.base_url: kwargs["base_url"] = args.base_url
    client = OpenAI(**kwargs)
    budget = Budget(args.budget_usd)

    candidates = load_json(LIB / "candidates.json")
    parents = load_json(LIB / "parents.json")
    parents_by_id = {p["skill_id"]: p for p in parents}

    drafts = []
    for c in candidates:
        if budget.tripped(): break
        t0 = time.time()
        contract = extract_contract(client, args.model, budget, c, parents_by_id)
        record = {
            "candidate_id":     c["candidate_id"],
            "n_units":          c["n_units"],
            "n_distinct_skills": c["n_distinct_skills"],
            "distinct_skills":  c["distinct_skills"],
            "contract":         contract,
            "extraction_time_s": round(time.time() - t0, 2),
        }
        drafts.append(record)
        save_json(drafts, LIB / "contracts_draft.json")
        if "_extraction_failed" in contract:
            print(f"  [{c['candidate_id']}] FAILED: {contract.get('_reason','')[:80]}")
        elif "_parse_error" in contract or "_api_error" in contract:
            print(f"  [{c['candidate_id']}] ERROR")
        else:
            cid = contract.get("id", "?")
            n_inputs = len(contract.get("input_schema", {}))
            n_outputs = len(contract.get("output_schema", {}))
            print(f"  [{c['candidate_id']}] id={cid}  inputs={n_inputs}  outputs={n_outputs}  "
                  f"({record['extraction_time_s']}s)  ${budget.spent:.4f}")

    print()
    print(f"drafted contracts: {len(drafts)}/{len(candidates)}; budget ${budget.spent:.4f}")


if __name__ == "__main__":
    main()
