"""
rewrite.py — Parent rewrite + call-site detection.

For each promoted child skill, identify the procedural units in each
source parent that the child replaces ("call-sites"), then produce a
rewritten parent skeleton where those units are replaced with an
invoke() placeholder.

Call-site = (parent_id, unit_index, original_span_text). The span IS
the procedural-unit text — we operate at section level, not at
character level (per the user's Q1 decision).

CRITICAL: not every member unit in the cluster is necessarily a real
call-site. The proposer's single-linkage clustering chains units
together transitively (A~B, B~C, B~D → cluster {A,B,C,D} even if A
is far from D). The downstream verifier checks aggregate cluster
properties; individual units can still be spurious. We add a
per-unit binding filter that drops members whose unit text has no
evidence for the contract's required inputs.

Two pipelines:

  1. Deterministic skeleton (no LLM):
       For each call-site that survives the per-unit binding filter,
       replace the unit's text with a templated invoke() string
       referring to the child contract. Bindings and
       residual_parent_text fields are left as TODO placeholders that
       a downstream LLM step (see exp_call_site_llm.py) fills in.

  2. LLM-aware fill (lives in exp_call_site_llm.py):
       Calls gpt-4o-mini with (contract, parent_unit_text) to extract
       the specific bindings and any parent-specific residual text
       that must be preserved after replacement.
"""
from __future__ import annotations
import re
from copy import deepcopy
from typing import Optional

# Bring in helpers from the v1 verifier for per-unit binding check
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from verify_primitives import tokenize, STOP


# ── Helpers ────────────────────────────────────────────────────────────
PLACEHOLDER_BINDINGS = "{ /* TODO: bindings to be filled by LLM step */ }"
PLACEHOLDER_RESIDUAL = "/* TODO: parent-specific residual to be filled by LLM step */"


def render_invoke_call(contract_id: str, bindings: Optional[dict] = None) -> str:
    """Render an `invoke(child_id, {args})` string from a contract id +
    optional bindings dict. If bindings is None, use a TODO placeholder."""
    if bindings is None:
        return f"invoke({contract_id}, {PLACEHOLDER_BINDINGS})"
    args_inner = ", ".join(f"{k}={v!r}" for k, v in bindings.items())
    return f"invoke({contract_id}, {{ {args_inner} }})"


# ── Per-unit binding filter (deterministic) ───────────────────────────
def _expand_input_name_tokens(name: str) -> set[str]:
    """Expand an input name like `archive_file` into matching tokens.

    Compound names must be split so they can match running text. Same
    logic as v1 verifier.binding_check.
    """
    tokens = set(tokenize(name)) | {name.lower()}
    for part in re.split(r"[_\W]+", name.lower()):
        if part and len(part) >= 3 and part not in STOP:
            tokens.add(part)
    return tokens


def _unit_has_binding_evidence_for_required_inputs(
        contract: dict, unit_text: str, min_inputs_evidenced: int = 1,
        permissive: bool = False) -> bool:
    """Check whether THIS unit (not the whole cluster) has evidence
    connecting it to the contract.

    Strict mode (default): the unit text must contain the name tokens
    of ≥ `min_inputs_evidenced` of the contract's required inputs.
    A `input_file` input matches text containing "input file" / "file" /
    "input".

    Permissive mode (--permissive-filter): the unit ALSO passes if it
    contains the contract id or trigger's content tokens (e.g.
    `office-unpack-pack` matches a unit titled "Step 1: Unpack").
    This recovers false negatives but adds false positives — use only
    when you have an LLM-aware second pass.

    The strict mode is the default because it is better to drop a few
    real call-sites and let the LLM step (Phase 4.2) recover them
    than to silently let in spurious sites that pollute the rewritten
    parents.
    """
    unit_tokens = set(tokenize(unit_text))

    # Required-input evidence
    inputs = contract.get("input_schema") or {}
    required = [(name, spec) for name, spec in inputs.items()
                  if spec.get("required") in (True, "true", 1)]
    if not required:
        required = list(inputs.items())

    n_evidenced_inputs = 0
    for name, _spec in required:
        name_tokens = _expand_input_name_tokens(name)
        if name_tokens & unit_tokens:
            n_evidenced_inputs += 1

    if n_evidenced_inputs >= min_inputs_evidenced:
        return True

    if not permissive:
        return False

    # Permissive: contract id / trigger token fallback
    contract_id = contract.get("id", "")
    id_tokens = _expand_input_name_tokens(contract_id) if contract_id else set()
    trigger = (contract.get("trigger") or "").lower()
    trigger_tokens = set(tokenize(trigger)) - STOP
    op_tokens = (id_tokens | trigger_tokens) - STOP
    # Only "content-ful" tokens (length ≥ 4, exclude very common words)
    op_tokens = {t for t in op_tokens
                  if len(t) >= 4 and t not in STOP
                  and t not in {"file", "user", "document", "data", "code",
                                 "text", "content", "value", "name", "type",
                                 "object", "skill", "task"}}
    return bool(op_tokens & unit_tokens)


