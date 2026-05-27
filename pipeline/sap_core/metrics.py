"""
metrics.py — Deterministic evaluation metrics for the main experiment.

All metrics are computable without LLM (per user's preference for
deterministic main metrics; LLM-judge is reserved as a secondary
quality probe in metrics_judge.py).

Metrics:

  1. Task success rate (primary):
       Each task has a gold artifact set — required strings, patterns,
       or schema field names that must appear in the agent's output.
       Success = recall ≥ task.success_threshold (default 0.7).

  2. Schema adherence:
       For tasks where the gold target is a typed contract output,
       check whether the agent's output contains each field declared
       in output_schema with non-empty content.

  3. Required-field F1:
       Per-task F1 of (gold required fields) vs (fields the agent
       actually produced).

  4. Token efficiency:
       prompt_tokens, completion_tokens, total_tokens per task.

  5. Invocation correctness (NEW):
       For factored systems (Ours, unverified, add-child-only),
       check whether the agent's output references `invoke(child, ...)`
       for the EXPECTED child contract, and whether the bindings are
       plausible (= contain values mentioned in the user prompt).

Task suite schema (see task_suite_anthropic.json):

  {
    "task_id": str,
    "user_prompt": str,
    "intended_child_skill": str,    # which child the agent should invoke
    "gold_artifacts": [
      {"type": "string"|"regex"|"contract_field",
       "value": "...",
       "weight": float | 1.0,
       "required": bool}
    ],
    "success_threshold": float | 0.7,
    "notes": str
  }
"""
from __future__ import annotations
import re
from typing import Optional


# ── Match primitives ────────────────────────────────────────────────────
def _norm(s: str) -> str:
    """Lowercase + collapse whitespace for fuzzy matching."""
    return re.sub(r"\s+", " ", s.lower()).strip()


def _match_artifact(art: dict, response_text: str) -> bool:
    """Check one gold artifact against the response."""
    kind = art.get("type", "string")
    value = art.get("value", "")
    if not value: return False
    rt = _norm(response_text)
    if kind == "string":
        return _norm(value) in rt
    if kind == "regex":
        try:
            return bool(re.search(value, response_text, re.IGNORECASE | re.DOTALL))
        except re.error:
            return False
    if kind == "contract_field":
        # Match the contract field name appearing as a label/key
        # e.g. "movement_name" → "movement_name:" or "**Movement Name**"
        norm_val = value.replace("_", "[ _]").replace("-", "[-_ ]")
        try:
            pat = re.compile(rf"\b{norm_val}\b", re.IGNORECASE)
            return bool(pat.search(response_text))
        except re.error:
            return False
    if kind == "contract_field_with_content":
        # Field name appears AND has non-empty content after it
        norm_val = value.replace("_", "[ _]").replace("-", "[-_ ]")
        try:
            pat = re.compile(rf"\b{norm_val}\b\s*[:=]\s*([^\n]{{3,}})",
                              re.IGNORECASE)
            return bool(pat.search(response_text))
        except re.error:
            return False
    return False


# ── 1. Task success rate ────────────────────────────────────────────────
def compute_task_success(task: dict, response_text: str,
                            include_schema_artifacts: bool = False) -> dict:
    """For a single task, compute recall over gold artifacts + success flag.

    By default, this is the FAIR content-only metric: contract_field
    artifacts (which require literal schema labels in the response) are
    EXCLUDED from the success calculation, so baselines without
    contract scaffolding aren't penalised for not using field labels.

    Schema adherence is computed separately by compute_schema_adherence.

    If `include_schema_artifacts=True`, the legacy strict metric is used
    that includes contract_field as required.
    """
    artifacts = task.get("gold_artifacts", [])
    if not artifacts:
        return {"recall": 1.0, "success": True, "matched": [], "missed": []}

    # Partition by kind for the fair metric
    content_artifacts = [a for a in artifacts
                          if a.get("type") not in ("contract_field",
                                                     "contract_field_with_content")]
    schema_artifacts  = [a for a in artifacts
                          if a.get("type") in ("contract_field",
                                                 "contract_field_with_content")]
    scored_artifacts = artifacts if include_schema_artifacts else content_artifacts

    matched = []; missed = []
    for art in scored_artifacts:
        if _match_artifact(art, response_text):
            matched.append(art)
        else:
            missed.append(art)

    # Weighted recall over required artifacts (within the scored subset)
    req = [a for a in scored_artifacts if a.get("required", True)]
    if req:
        n_match_req = sum(1 for a in req if a in matched)
        recall = n_match_req / len(req)
    elif scored_artifacts:
        recall = len(matched) / len(scored_artifacts)
    else:
        # No content artifacts at all — fall back to schema (no penalty)
        recall = 1.0

    threshold = task.get("success_threshold", 0.7)
    return {
        "recall":      round(recall, 3),
        "success":     recall >= threshold,
        "n_artifacts": len(scored_artifacts),
        "n_required":  len(req),
        "n_matched":   len(matched),
        "matched_values": [a.get("value") for a in matched],
        "missed_values":  [a.get("value") for a in missed],
        "n_schema_artifacts_excluded": len(schema_artifacts),
    }


