#!/usr/bin/env python3
"""
negative_controls.py — generate negative-control clusters and
push them through the full Skill-as-Pseudocode pipeline.

Pipeline:
  1. Generate 3 classes of fake clusters (A cross-domain, B same-domain
     distinct, C near-miss) using sap.negative_controls.
  2. Save them as a candidates_negative.json.
  3. Run contract_extractor on these fake clusters (LLM).
  4. Run verifier_v2 (evidence + 3-tier decision) on them.
  5. Compute false-positive rate per class at the current policy.

Output:
  <lib>/negative_controls/candidates.json
  <lib>/negative_controls/contracts_draft.json
  <lib>/negative_controls/evidence_reports.json
  <lib>/negative_controls/fp_report.txt
  <lib>/negative_controls/fp_report.json

Usage:
  python3 negative_controls.py --lib-dir results_anthropic \
        --base-url ... --api-key ... [--n-per-class 10]

For multi-library negative controls (mix Anthropic + GoS in cross-domain):
  python3 negative_controls.py --lib-dir results_anthropic \
        --extra-libs results_gos
"""
from __future__ import annotations
import os
import argparse, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from common import load_json, save_json, Budget
from extract_contracts import (
    SYSTEM_PROMPT, USER_TMPL,
    render_units_block, render_parents_block, render_scripts_block,
)
from verify_primitives import embed_text
from sap_core.evidence import build_evidence_profile
from sap_core.decision import DecisionPolicy, decide, policy_from_dict
from sap_core.negative_controls import (
    generate_cross_domain, generate_same_domain_distinct, generate_near_miss,
    generate_swapped_contracts,
)


