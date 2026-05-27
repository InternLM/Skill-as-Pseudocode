#!/usr/bin/env python3
"""
refactor.py — Build refactored_library.json from
promoted children + parent SKILL.md.

Stages:
  Phase 4.1: detect call-sites
  Phase 4.3: rewrite parent skeletons with invoke() placeholders

This stage is fully deterministic (no LLM). The placeholders for
bindings + residual_parent_text are TODO markers that the
LLM-aware step (exp_call_site_llm.py, Phase 4.2) fills in.

Inputs:
  <lib>/contracts_accepted_v2.json  (auto_promote tier, from skip_repair_v2)
  <lib>/contracts_review_v2.json    (optional, --include-review)
  <lib>/candidates.json
  <lib>/parents.json

Outputs:
  <lib>/refactored/refactored_library.json
  <lib>/refactored/parent_<skill_id>.rewritten.md  (one per parent)
  <lib>/refactored/refactored_summary.txt

Usage:
  python3 refactor.py --lib-dir results_anthropic
  python3 refactor.py --lib-dir results_anthropic --include-review
  python3 refactor.py --lib-dir results_gos --include-review
"""
from __future__ import annotations
import os
import argparse, json, os, sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from common import load_json, save_json, Budget
from sap_core.rewrite import build_refactored_library, rewrite_parent_skeletons
from sap_core.rewrite_llm_cleanup import run_cleanup_pass
from sap_core.bindings_pass import run_bindings_pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib-dir", required=True)
    ap.add_argument("--include-review", action="store_true",
                     help="include review-tier children, not just auto_promote")
    ap.add_argument("--no-unit-binding-filter", action="store_true",
                     help="disable the per-unit binding filter (debug only). "
                          "By default we filter out call-sites whose unit "
                          "text has no required-input evidence.")
    ap.add_argument("--min-inputs-evidenced", type=int, default=1,
                     help="minimum number of contract required inputs whose "
                          "name tokens must appear in the unit's text for it "
                          "to be kept as a call-site (default 1)")
    ap.add_argument("--permissive-filter", action="store_true",
                     help="also keep call-sites whose unit text contains "
                          "contract-id / trigger tokens, even without "
                          "input-name evidence. False negatives go down, "
                          "false positives go up. Use only when followed "
                          "by an LLM-aware filter.")
    # LLM-aware bindings (Phase 4.2)
    ap.add_argument("--llm-bindings", action="store_true", default=False,
                     help="run LLM-aware bindings pass (Phase 4.2) to extract "
                          "per-input bindings + residual + filter spurious "
                          "call-sites. Adds ~$0.5/100 call-sites (gpt-4o-mini).")
    ap.add_argument("--bindings-model", type=str, default="gpt-4o-mini",
                     help="model for LLM bindings pass (default gpt-4o-mini)")
    # LLM-aware cleanup (Phase 5.5)
    ap.add_argument("--llm-cleanup", action="store_true", default=True,
                     help="run LLM-aware cleanup pass on each rewritten parent "
                          "to remove residual content that conflicts with child "
                          "contracts (e.g. buggy examples). Default ON. Use "
                          "--no-llm-cleanup to disable.")
    ap.add_argument("--no-llm-cleanup", dest="llm_cleanup",
                     action="store_false",
                     help="disable LLM-aware cleanup pass")
    ap.add_argument("--cleanup-model", type=str, default="gpt-4o-mini",
                     help="model for LLM cleanup pass (default gpt-4o-mini)")
    args = ap.parse_args()
    LIB = ROOT / args.lib_dir
    OUT = LIB / "refactored"
    OUT.mkdir(parents=True, exist_ok=True)

    # Load promoted children
    if not (LIB / "contracts_accepted_v2.json").exists():
        print(f"ERROR: {LIB / 'contracts_accepted_v2.json'} not found. "
               f"Run skip_repair.py or exp_repair_v2.py first.")
        sys.exit(1)

    promoted = load_json(LIB / "contracts_accepted_v2.json")
    n_auto = len(promoted)
    n_review = 0
    if args.include_review and (LIB / "contracts_review_v2.json").exists():
        review = load_json(LIB / "contracts_review_v2.json")
        promoted = promoted + review
        n_review = len(review)

    print(f"Promoted children: {len(promoted)} "
          f"({n_auto} auto + {n_review} review)")

    candidates = load_json(LIB / "candidates.json")
    parents    = load_json(LIB / "parents.json")
    cand_by_id = {c["candidate_id"]: c for c in candidates}
    parents_by_id = {p["skill_id"]: p for p in parents}

    # Build refactored library (Phase 4.1 detect + 4.3 deterministic rewrite)
    lib_data = build_refactored_library(
        lib_name=LIB.name,
        promoted_records=promoted,
        candidates_by_id=cand_by_id,
        parents_by_id=parents_by_id,
        parents=parents,
        apply_unit_binding_filter=not args.no_unit_binding_filter,
        min_inputs_evidenced=args.min_inputs_evidenced,
        permissive_filter=args.permissive_filter,
    )

    # Phase 4.2: LLM-aware bindings pass (optional)
    # Refines call-sites: drops spurious, adds concrete bindings + residual.
    # Must run BEFORE Phase 5.5 (cleanup operates on rewritten_text).
    bindings_stats = None
    if args.llm_bindings and lib_data["call_sites"]:
        print()
        print("Phase 4.2: LLM-aware bindings pass on call-sites...")
        try:
            from openai import OpenAI
        except ImportError:
            print("  WARN: openai package not installed; skipping bindings pass")
        else:
            api_key = os.environ.get("OPENAI_API_KEY")
            base_url = os.environ.get("OPENAI_BASE_URL")
            if not api_key:
                print("  WARN: OPENAI_API_KEY not set; skipping bindings pass")
            else:
                bclient = OpenAI(api_key=api_key,
                                   base_url=base_url if base_url else None)
                bbudget = Budget(cap_usd=5.0)
                kept, dropped, decisions = run_bindings_pass(
                    bclient, args.bindings_model, bbudget,
                    call_sites=lib_data["call_sites"],
                    child_skills=lib_data["child_skills"],
                    parents_by_id=parents_by_id,
                    verbose=True,
                )
                # Update lib_data
                lib_data["call_sites"] = kept
                lib_data["call_sites_dropped"] = lib_data.get("call_sites_dropped", []) + dropped
                lib_data["n_call_sites"] = len(kept)
                lib_data["n_call_sites_dropped"] = len(lib_data["call_sites_dropped"])
                # Re-render parent skeletons with refined call_sites
                parents_with_cs = sorted({cs["parent_id"] for cs in kept})
                parents_to_rewrite = [p for p in parents if p["skill_id"] in parents_with_cs]
                lib_data["parents_rewritten"] = rewrite_parent_skeletons(
                    parents_to_rewrite, kept)
                lib_data["n_parents_rewritten"] = len(lib_data["parents_rewritten"])
                bindings_stats = {
                    "n_kept": len(kept),
                    "n_dropped": len(dropped),
                    "spent_usd": bbudget.spent,
                }
                lib_data["bindings_stats"] = bindings_stats
                save_json(decisions,
                            OUT / "call_site_llm_decisions.json")
                print(f"  bindings budget: ${bbudget.spent:.4f}, "
                       f"saved {OUT / 'call_site_llm_decisions.json'}")

    # Phase 5.5: LLM-aware cleanup pass (default ON)
    # This removes residual content that conflicts with child contracts —
    # e.g. buggy examples that survived the deterministic rewrite.
    cleanup_stats = None
    if args.llm_cleanup and lib_data["parents_rewritten"]:
        print()
        print("Phase 5.5: LLM-aware cleanup pass on rewritten parents...")
        try:
            from openai import OpenAI
        except ImportError:
            print("  WARN: openai package not installed; skipping cleanup")
        else:
            api_key = os.environ.get("OPENAI_API_KEY")
            base_url = os.environ.get("OPENAI_BASE_URL")
            if not api_key:
                print("  WARN: OPENAI_API_KEY not set; skipping cleanup")
            else:
                client = OpenAI(api_key=api_key,
                                  base_url=base_url if base_url else None)
                budget = Budget(cap_usd=5.0)  # $5 hard cap for cleanup
                cleanup_stats = run_cleanup_pass(
                    client, args.cleanup_model, budget,
                    parents_rewritten=lib_data["parents_rewritten"],
                    call_sites=lib_data["call_sites"],
                    child_skills=lib_data["child_skills"],
                    verbose=True,
                )
                print(f"  cleanup done: total={cleanup_stats['n_total']} "
                       f"cleaned={cleanup_stats['n_cleaned']} "
                       f"skipped={cleanup_stats['n_skipped']} "
                       f"failed={cleanup_stats['n_failed']}")
                print(f"  cleanup budget: ${budget.spent:.4f}")
                lib_data["cleanup_stats"] = cleanup_stats

    # Save JSON
    save_json(lib_data, OUT / "refactored_library.json")

    # Save per-parent markdown files
    for pr in lib_data["parents_rewritten"]:
        safe_name = pr["parent_id"].replace("/", "_")
        md_path = OUT / f"parent_{safe_name}.rewritten.md"
        cleanup_marker = " (cleanup applied)" if pr.get("cleanup_applied") else ""
        header = (f"<!-- refactored skeleton for {pr['parent_id']} "
                   f"({pr['n_call_sites_in_parent']} of {pr['n_units_total']} "
                   f"units replaced by child invocations{cleanup_marker}) -->\n\n")
        md_path.write_text(header + pr["rewritten_text"])

    # Save summary
    L = ["=" * 80,
          f"  Refactored Library — {LIB.name}",
          "=" * 80, ""]
    L.append(f"  Promoted children:    {lib_data['n_promoted']}")
    L.append(f"    auto:               {n_auto}")
    L.append(f"    review:             {n_review}")
    L.append(f"  Call-sites kept:      {lib_data['n_call_sites']}")
    L.append(f"  Call-sites dropped:   {lib_data['n_call_sites_dropped']} "
              f"(per-unit binding filter)")
    L.append(f"  Parents touched:      {lib_data['n_parents_rewritten']}")
    L.append("")
    L.append("  Per parent:")
    for pr in lib_data["parents_rewritten"]:
        L.append(f"    {pr['parent_id']:<50s}  "
                  f"{pr['n_call_sites_in_parent']}/{pr['n_units_total']} units rewritten")
    L.append("")
    L.append("  Per child skill:")
    cs_by_child = {}
    for cs in lib_data["call_sites"]:
        cs_by_child.setdefault(cs["child_skill_id"], []).append(cs)
    for child_id, sites in cs_by_child.items():
        parents_covered = sorted({s["parent_id"] for s in sites})
        L.append(f"    {child_id:<40s} "
                  f"{len(sites)} call-sites across {len(parents_covered)} parents")
        for p in parents_covered[:5]:
            L.append(f"        - {p}")
        if len(parents_covered) > 5:
            L.append(f"        ... and {len(parents_covered) - 5} more")
    text = "\n".join(L)
    (OUT / "refactored_summary.txt").write_text(text)
    print()
    print(text)
    print(f"\nwrote {OUT / 'refactored_library.json'}")
    print(f"wrote {len(lib_data['parents_rewritten'])} parent_<id>.rewritten.md files")
    print(f"wrote {OUT / 'refactored_summary.txt'}")


if __name__ == "__main__":
    main()