# ── 2. Schema adherence ────────────────────────────────────────────────
def compute_schema_adherence(intended_contract: dict,
                                response_text: str) -> dict:
    """Check whether the response covers each output_schema field.

    Returns:
      {
        "n_fields_total": int,
        "n_fields_present": int,
        "field_coverage_rate": float,
        "missing_fields": [str, ...]
      }
    """
    output_schema = (intended_contract or {}).get("output_schema") or {}
    if not output_schema:
        return {"n_fields_total": 0, "n_fields_present": 0,
                "field_coverage_rate": 1.0, "missing_fields": []}
    field_names = list(output_schema.keys())
    present = []; missing = []
    for f in field_names:
        art = {"type": "contract_field", "value": f}
        if _match_artifact(art, response_text):
            present.append(f)
        else:
            missing.append(f)
    return {
        "n_fields_total":      len(field_names),
        "n_fields_present":    len(present),
        "field_coverage_rate": round(len(present) / max(len(field_names), 1), 3),
        "present_fields":      present,
        "missing_fields":      missing,
    }


# ── 3. Required-field F1 ────────────────────────────────────────────────
def compute_required_field_f1(intended_contract: dict,
                                 response_text: str) -> dict:
    """Per-task F1 on REQUIRED input fields (the ones the agent should
    surface to the user / pipeline)."""
    input_schema = (intended_contract or {}).get("input_schema") or {}
    required_inputs = [name for name, spec in input_schema.items()
                          if spec.get("required") in (True, "true", 1)]
    if not required_inputs:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0,
                "n_required": 0, "n_found": 0}
    found = []
    for name in required_inputs:
        art = {"type": "contract_field_with_content", "value": name}
        if _match_artifact(art, response_text):
            found.append(name)
    # Precision: of fields the agent produced with values, how many were required?
    # (For now, we don't list "extra" fields, so precision = recall trivially.
    # When LLM step provides structured output, precision becomes meaningful.)
    recall    = len(found) / max(len(required_inputs), 1)
    precision = recall  # placeholder until structured output is available
    if precision + recall < 1e-9: f1 = 0.0
    else: f1 = 2 * precision * recall / (precision + recall)
    return {"precision": round(precision, 3),
            "recall":    round(recall, 3),
            "f1":        round(f1, 3),
            "n_required": len(required_inputs),
            "n_found":    len(found),
            "found_fields":   found,
            "missing_fields": [n for n in required_inputs if n not in found]}


# ── 4. Token efficiency ─────────────────────────────────────────────────
def compute_token_efficiency(prompt_tokens: int, completion_tokens: int) -> dict:
    return {
        "prompt_tokens":     int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "total_tokens":      int(prompt_tokens + completion_tokens),
    }


# ── 5. Invocation correctness ──────────────────────────────────────────
INVOKE_RE = re.compile(r"invoke\(\s*([\w\-]+)\s*[,\)]", re.IGNORECASE)


def compute_invocation_correctness(task: dict, response_text: str,
                                       intended_contract: dict = None) -> dict:
    """For factored systems, check whether the agent called the right
    child skill with plausible bindings."""
    intended = task.get("intended_child_skill") or ""
    invocations = INVOKE_RE.findall(response_text)
    n_invoke = len(invocations)
    intended_called = (intended.lower() in [i.lower() for i in invocations])

    # Plausible binding: do the bindings reference content from the user
    # prompt?
    prompt_tokens = set(re.findall(r"\w+", task.get("user_prompt", "").lower()))
    binding_overlap = None
    if intended_called and intended_contract:
        # Find the invoke() block for the intended child
        bind_re = re.compile(
            rf"invoke\(\s*{re.escape(intended)}\s*,\s*\{{(.+?)\}}\s*\)",
            re.IGNORECASE | re.DOTALL,
        )
        m = bind_re.search(response_text)
        if m:
            bindings_blob = m.group(1)
            blob_tokens = set(re.findall(r"\w+", bindings_blob.lower()))
            overlap = len(prompt_tokens & blob_tokens) / max(len(prompt_tokens), 1)
            binding_overlap = round(overlap, 3)
    return {
        "n_invokes":              n_invoke,
        "intended_invoked":       intended_called,
        "invocations":            invocations,
        "binding_prompt_overlap": binding_overlap,
    }


# ── Aggregate over a system's runs ────────────────────────────────────
def aggregate_run_metrics(run_results: list[dict]) -> dict:
    """Aggregate per-task metrics into a single summary."""
    if not run_results: return {}
    n = len(run_results)
    def avg(f):
        vals = [f(r) for r in run_results if f(r) is not None]
        return sum(vals) / max(len(vals), 1) if vals else 0.0
    return {
        "n_tasks":              n,
        "success_rate":         round(avg(lambda r: float(r["task_success"]["success"])), 3),
        "avg_recall":           round(avg(lambda r: r["task_success"]["recall"]), 3),
        "avg_field_coverage":   round(avg(lambda r: r.get("schema_adherence", {}).get("field_coverage_rate", 0)), 3),
        "avg_required_f1":      round(avg(lambda r: r.get("required_field_f1", {}).get("f1", 0)), 3),
        "avg_prompt_tokens":    round(avg(lambda r: r.get("token_efficiency", {}).get("prompt_tokens", 0)), 1),
        "avg_completion_tokens":round(avg(lambda r: r.get("token_efficiency", {}).get("completion_tokens", 0)), 1),
        "avg_total_tokens":     round(avg(lambda r: r.get("token_efficiency", {}).get("total_tokens", 0)), 1),
        "avg_intended_invoked": round(avg(lambda r: float(r.get("invocation", {}).get("intended_invoked", 0))), 3),
        "avg_binding_overlap":  round(avg(lambda r: r.get("invocation", {}).get("binding_prompt_overlap")), 3),
    }