def filter_call_sites_by_unit_binding(
        call_sites: list[dict], promoted_records: list[dict],
        parents_by_id: dict, min_inputs_evidenced: int = 1,
        permissive: bool = False) -> tuple[list[dict], list[dict]]:
    """Drop call-sites where the unit text doesn't have evidence for
    any required input. Returns (kept, dropped) lists."""
    contracts: dict[str, dict] = {}
    for r in promoted_records:
        c = r.get("final_contract") or {}
        cid = c.get("id") or r.get("candidate_id")
        contracts[cid] = c

    kept = []; dropped = []
    for cs in call_sites:
        contract = contracts.get(cs["child_skill_id"], {})
        unit_text = cs.get("original_unit_text", "") + " " + cs.get("original_unit_title", "")
        if _unit_has_binding_evidence_for_required_inputs(
                contract, unit_text, min_inputs_evidenced, permissive):
            kept.append(cs)
        else:
            cs2 = dict(cs)
            cs2["_filter_reason"] = "no required input evidence in unit text"
            dropped.append(cs2)
    return kept, dropped


# ── Phase 4.1: Call-site detection ────────────────────────────────────
def detect_call_sites(promoted_records: list[dict],
                        candidates_by_id: dict,
                        parents_by_id: dict,
                        apply_unit_binding_filter: bool = True,
                        min_inputs_evidenced: int = 1,
                        permissive_filter: bool = False) -> tuple[list[dict], list[dict]]:
    """For each promoted child skill, list the (parent_id, unit_index)
    call-sites where it should be invoked.

    A 'call-site' corresponds to one procedural unit in one parent
    that contributed to the candidate cluster.

    Args:
      promoted_records: list of records each having
          {candidate_id, final_contract, final_profile, ...}
      candidates_by_id: candidates.json indexed by candidate_id
      parents_by_id: parents.json indexed by skill_id

    Returns:
      list of call_site dicts (without bindings / residual yet —
      those are filled by the LLM-aware step).
    """
    call_sites = []
    for rec in promoted_records:
        contract = rec.get("final_contract") or {}
        contract_id = contract.get("id") or rec.get("candidate_id")
        cand = candidates_by_id.get(rec.get("candidate_id"))
        if cand is None: continue

        for member in cand.get("members", []):
            parent_id = member.get("skill_id")
            unit_idx  = member.get("unit_index")
            parent = parents_by_id.get(parent_id)
            if parent is None: continue
            proc = parent.get("procedural_units") or []
            if unit_idx is None or unit_idx >= len(proc): continue
            unit = proc[unit_idx]

            call_sites.append({
                "child_skill_id":      contract_id,
                "candidate_id":        rec.get("candidate_id"),
                "parent_id":           parent_id,
                "unit_index":          unit_idx,
                "original_unit_title": unit.get("title", ""),
                "original_unit_text":  unit.get("text", ""),
                "replacement":         render_invoke_call(contract_id, None),
                "bindings":            None,
                "residual_parent_text": None,
                "verification":        None,
            })
    # Per-unit binding filter (drop spurious cluster members)
    if apply_unit_binding_filter:
        kept, dropped = filter_call_sites_by_unit_binding(
            call_sites, promoted_records, parents_by_id,
            min_inputs_evidenced=min_inputs_evidenced,
            permissive=permissive_filter,
        )
        return kept, dropped
    return call_sites, []