def extract_one_contract(client, model, budget, cand, parents_by_id):
    """Reuse the same extraction prompt as extract_contracts.py."""
    distinct_skills = cand["distinct_skills"]
    parent_skills = [parents_by_id[sid] for sid in distinct_skills
                       if sid in parents_by_id]
    parents_block = render_parents_block(parent_skills)
    scripts_block = render_scripts_block(parent_skills)
    units_block   = render_units_block(cand["members"], parents_by_id)
    user = USER_TMPL.format(
        n_parents=len(distinct_skills),
        parents_block=parents_block,
        scripts_block=scripts_block,
        units_block=units_block[:8000],
    )
    try:
        r = client.chat.completions.create(
            model=model, temperature=0.0, max_tokens=1500,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user",   "content": user}])
        budget.add(model, r.usage.prompt_tokens, r.usage.completion_tokens,
                    phase="neg_extract")
        try:
            return json.loads(r.choices[0].message.content)
        except json.JSONDecodeError:
            return {"_parse_error": True}
    except Exception as e:
        return {"_api_error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib-dir", required=True,
                     help="Primary library dir (negative controls saved here)")
    ap.add_argument("--extra-libs", nargs="*", default=[],
                     help="Extra library dirs to mix into cross-domain controls")
    ap.add_argument("--n-per-class", type=int, default=10)
    ap.add_argument("--members-per-cluster", type=int, default=3)
    ap.add_argument("--random-state", type=int, default=42)

    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--extract-model", default="gpt-4o-mini")
    ap.add_argument("--embed-model",   default="text-embedding-3-small")
    ap.add_argument("--budget-usd", type=float, default=2.0)
    ap.add_argument("--skip-llm", action="store_true",
                     help="Skip LLM extraction (deterministic-only fields). "
                          "Useful when API quota is depleted.")
    args = ap.parse_args()

    LIB = ROOT / args.lib_dir
    out = LIB / "negative_controls"
    out.mkdir(parents=True, exist_ok=True)

    # Load parents from the primary library + any extras (for cross-domain mix)
    primary_parents = load_json(LIB / "parents.json")
    all_parents = list(primary_parents)
    for extra in args.extra_libs:
        p = ROOT / extra / "parents.json"
        if p.exists():
            all_parents += load_json(p)
            print(f"  mixed in {extra}: +{len(load_json(p))} parents")

    parents_by_id = {p["skill_id"]: p for p in all_parents}

    # 1. Generate A/B/C
    print(f"\nGenerating negative controls ({args.n_per_class} per class)...")
    A = generate_cross_domain(all_parents,
                                  n_clusters=args.n_per_class,
                                  members_per_cluster=args.members_per_cluster,
                                  random_state=args.random_state)
    B = generate_same_domain_distinct(all_parents,
                                          n_clusters=args.n_per_class,
                                          members_per_cluster=args.members_per_cluster,
                                          random_state=args.random_state + 1)
    C = generate_near_miss(all_parents,
                              n_clusters=args.n_per_class,
                              members_per_cluster=args.members_per_cluster,
                              random_state=args.random_state + 2)
    print(f"  A (cross-domain):           {len(A)}")
    print(f"  B (same-domain distinct):   {len(B)}")
    print(f"  C (near-miss):              {len(C)}")

    # Class D: swapped contracts (real contract paired with wrong cluster).
    # This bypasses the LLM extractor and tests the verifier directly.
    D_swap = []
    if (LIB / "candidates.json").exists() and (LIB / "contracts_draft.json").exists():
        real_cands  = load_json(LIB / "candidates.json")
        real_drafts = load_json(LIB / "contracts_draft.json")
        D_swap = generate_swapped_contracts(real_cands, real_drafts,
                                                n_clusters=args.n_per_class,
                                                random_state=args.random_state + 3)
    D_candidates = [pair[0] for pair in D_swap]
    D_drafts     = [pair[1] for pair in D_swap]
    print(f"  D (swapped contract):       {len(D_candidates)}")

    fake_candidates = A + B + C + D_candidates
    save_json(fake_candidates, out / "candidates.json")
    print(f"  wrote {out / 'candidates.json'}")

    if args.skip_llm:
        print("\n--skip-llm specified, stopping after candidate generation.")
        return

    # 2. Extract contracts via LLM (gpt-4o-mini)
    from openai import OpenAI
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
    kwargs = {"api_key": api_key}
    if args.base_url: kwargs["base_url"] = args.base_url
    client = OpenAI(**kwargs)
    budget = Budget(args.budget_usd)

    drafts = []
    # Pre-populate Class D drafts (already have a swapped contract)
    D_drafts_by_id = {d["candidate_id"]: d for d in D_drafts}

    print(f"\nExtracting contracts via {args.extract_model}...")
    for c in fake_candidates:
        cid = c["candidate_id"]
        cls = c.get("_negative_control_class", "?")
        # Class D: contract already provided (swapped from real). Skip LLM.
        if cid in D_drafts_by_id:
            drafts.append(D_drafts_by_id[cid])
            print(f"  [{cid}] class={cls} SWAPPED_CONTRACT (no LLM call)")
            save_json(drafts, out / "contracts_draft.json")
            continue
        if budget.tripped(): break
        t0 = time.time()
        contract = extract_one_contract(client, args.extract_model, budget,
                                              c, parents_by_id)
        elapsed = time.time() - t0
        drafts.append({
            "candidate_id":      cid,
            "n_units":           c["n_units"],
            "n_distinct_skills": c["n_distinct_skills"],
            "distinct_skills":   c["distinct_skills"],
            "contract":          contract,
            "extraction_time_s": round(elapsed, 2),
        })
        ok  = ("contract" if isinstance(contract, dict)
               and not any(k.startswith("_") for k in contract.keys())
               else "EXT_FAIL")
        print(f"  [{cid}] class={cls} {ok} ({elapsed:.1f}s)")
        save_json(drafts, out / "contracts_draft.json")

    print(f"\nspent on extraction: ${budget.spent:.4f}")

    # 3. Run v2 verifier on the drafts
    print(f"\nRunning v2 verifier...")
    # Load policy from primary lib (or default)
    if (LIB / "policy.json").exists():
        policy = policy_from_dict(load_json(LIB / "policy.json"))
    else:
        policy = DecisionPolicy()

    cands_by_id = {c["candidate_id"]: c for c in fake_candidates}
    reports = []
    n_by_tier = {"auto_promote": 0, "review": 0, "reject": 0}
    n_by_class_tier = {}

    for d in drafts:
        contract = d["contract"]
        cand     = cands_by_id[d["candidate_id"]]
        cls      = cand.get("_negative_control_class", "?")
        member_units = cand["members"]

        # Embeddings (skip on extract-failed)
        unit_emb = None; contract_emb = None
        if isinstance(contract, dict) and not any(k.startswith("_") for k in contract.keys()):
            unit_texts = []
            for u in member_units:
                parent = parents_by_id.get(u["skill_id"], {})
                proc = parent.get("procedural_units", [])
                unit_full = proc[u["unit_index"]] if u["unit_index"] < len(proc) else None
                body = unit_full["text"] if unit_full else u.get("text_preview", "")
                unit_texts.append(f"{u['title']}: {body[:400]}")
            contract_text = (contract.get("trigger", "") + " " +
                              " ".join(contract.get("preconditions") or []) + " " +
                              " ".join(contract.get("postconditions") or []) + " " +
                              contract.get("rationale", ""))
            try:
                unit_emb = embed_text(client, args.embed_model, unit_texts, budget)
                contract_emb = embed_text(client, args.embed_model, [contract_text], budget)[0]
            except Exception:
                pass

        profile = build_evidence_profile(
            candidate=cand, contract=contract, member_units=member_units,
            parents_by_id=parents_by_id,
            contract_emb=contract_emb, unit_emb=unit_emb,
        )
        dec = decide(profile, policy)

        report = {
            "candidate_id":     cand["candidate_id"],
            "_neg_control_class": cls,
            "contract_id":      profile.get("contract_id"),
            "evidence_profile": {k: v for k, v in profile.items()
                                  if k not in ("coverage_detail", "binding_detail",
                                                "replacement_detail", "risk_detail")},
            "decision":         dec["decision"],
            "promotion_score":  dec["promotion_score"],
            "reasons":          dec["reasons"],
        }
        reports.append(report)
        n_by_tier[dec["decision"]] += 1
        n_by_class_tier.setdefault(cls, {"auto_promote": 0, "review": 0, "reject": 0})
        n_by_class_tier[cls][dec["decision"]] += 1

        score = dec["promotion_score"]
        cid = cand["candidate_id"]
        marker = ("✓✓" if dec["decision"] == "auto_promote"
                   else "?? " if dec["decision"] == "review" else "✗ ")
        print(f"  [{cid}] {cls} {marker} {dec['decision']}  score={score:.2f}")

    save_json(reports, out / "evidence_reports.json")

    # 4. FP report
    n_total = len(reports)
    fp_auto = n_by_tier["auto_promote"]
    fp_auto_or_review = n_by_tier["auto_promote"] + n_by_tier["review"]
    fp_rate_auto = fp_auto / max(n_total, 1)
    fp_rate_auto_or_review = fp_auto_or_review / max(n_total, 1)

    fp_summary = {
        "n_total": n_total,
        "n_by_tier": n_by_tier,
        "n_by_class_tier": n_by_class_tier,
        "fp_rate_auto": round(fp_rate_auto, 3),
        "fp_rate_auto_or_review": round(fp_rate_auto_or_review, 3),
        "policy": {k: getattr(policy, k) for k in policy.__dataclass_fields__.keys()},
    }
    save_json(fp_summary, out / "fp_report.json")

    L = ["=" * 80, "  Negative Controls — False Positive Report", "=" * 80, ""]
    L.append(f"  n_total: {n_total}")
    L.append(f"  by tier: auto={n_by_tier['auto_promote']}  "
              f"review={n_by_tier['review']}  reject={n_by_tier['reject']}")
    L.append("")
    L.append(f"  False-positive rate (auto_promote on negative):       "
              f"{fp_rate_auto*100:.1f}%  (target ≤ 5%)")
    L.append(f"  False-positive rate (auto OR review on negative):     "
              f"{fp_rate_auto_or_review*100:.1f}%")
    L.append("")
    L.append(f"  Per class:")
    for cls, counts in sorted(n_by_class_tier.items()):
        total = sum(counts.values())
        fp_a = counts["auto_promote"] / max(total, 1)
        L.append(f"    {cls:<30s}  auto={counts['auto_promote']:>2}  "
                  f"review={counts['review']:>2}  reject={counts['reject']:>2}  "
                  f"|  fp(auto)={fp_a*100:.0f}%  (n={total})")
    text = "\n".join(L)
    (out / "fp_report.txt").write_text(text)
    print()
    print(text)


if __name__ == "__main__":
    main()