# ── Phase 4.3: Parent skeleton rewrite ────────────────────────────────
def rewrite_parent_skeletons(parents: list[dict],
                                call_sites: list[dict],
                                max_residual_chars: int = 120) -> list[dict]:
    """Generate rewritten parent skeletons.

    For each parent, walk its procedural_units and substitute units
    matching any call-site with a SHORT invoke() reference. Other
    units are preserved verbatim.

    Compression rules:
      - The rewritten unit shows the heading + a one-line invoke()
        reference + (optionally) a SHORT residual ≤ max_residual_chars.
      - The full bindings and residual are kept in the JSON call_site
        record for downstream tooling, NOT inlined into the markdown.
      - This makes the rewritten library substantially shorter than
        the original.

    Returns list of:
      {
        parent_id, original_text, rewritten_text,
        n_call_sites_in_parent, n_units_total, unit_map
      }
    """
    cs_index: dict[tuple[str, int], dict] = {}
    for cs in call_sites:
        key = (cs["parent_id"], cs["unit_index"])
        if key not in cs_index:
            cs_index[key] = cs

    out = []
    for p in parents:
        proc = p.get("procedural_units") or []
        unit_map = []
        original_pieces = []
        rewritten_pieces = []
        n_replaced = 0
        for i, u in enumerate(proc):
            unit_title = u.get("title", "")
            unit_text  = u.get("text", "")
            heading = "#" * u.get("level", 2) + " " + unit_title if unit_title else ""
            original_block = (heading + "\n" + unit_text).strip()
            original_pieces.append(original_block)

            key = (p["skill_id"], i)
            if key in cs_index:
                cs = cs_index[key]
                child_id = cs["child_skill_id"]
                # Compress invoke string. If LLM bindings (Phase 4.2) populated
                # concrete values, show them so the agent sees a call with
                # actual arguments — much more useful than placeholder names.
                bindings = cs.get("bindings") or {}
                if bindings:
                    # Heuristic: show values if they look like concrete data
                    # (short, non-placeholder strings), else just keys.
                    has_concrete = any(
                        isinstance(v, str) and v and not v.startswith("/* TODO")
                        and len(v) <= 80
                        for v in bindings.values()
                    )
                    if has_concrete:
                        arg_strs = []
                        for k, v in bindings.items():
                            if isinstance(v, str) and v.strip():
                                # Truncate long values
                                vs = v if len(v) <= 60 else v[:57] + "..."
                                # Use double quotes; escape inner
                                vs_esc = vs.replace('"', '\\"')
                                arg_strs.append(f'{k}="{vs_esc}"')
                            else:
                                arg_strs.append(f"{k}")
                        invoke_short = f"invoke({child_id}, {{{', '.join(arg_strs)}}})"
                    else:
                        keys = ", ".join(bindings.keys())
                        invoke_short = f"invoke({child_id}, {{{keys}}})"
                else:
                    invoke_short = f"invoke({child_id})"
                # Short residual (parent-specific extras only)
                residual_full = cs.get("residual_parent_text") or ""
                if residual_full:
                    residual_short = residual_full[:max_residual_chars].strip()
                    if len(residual_full) > max_residual_chars:
                        residual_short += "…"
                    residual_line = f"  (parent-specific: {residual_short})"
                else:
                    residual_line = ""
                block = f"{heading}\n{invoke_short}{residual_line}".strip()
                rewritten_pieces.append(block)
                unit_map.append((i, "invoke", invoke_short))
                n_replaced += 1
            else:
                rewritten_pieces.append(original_block)
                unit_map.append((i, "original", unit_text[:80]))

        out.append({
            "parent_id":              p["skill_id"],
            "name":                   p.get("name", p["skill_id"]),
            "original_text":          "\n\n".join(original_pieces),
            "rewritten_text":         "\n\n".join(rewritten_pieces),
            "n_call_sites_in_parent": n_replaced,
            "n_units_total":          len(proc),
            "unit_map":               unit_map,
        })
    return out


# ── Final refactored_library assembly ────────────────────────────────
def build_refactored_library(lib_name: str,
                                promoted_records: list[dict],
                                candidates_by_id: dict,
                                parents_by_id: dict,
                                parents: list[dict],
                                apply_unit_binding_filter: bool = True,
                                min_inputs_evidenced: int = 1,
                                permissive_filter: bool = False) -> dict:
    """End-to-end Phase 4.1 + 4.3 assembly.

    Produces the refactored_library.json content.
    """
    call_sites, dropped_sites = detect_call_sites(
        promoted_records, candidates_by_id, parents_by_id,
        apply_unit_binding_filter=apply_unit_binding_filter,
        min_inputs_evidenced=min_inputs_evidenced,
        permissive_filter=permissive_filter,
    )
    # Only rewrite parents that have at least one call-site
    parents_with_cs = sorted({cs["parent_id"] for cs in call_sites})
    parents_to_rewrite = [p for p in parents if p["skill_id"] in parents_with_cs]
    parents_rewritten = rewrite_parent_skeletons(parents_to_rewrite, call_sites)

    # Sanitize child contracts for the output
    child_skills = []
    for rec in promoted_records:
        contract = rec.get("final_contract") or {}
        # Stub: tier + score + n_parents
        prof = rec.get("final_profile") or {}
        child_skills.append({
            "child_skill_id": contract.get("id") or rec.get("candidate_id"),
            "candidate_id":   rec.get("candidate_id"),
            "tier":           rec.get("final_tier",
                                       rec.get("decision", "auto_promote")),
            "promotion_score": prof.get("promotion_score",
                                          prof.get("score", None)),
            "evidence_profile": {
                "coverage":         prof.get("coverage"),
                "binding_rate":     prof.get("binding_rate"),
                "replacement_rate": prof.get("replacement_rate"),
                "risk":             prof.get("risk"),
                "parents_covered":  prof.get("parents_covered"),
            },
            "contract":     contract,
        })

    return {
        "lib":                 lib_name,
        "n_promoted":          len(promoted_records),
        "n_call_sites":        len(call_sites),
        "n_call_sites_dropped": len(dropped_sites),
        "n_parents_rewritten": len(parents_rewritten),
        "child_skills":        child_skills,
        "call_sites":          call_sites,
        "call_sites_dropped":  dropped_sites,
        "parents_rewritten":   parents_rewritten,
    }
