import json
import os
import re
import asyncio
import inspect
from pathlib import Path
import yaml
from typing import Any
import sys


_SKILLS_REF_SRC = str(Path(__file__).resolve().parent)
if _SKILLS_REF_SRC not in sys.path:
    sys.path.insert(0, _SKILLS_REF_SRC)

# Try to import GoS engine
try:
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from gos import SkillGraphRAG
    from gos.core.engine import build_default_embedding_service, build_default_llm_service
    from gos.core.schema import QuerySchema
except ImportError:
    SkillGraphRAG = None
    build_default_embedding_service = None
    build_default_llm_service = None
    QuerySchema = None

try:
    from .utils import get_llm_response
    from .skills_ref import to_prompt as skills_ref_to_prompt
except ImportError:
    from utils import get_llm_response
    from skills_ref import to_prompt as skills_ref_to_prompt

class SkillModule:
    def __init__(self, **kwargs):
        self.skills_dir = Path(kwargs.get("skills_dir", "skills"))
        self.model = kwargs.get("model", "gpt-4o")
        # Modes: "all_full", "gos", "vector", "none", "sap"
        self.mode = kwargs.get("mode", "gos")
        self.gos_workspace = kwargs.get("gos_workspace", None)
        self.enable_alfworld_gating = bool(kwargs.get("enable_alfworld_gating", False))

        # sap mode (Option Z): re-use GoS retrieval to find top-K
        # parents, then substitute payloads with rewritten_text + child
        # contracts from a precomputed refactored_library.json. If the
        # refactored_library path is None or the file is missing, this
        # mode falls back to gos behavior (Y plumbing).
        self.refactored_library_path = kwargs.get("refactored_library", None)
        self.sap_data = None    # loaded lazily on first retrieve

        self.last_retrieval_result: Any = None
        self.last_retrieval_status = "NOT_RUN"
        self.last_retrieval_summary = ""
        self.last_retrieved_skill_names = []
        self.last_retrieval_query = ""
        self.runtime_skill_events = []
        self.runtime_skill_count = 0
        self.runtime_last_injection_step = -999

        self.metadata = self._load_metadata()

        # Initialize GoS if needed
        if self.mode in {"gos", "vector", "sap"} and not self.gos_workspace:
            raise ValueError(f"{self.mode} mode requires `gos_workspace`.")

        if self.mode in {"gos", "vector", "sap"} and SkillGraphRAG is None:
            raise ImportError("Failed to import `gos.SkillGraphRAG`; retrieval is unavailable.")

        if self.mode in {"gos", "vector", "sap"} and SkillGraphRAG and self.gos_workspace:
            gos_workspace = str(Path(self.gos_workspace).expanduser().resolve())
            self.gos_workspace = gos_workspace
            self.rag = SkillGraphRAG(
                working_dir=gos_workspace,
                config=SkillGraphRAG.Config(
                    working_dir=gos_workspace,
                    prebuilt_working_dir=gos_workspace,
                    llm_service=build_default_llm_service() if build_default_llm_service else None,
                    embedding_service=build_default_embedding_service() if build_default_embedding_service else None,
                    # ALFWorld already constructs a retrieval-oriented query.
                    # Skip GoS internal LLM rewrite here to avoid schema-format drift.
                    enable_query_rewrite=False,
                )
            )
        else:
            self.rag = None

    def _log(self, message):
        print(f"[SkillModule] {message}")

    # ── sap mode (Option Z) ───────────────────────────────────
    def _load_sap_lazy(self):
        """Lazy-load refactored_library.json on first access."""
        if self.sap_data is not None:
            return self.sap_data
        if not self.refactored_library_path:
            self.sap_data = {}      # signals "fallback to gos"
            return self.sap_data
        path = Path(self.refactored_library_path).expanduser().resolve()
        if not path.exists():
            self._log(f"sap: refactored_library not found at {path}; "
                      f"falling back to gos behavior")
            self.sap_data = {}
            return self.sap_data
        try:
            with open(path, "r") as f:
                data = json.load(f)
            self._log(f"sap: loaded refactored_library from {path} "
                      f"({len(data.get('parents_rewritten', []))} parents rewritten, "
                      f"{len(data.get('child_skills', []))} child contracts, "
                      f"{len(data.get('call_sites', []))} call-sites)")
            self.sap_data = data
        except Exception as e:
            self._log(f"sap: failed to load {path}: {e}; falling back")
            self.sap_data = {}
        return self.sap_data

    def _is_child_skill(self, skill_name):
        """True iff skill_name is a SaP refactored child contract (designed
        to be invoked by a parent, not retrieved directly)."""
        if self.mode != "sap":
            return False
        data = self._load_sap_lazy()
        if not data:
            return False
        if getattr(self, "_child_ids_cache", None) is None:
            self._child_ids_cache = set()
            for c in data.get("child_skills", []):
                cid = c.get("child_skill_id") or c.get("contract", {}).get("id", "")
                if cid:
                    self._child_ids_cache.add(cid)
        return skill_name in self._child_ids_cache

    def _filter_children_from_retrieval(self, result, target_k):
        """Strip child skills from a retrieval result and keep top `target_k`
        parents. Children should be reached via parent's invoke() expansion,
        not selected at top-level."""
        if self.mode != "sap":
            return result
        kept = [s for s in result.skills if not self._is_child_skill(s.name)]
        result.skills = kept[:target_k]
        return result

    def _build_sap_substituted_text(self, skill_name):
        """Return the agent-facing text for a refactored skill: rewritten
        parent skeleton (with `invoke(<child_id>, args)` placeholders) plus
        the inlined contract spec for each referenced child plus the
        ORIGINAL action templates (call_site.original_unit_text) restored
        for each invoke (Fix C: undoes the procedural detail that RC cleanup
        deleted). Returns None if the refactored library has no rewritten
        version for this skill_name.
        """
        data = self._load_sap_lazy()
        if not data:
            return None
        rewritten_by_id = {pr["parent_id"]: pr
                              for pr in data.get("parents_rewritten", [])}
        child_by_id = {c.get("child_skill_id") or c.get("contract", {}).get("id"): c
                          for c in data.get("child_skills", [])}
        children_for_parent = {}
        call_sites_for_parent = {}
        for cs in data.get("call_sites", []):
            children_for_parent.setdefault(cs["parent_id"], set()).add(cs["child_skill_id"])
            call_sites_for_parent.setdefault(cs["parent_id"], []).append(cs)

        candidates = [skill_name, f"gos_{skill_name}", f"anthropic_{skill_name}"]
        rewritten = None
        relevant_children = []
        relevant_call_sites = []
        for cand in candidates:
            if cand in rewritten_by_id:
                rewritten = rewritten_by_id[cand]
                for cid in children_for_parent.get(cand, []):
                    if cid in child_by_id:
                        relevant_children.append(child_by_id[cid])
                relevant_call_sites = call_sites_for_parent.get(cand, [])
                break
        if rewritten is None:
            return None

        new_content_parts = [
            f"## REFACTORED SKILL: {skill_name}\n",
            f"**CRITICAL — how to read this skill:** The skill body below "
            f"contains `invoke(<child_id>, args)` placeholders and EXAMPLES "
            f"that show invoke(...) as Actions. **Those examples are "
            f"abstract / illustrative ONLY — do NOT emit `Action: invoke(...)`. "
            f"The environment will not execute invoke.** Instead, use the "
            f"\"CONCRETE ACTION TEMPLATES\" section below: it lists the "
            f"actual `go to / take / put / open / use / heat / cool / clean` "
            f"actions that each invoke replaces. Emit those concrete actions "
            f"directly, substituting the bindings.\n",
        ]
        if relevant_call_sites:
            new_content_parts.append(
                "### CONCRETE ACTION TEMPLATES (use these env actions, "
                "NOT the invoke() syntax):\n")
            for cs in relevant_call_sites[:6]:
                cid = cs.get("child_skill_id", "?")
                title = cs.get("original_unit_title", "")
                orig = (cs.get("original_unit_text") or "").strip()
                bindings = cs.get("bindings") or {}
                if not orig:
                    continue
                new_content_parts.append(f"\n#### what `invoke({cid}, ...)` actually means (replaces parent section \"{title}\"):")
                new_content_parts.append(orig[:600])
                if bindings:
                    new_content_parts.append(
                        "  bindings to substitute: " +
                        ", ".join(f"{k}={v}" for k, v in bindings.items()))
            new_content_parts.append("")
        new_content_parts.append("### Skill body (with `invoke(...)` placeholders; treat as informational, NOT executable):\n")
        new_content_parts.append(rewritten.get("rewritten_text", "")[:3000])
        if relevant_children:
            new_content_parts.append(
                f"\n\n### CHILD CONTRACTS (high-level I/O semantics for the "
                f"invoke(...) calls above; informational):\n")
            for child in relevant_children[:4]:
                contract = child.get("contract") or {}
                cid = child.get("child_skill_id", contract.get("id", "?"))
                new_content_parts.append(f"\n#### child: `{cid}`")
                new_content_parts.append(f"  trigger: {contract.get('trigger', '')}")
                inputs = contract.get("input_schema") or {}
                if inputs:
                    new_content_parts.append(
                        f"  input_schema: {{ " +
                        ", ".join(f"{k}" for k in inputs.keys()) + " }")
                outputs = contract.get("output_schema") or {}
                if outputs:
                    new_content_parts.append(
                        f"  output_schema: {{ " +
                        ", ".join(f"{k}" for k in outputs.keys()) + " }")
                pre = contract.get("preconditions") or []
                if pre:
                    new_content_parts.append(
                        f"  preconditions: " +
                        "; ".join(str(p)[:80] for p in pre[:3]))
                post = contract.get("postconditions") or []
                if post:
                    new_content_parts.append(
                        f"  postconditions: " +
                        "; ".join(str(p)[:80] for p in post[:3]))
        return "\n".join(new_content_parts)

    def _apply_sap_substitution(self, task, skill_payloads, skill_names):
        """For each retrieved skill (by name), if the refactored library has
        a rewritten version, substitute the payload's content with the
        rewritten parent + inlined child contracts."""
        new_payloads = []
        for payload, skill_name in zip(skill_payloads, skill_names):
            new_text = self._build_sap_substituted_text(skill_name)
            if new_text is None:
                # No refactored version for this parent; keep original
                new_payloads.append(payload)
                continue
            new_payload = dict(payload) if isinstance(payload, dict) else payload
            if isinstance(new_payload, dict):
                if "content" in new_payload:
                    new_payload["content"] = new_text
                if "body" in new_payload:
                    new_payload["body"] = new_text
            else:
                new_payload = new_text
            new_payloads.append(new_payload)
        return new_payloads
    # ───────────────────────────────────────────────────────────────────

    def _is_alfworld_task(self, task):
        task_lower = task.lower()
        return "your task is to:" in task_lower or "you are in the middle of a room" in task_lower

    def _extract_alfworld_goal(self, task):
        match = re.search(r"your task is to:\s*(.+)", task, re.IGNORECASE)
        if match:
            return match.group(1).splitlines()[0].strip().rstrip('.')
        return task.strip()

    def _infer_task_type(self, goal):
        goal_lower = goal.lower()
        if "look at" in goal_lower or "examine" in goal_lower:
            return "examine"
        if "find two" in goal_lower or "put two" in goal_lower:
            return "put_two"
        if "clean" in goal_lower:
            return "clean_and_place"
        if "cool" in goal_lower:
            return "cool_and_place"
        if "heat" in goal_lower or "hot " in goal_lower:
            return "heat_and_place"
        if "put" in goal_lower:
            return "put"
        return "other"

    def _extract_required_state(self, goal):
        goal_lower = goal.lower()
        if "clean" in goal_lower:
            return "clean"
        if "cool" in goal_lower:
            return "cool"
        if "heat" in goal_lower or "hot " in goal_lower:
            return "hot"
        return "none"

    def _extract_count(self, goal):
        goal_lower = goal.lower()
        if "find two" in goal_lower or "put two" in goal_lower:
            return "2"
        if re.search(r"\bsome\b", goal_lower):
            return "some"
        if re.search(r"\ban?\b", goal_lower):
            return "1"
        return "unspecified"

    def _extract_target_receptacle(self, goal):
        patterns = [
            r"\b(?:in|into|inside|on|onto|under)\s+([a-z0-9]+)",
            r"\bto\s+([a-z0-9]+)$",
        ]
        for pattern in patterns:
            matches = re.findall(pattern, goal, re.IGNORECASE)
            if matches:
                return matches[-1].lower()
        return "unknown"

    def _extract_device(self, goal):
        devices = [
            "desklamp",
            "microwave",
            "fridge",
            "sinkbasin",
            "coffeemachine",
            "stoveburner",
            "cabinet",
            "drawer",
            "dresser",
            "garbagecan",
            "diningtable",
            "countertop",
            "desk",
            "sidetable",
            "table",
            "toilet",
        ]
        goal_lower = goal.lower()
        for device in devices:
            if device in goal_lower:
                return device
        return "none"

    def _extract_primary_object(self, goal):
        goal_lower = goal.lower().rstrip('.')
        patterns = [
            r"(?:look at|examine)\s+the\s+([a-z0-9]+)",
            r"(?:look at|examine)\s+([a-z0-9]+)",
            r"(?:put|find|clean|cool|heat)\s+(?:a|an|some|two)?\s*(?:clean|cool|hot|heated)?\s*([a-z0-9]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, goal_lower)
            if match:
                candidate = match.group(1).lower()
                if candidate not in {"clean", "cool", "hot", "heated"}:
                    return candidate
        tokens = re.findall(r"[a-z0-9]+", goal_lower)
        stop = {
            "put", "find", "clean", "cool", "heat", "hot", "heated", "look", "at", "examine",
            "the", "a", "an", "some", "two", "in", "into", "inside", "on", "onto",
            "under", "with", "and", "it", "them"
        }
        for token in tokens:
            if token not in stop:
                return token
        return "unknown"

    def _build_alfworld_structured_query(self, task):
        goal = self._extract_alfworld_goal(task)
        task_type = self._infer_task_type(goal)
        obj = self._extract_primary_object(goal)
        required_state = self._extract_required_state(goal)
        target_receptacle = self._extract_target_receptacle(goal)
        count = self._extract_count(goal)
        device = self._extract_device(goal)
        if QuerySchema is None:
            return (
                "environment=alfworld; "
                f"task_type={task_type}; "
                f"goal={goal}; "
                f"object={obj}; "
                f"required_state={required_state}; "
                f"target_receptacle={target_receptacle}; "
                f"count={count}; "
                f"device={device}; "
                "actions=navigate,take,move,open,close,heat,cool,clean,use,look; "
                "optimization=shortest_valid_action_sequence"
            )

        schema = QuerySchema(
            goal=goal,
            task_name=f"alfworld-{task_type}",
            domain=["alfworld", "household manipulation", "embodied task planning"],
            operations=[
                task_type,
                "navigate",
                "take",
                "move",
                "open",
                "close",
                "heat",
                "cool",
                "clean",
                "look",
            ],
            artifacts=[obj, target_receptacle, device],
            constraints=[
                f"required_state={required_state}",
                f"count={count}",
                "optimize for shortest valid action sequence",
            ],
            keywords=[
                "environment=alfworld",
                f"object={obj}",
                f"target_receptacle={target_receptacle}",
                f"device={device}",
            ],
        )
        return schema.to_query_text()

    def _build_targeted_retrieval_query(self, task):
        if self._is_alfworld_task(task):
            return self._build_alfworld_structured_query(task)

        return task.strip()

    def _skill_confident_enough(self, skill):
        rerank_score = float(getattr(skill, "rerank_score", 0.0) or 0.0)
        score = float(getattr(skill, "score", 0.0) or 0.0)
        semantic_rank = getattr(skill, "semantic_rank", None)

        if rerank_score >= 0.60:
            return True
        if rerank_score >= 0.45 and semantic_rank is not None and semantic_rank <= 2:
            return True
        if score >= 0.30 and semantic_rank is not None and semantic_rank <= 2:
            return True
        return False

    def _effective_top_k(self, task, requested_top_k):
        if self._is_alfworld_task(task):
            return min(requested_top_k, 4)
        return requested_top_k

    def _extract_vector_skill_payloads(self, result):
        skill_names = [skill.name for skill in result.skills]
        skill_payloads = [skill.payload for skill in result.skills]
        return skill_payloads, skill_names

    def _filter_skills_for_task(self, task, result, *, source_label="retrieval"):
        if not self._is_alfworld_task(task) or not self.enable_alfworld_gating:
            skill_names = [skill.name for skill in result.skills]
            skill_payloads = [skill.payload for skill in result.skills]
            return skill_payloads, skill_names

        confident_skills = [skill for skill in result.skills if self._skill_confident_enough(skill)]
        if not confident_skills:
            self._log(f"alfworld gating pruned all {source_label} results; returning NO_SKILL_HIT")
            return [], []

        selected_skills = confident_skills
        skill_names = [skill.name for skill in selected_skills]
        skill_payloads = [skill.payload for skill in selected_skills]

        self._log(
            f"alfworld gating kept {len(selected_skills)}/{len(result.skills)} {source_label} skills after confidence pruning"
        )
        return skill_payloads, skill_names

    def should_generate_procedure(self, task):
        return False

    def _reset_retrieval_state(self):
        self.last_retrieval_result = None
        self.last_retrieval_status = "NOT_RUN"
        self.last_retrieval_summary = ""
        self.last_retrieved_skill_names = []
        self.last_retrieval_query = ""
        self.runtime_skill_events = []
        self.runtime_skill_count = 0
        self.runtime_last_injection_step = -999

    def _set_retrieval_state(self, status, summary="", skill_names=None, result=None):
        self.last_retrieval_status = status
        self.last_retrieval_summary = summary or ""
        self.last_retrieved_skill_names = list(skill_names or [])
        self.last_retrieval_result = result

    def _all_metadata_entries(self):
        return [
            {
                "name": name,
                "description": data.get("description", ""),
                "skill_dir": data.get("skill_dir", ""),
            }
            for name, data in sorted(self.metadata.items())
        ]

    def _all_metadata_context(self):
        lines = []
        for item in self._all_metadata_entries():
            lines.append(f"- {item['name']}: {item['description']}")
        return "\n".join(lines)

    def _all_metadata_skill_bundle(self):
        metadata_context = self._all_metadata_context()
        if not metadata_context:
            return []
        return [
            "=== Full Skill Library Metadata ===\n"
            "The following is the full available skill library. Treat it as capability exposure, not as a pre-filtered retrieval result.\n\n"
            f"{metadata_context}"
        ]

    def get_all_full_exposure_messages(self):
        if self.mode != "all_full":
            return []

        skill_dirs = [Path(item["skill_dir"]) for item in self._all_metadata_entries() if item.get("skill_dir")]
        if not skill_dirs:
            return []

        prompt_block = skills_ref_to_prompt(skill_dirs)
        return [
            "The following block lists the full available skill library in Anthropic skills-ref format. "
            "This is not a pre-filtered retrieval result. Use it as a catalog of available capabilities. "
            "If a skill looks relevant, prefer reading only the few most relevant skills by exact name.\n\n"
            f"{prompt_block}"
        ]

    def get_all_full_exposure_message(self):
        messages = self.get_all_full_exposure_messages()
        if not messages:
            return ""
        return messages[0]

    def get_agent_skill_request_message(self):
        if self.mode == "none":
            return ""

        lines = [
            "Tool-style skill access is available in this run.",
            "Use it when you are blocked, the syntax is unclear, the retrieved skills look mismatched to the current blocker, or 1-2 actions already failed.",
            "Use skills conditionally, not by default: if the next environment action is already obvious from the current observation, act directly instead of retrieving.",
            "Prefer retrieval when the exact syntax is unclear, the task needs a multi-step procedure or tool setup, the current shortlist looks mismatched, or 1-2 recent actions failed.",
            "Prefer READ_SKILL when you already have a promising exact skill name. For measurement, electrical connection, conditional placement, or any unfamiliar procedure, do not guess the syntax twice in a row; retrieve first, then read the single best skill before continuing.",
            "Mirror the current benchmark vocabulary in retrieval queries. Reuse the task's own object, property, tool, room, and container words instead of naming a different environment.",
            "In a request turn, output exactly two lines: `Thought: ...` and `SkillRequest: ...`. Do not output an `Action:` line in the same turn.",
        ]

        if self.mode == "gos":
            lines.extend([
                "Available requests:",
                "- `SkillRequest: GOS_RETRIEVE <short focused query>` to search GoS again. Prefer this first when you are blocked or the current shortlist looks noisy, generic, or off-task.",
                "- `SkillRequest: READ_SKILL <exact skill name>` to read one concrete skill after GoS has surfaced a promising candidate.",
                "Examples:",
                "- `Thought: I already have a good shortlist and need the exact instructions from one candidate.`",
                "  `SkillRequest: READ_SKILL <exact shortlisted skill name>`",
                "- `Thought: The current shortlist looks noisy. I need a narrower retrieval grounded in the current task.`",
                "  `SkillRequest: GOS_RETRIEVE <target object> <property or subgoal> <tool if needed> <room> <destination container>`",
                "- `Thought: I failed twice and need retrieval that mirrors the current blocker instead of guessing again.`",
                "  `SkillRequest: GOS_RETRIEVE <task-specific keywords from the current benchmark only>`",
                "Use skill requests sparingly, only when they directly help the next action. Prefer a two-step pattern: `GOS_RETRIEVE` to shortlist candidates, then `READ_SKILL` for the single best candidate before guessing again.",
            ])
        elif self.mode == "vector":
            lines.extend([
                "Available requests:",
                "- `SkillRequest: VECTOR_RETRIEVE <short focused query>` to run vector-only retrieval again. This uses embedding similarity only, without graph propagation or lexical expansion.",
                "- `SkillRequest: READ_SKILL <exact skill name>` to read a known skill file. Use this only when you already know the exact skill you want.",
                "Examples:",
                "- `Thought: I already have a good shortlist and need the exact instructions from one candidate.`",
                "  `SkillRequest: READ_SKILL <exact shortlisted skill name>`",
                "- `Thought: The current shortlist looks noisy. I need a narrower vector retrieval grounded in the current task.`",
                "  `SkillRequest: VECTOR_RETRIEVE <target object> <property or subgoal> <tool if needed> <room> <destination container>`",
                "- `Thought: I failed twice and need vector retrieval that mirrors the current blocker instead of guessing again.`",
                "  `SkillRequest: VECTOR_RETRIEVE <task-specific keywords from the current benchmark only>`",
                "Use skill requests sparingly, only when they directly help the next action. In vector mode, prefer `VECTOR_RETRIEVE` before guessing again, and `READ_SKILL` only after a specific skill name looks relevant.",
            ])
        elif self.mode == "all_full":
            lines.extend([
                "Available requests:",
                "- `SkillRequest: READ_SKILL <exact skill name>` to read a known skill file.",
                "Examples:",
                "- `Thought: The full catalog already shows a likely match and I need its exact instructions.`",
                "  `SkillRequest: READ_SKILL <exact skill name already visible in the catalog>`",
                "Use skill requests sparingly, only when they directly help the next action. In all_full mode, do not attempt retrieval; read a specific skill only when the full catalog already reveals a directly relevant candidate.",
            ])
        elif self.mode == "sap":
            lines.extend([
                "The library is REFACTORED — each skill body may contain `invoke(<child_id>, args)` placeholders that point to verified callable child contracts. When you READ_SKILL on a parent, the child contracts referenced via `invoke(...)` are listed inline below the parent body. Treat each `invoke(...)` as a HIERARCHICAL CALL: read the child contract's trigger / inputs / preconditions / postconditions, then translate to concrete benchmark actions yourself. Prefer the child contract's specification over residual parent prose.",
                "Available requests:",
                "- `SkillRequest: GOS_RETRIEVE <short focused query>` to search the refactored library again. The retrieval surfaces verified child contracts inline with the parent skill.",
                "- `SkillRequest: READ_SKILL <exact skill name>` to read one rewritten parent (with `invoke(child, ...)` placeholders + child contract block below).",
                "Examples:",
                "- `Thought: A child skill name in the shortlist looks like the exact procedure I need.`",
                "  `SkillRequest: READ_SKILL <exact shortlisted skill name>`",
                "- `Thought: The current shortlist looks noisy. Refine the retrieval with task-specific keywords.`",
                "  `SkillRequest: GOS_RETRIEVE <target object> <property or subgoal>`",
                "When using the refactored library, prefer the child contract's typed inputs/outputs as the structural anchor; the parent's residual text only carries parent-specific extras that the child does not abstract over.",
            ])

        else:
            return ""
        return "\n".join(lines)

    def _skill_catalog_entries(self, skill_names):
        entries = []
        for name in skill_names or []:
            meta = self.metadata.get(name, {})
            entries.append(
                {
                    "name": name,
                    "description": meta.get("description", ""),
                    "skill_dir": meta.get("skill_dir", ""),
                }
            )
        return entries

    def _format_retrieval_shortlist(self, header, query, skill_names, source_label):
        if not skill_names:
            return f"{header}\n\nNo relevant skills were retrieved."

        lines = [
            header,
            f"Query: {query}",
            f"Shortlisted {source_label} candidates:",
        ]
        for entry in self._skill_catalog_entries(skill_names[:3]):
            description = entry["description"] or "No description available."
            lines.append(f"- {entry['name']}: {description}")
            if entry["skill_dir"]:
                lines.append(f"  Source: {entry['skill_dir']}/SKILL.md")
        lines.extend([
            "Do not assume these summaries are enough to execute correctly.",
            "If one candidate looks directly relevant to the current blocker, issue `SkillRequest: READ_SKILL <exact skill name>` before trying another uncertain action.",
        ])
        return "\n".join(lines)

    def _load_metadata(self):
        """Load existing metadata from file."""
        metadata = {}
        if not self.skills_dir.exists():
            return metadata
            
        for skill_dir in self.skills_dir.iterdir():
            if skill_dir.is_dir():
                skill_md_path = skill_dir / "SKILL.md"
                if skill_md_path.exists():
                    try:
                        content = skill_md_path.read_text(encoding="utf-8")
                        if content.strip().startswith('---'):
                            parts = content.split('---', 2)
                            if len(parts) >= 3:
                                header_data = yaml.safe_load(parts[1])
                                if isinstance(header_data, dict) and header_data.get('name') and header_data.get('description'):
                                    metadata[header_data['name']] = {
                                        'description': header_data['description'],
                                        'skill_dir': str(skill_dir)
                                    }
                    except Exception as e:
                        print(f"[ERROR] Failed to parse SKILL.md for {skill_dir.name}: {e}")
        return metadata
    
    def retrieve_relevant_skills(self, task, top_k=15):
        self._reset_retrieval_state()
        effective_top_k = self._effective_top_k(task, top_k)
        retrieval_query = self._build_targeted_retrieval_query(task)
        self.last_retrieval_query = retrieval_query
        self._log(
            f"retrieve_relevant_skills start mode={self.mode} top_k={top_k} effective_top_k={effective_top_k} task_chars={len(task)} retrieval_query={retrieval_query!r}"
        )

        if self.mode == "none":
            self._log("mode=none, skipping retrieval")
            return []
            
        if self.mode in {"gos", "vector", "sap"} and self.rag:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            if self.mode == "gos":
                self._log(f"starting GoS async_retrieve workspace={self.gos_workspace}")
                result = loop.run_until_complete(self.rag.async_retrieve(retrieval_query, top_n=effective_top_k))
                skill_payloads, skill_names = self._filter_skills_for_task(task, result, source_label="gos")
                status = "SKILL_HIT" if skill_names else "NO_SKILL_HIT"
                summary = result.summary
                if not skill_names:
                    status = "NO_SKILL_HIT"
                    summary = "ALFWorld retrieval gating pruned all retrieved skills; proceeding without injected skills."
                self._log(f"GoS async_retrieve finished status={status} n_skills={len(skill_names)}")
            elif self.mode == "sap":
                # Over-fetch so that filtering children still leaves us with
                # `effective_top_k` parent candidates.
                self._log(f"starting sap GoS retrieve workspace={self.gos_workspace}")
                result = loop.run_until_complete(self.rag.async_retrieve(retrieval_query, top_n=effective_top_k * 3))
                self._filter_children_from_retrieval(result, effective_top_k)
                skill_payloads, skill_names = self._filter_skills_for_task(task, result, source_label="sap")
                status = "SKILL_HIT" if skill_names else "NO_SKILL_HIT"
                summary = result.summary
                if not skill_names:
                    status = "NO_SKILL_HIT"
                    summary = "ALFWorld retrieval gating pruned all retrieved skills."
                # Try to apply refactored substitutions
                skill_payloads = self._apply_sap_substitution(
                    task, skill_payloads, skill_names)
                self._log(f"sap finished status={status} n_skills={len(skill_names)} "
                          f"refactored_lib={'loaded' if self.sap_data else 'fallback=gos'}")
            else:
                self._log(f"starting vector async_retrieve workspace={self.gos_workspace}")
                result = loop.run_until_complete(self.rag.async_retrieve_vector(retrieval_query, top_n=effective_top_k))
                skill_payloads, skill_names = self._filter_skills_for_task(task, result, source_label="vector")
                status = "SKILL_HIT" if skill_names else "NO_SKILL_HIT"
                summary = result.summary
                self._log(f"vector async_retrieve finished status={status} n_skills={len(skill_names)}")

            self._set_retrieval_state(
                status=status,
                summary=summary,
                skill_names=skill_names,
                result=result,
            )
            return skill_payloads

        if self.mode == "all_full":
            metadata_entries = self._all_metadata_entries()
            skill_names = [entry["name"] for entry in metadata_entries]
            status = "SKILL_HIT" if skill_names else "NO_SKILL_HIT"
            summary = (
                f"Exposed full skill metadata library in a single initial dialogue message ({len(skill_names)} skills). "
                "This matches the all-skills capability-exposure baseline rather than retrieval-time shortlisting."
            )
            self._set_retrieval_state(
                status=status,
                summary=summary,
                skill_names=skill_names,
                result={"skill_names": skill_names, "mode": "all_full"},
            )
            self._log(f"all_full exposure finished status={status} n_skills={len(skill_names)}")
            return []

        self._set_retrieval_state(status="NO_SKILL_HIT", summary="No retrieval configured for this mode.")
        return []

    def get_retrieval_guidance(self):
        if self.mode not in {"gos", "vector", "sap"} or self.last_retrieval_result is None:
            return ""

        if self.last_retrieval_status != "SKILL_HIT" or not self.last_retrieved_skill_names:
            return ""

        top_skills = self.last_retrieved_skill_names[:3]
        if self.mode == "gos":
            title = "Graph of Skills retrieval guidance:"
        elif self.mode == "sap":
            title = "SaP retrieval guidance (rewritten parents + verified child contracts):"
        else:
            title = "Vector-skills retrieval guidance:"
        content_parts = [
            title,
            f"Retrieval Status: {self.last_retrieval_status}",
        ]
        if top_skills:
            content_parts.append("Top retrieved skills: " + ", ".join(top_skills))
            skill_lines = ["Retrieved skill summaries:"]
            for entry in self._skill_catalog_entries(top_skills):
                description = entry["description"] or "No description available."
                skill_lines.append(f"- {entry['name']}: {description}")
            content_parts.append("\n".join(skill_lines))
        content_parts.append(
            "Use retrieval only as weak high-level guidance. Prioritize the shortest path from current observation to task completion."
        )
        content_parts.append(
            "Do not follow a rigid room-wide search checklist if the current observation already reveals the target object or target receptacle."
        )
        content_parts.append(
            "If the environment feedback or reward indicates the task is complete, stop issuing new actions immediately."
        )
        content_parts.append(
            "For ALFWorld action syntax: first navigate to the destination receptacle, then use the exact action form 'move {obj} to {recep}'."
        )
        content_parts.append(
            "Do not use 'use {obj}' unless the task explicitly requires turning on, heating, cooling, or cleaning something."
        )
        if self.mode == "gos":
            content_parts.append(
                "If the current retrieved skills look mismatched to the blocker, or 1-2 actions already failed, issue `SkillRequest: GOS_RETRIEVE <short focused query>`. Treat retrieval as a shortlist step and prefer `READ_SKILL` for the single best candidate before another uncertain action."
            )
        elif self.mode == "vector":
            content_parts.append(
                "If the current retrieved skills look mismatched to the blocker, or 1-2 actions already failed, issue `SkillRequest: VECTOR_RETRIEVE <short focused query>`. After vector retrieval surfaces a plausible exact skill name, prefer `READ_SKILL` for that single candidate before another uncertain action."
            )
        elif self.mode == "sap":
            content_parts.append(
                "If the current retrieved skills look mismatched to the blocker, or 1-2 actions already failed, issue `SkillRequest: GOS_RETRIEVE <short focused query>`. Treat retrieval as a shortlist step over the refactored library; prefer `READ_SKILL` for the single best candidate to see its `invoke(child, ...)` placeholders + parent-specific residual."
            )
        return "\n\n".join(part for part in content_parts if part)

    def _get_skill_contents(self, skill_names):
        skill_contents = []
        for name in skill_names:
            if name in self.metadata:
                skill_dir = Path(self.metadata[name]['skill_dir'])
                combined_text = f"=== Skill: {name} ===\n"
                for file_path in skill_dir.rglob('*'):
                    if file_path.is_file():
                        try:
                            content = file_path.read_text(encoding='utf-8')
                            combined_text += f"\n[File: {file_path.name}]\n{content}\n"
                        except: continue
                skill_contents.append(combined_text)
        return skill_contents

    def _parse_skill_request(self, response):
        if not isinstance(response, str):
            return None, ""

        patterns = [
            r"^SkillRequest:\s*(.+)$",
            r"^Action:\s*SkillRequest:\s*(.+)$",
        ]
        match = None
        for pattern in patterns:
            match = re.search(pattern, response, re.IGNORECASE | re.MULTILINE)
            if match:
                break
        if not match:
            return None, ""

        payload = match.group(1).strip()
        if not payload:
            return None, ""

        upper = payload.upper()
        if upper.startswith("READ_SKILL "):
            return "read_skill", payload[len("READ_SKILL "):].strip()
        if upper.startswith("GOS_RETRIEVE "):
            return "gos_retrieve", payload[len("GOS_RETRIEVE "):].strip()
        if upper.startswith("VECTOR_RETRIEVE "):
            return "vector_retrieve", payload[len("VECTOR_RETRIEVE "):].strip()
        return None, payload

    def _record_runtime_skill_event(self, step, trigger, query, skill_names):
        self.runtime_skill_count += 1
        self.runtime_last_injection_step = step
        self.runtime_skill_events.append(
            {
                "step": step,
                "trigger": trigger,
                "query": query,
                "skill_names": list(skill_names or []),
            }
        )

    def _format_agent_skill_response(self, header, skill_names, skill_payloads, char_budget=1200):
        if not skill_payloads:
            return ""
        clipped_payloads = [self._clip_text(payload, char_budget) for payload in skill_payloads[:2]]
        lines = [header, "Use this only if it directly improves the next action."]
        if skill_names:
            lines.append("Selected skills: " + ", ".join(skill_names[:2]))
        return "\n\n".join(lines + clipped_payloads)

    def handle_agent_skill_request(self, task, response, current_step):
        request_type, payload = self._parse_skill_request(response)
        if not request_type:
            return ""

        if request_type == "read_skill":
            skill_name = payload
            sap_substituted = None
            if self.mode == "sap":
                sap_substituted = self._build_sap_substituted_text(skill_name)
            if sap_substituted is not None:
                skill_payloads = [sap_substituted]
                char_budget = 5000
            else:
                skill_payloads = self._get_skill_contents([skill_name])[:1]
                char_budget = 1200
            if not skill_payloads:
                return (
                    f"Skill request could not be fulfilled: skill `{skill_name}` was not found. "
                    "Use an exact skill name from the available skill list or retrieval results."
                )
            self._record_runtime_skill_event(current_step, "agent_request:read_skill", skill_name, [skill_name])
            return self._format_agent_skill_response(
                f"Skill request fulfilled: READ_SKILL {skill_name}",
                [skill_name],
                skill_payloads,
                char_budget=char_budget,
            )

        if request_type == "gos_retrieve":
            if self.mode not in {"gos", "sap"} or not self.rag:
                return "Skill request could not be fulfilled: GOS_RETRIEVE is only available in gos / sap mode."
            query = payload
            if not query:
                return "Skill request could not be fulfilled: empty GOS_RETRIEVE query."
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            over_k = 6 if self.mode == "sap" else 2
            result = loop.run_until_complete(self.rag.async_retrieve(query, top_n=over_k))
            if self.mode == "sap":
                self._filter_children_from_retrieval(result, 2)
            src_label = "sap" if self.mode == "sap" else "gos"
            skill_payloads, skill_names = self._filter_skills_for_task(task, result, source_label=src_label)
            if self.mode == "sap":
                skill_payloads = self._apply_sap_substitution(task, skill_payloads, skill_names)
            skill_names = skill_names[:2]
            if not skill_names:
                return f"Skill request fulfilled: GOS_RETRIEVE {query}\n\nNo relevant skills were retrieved."
            self._record_runtime_skill_event(current_step, "agent_request:gos_retrieve", query, skill_names)
            return self._format_retrieval_shortlist(
                f"Skill request fulfilled: GOS_RETRIEVE {query}",
                query,
                skill_names,
                "GoS" if self.mode == "gos" else "SaP",
            )

        if request_type == "vector_retrieve":
            if self.mode != "vector" or not self.rag:
                return "Skill request could not be fulfilled: VECTOR_RETRIEVE is only available in vector mode."
            query = payload
            if not query:
                return "Skill request could not be fulfilled: empty VECTOR_RETRIEVE query."
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self.rag.async_retrieve_vector(query, top_n=2))
            skill_payloads, skill_names = self._filter_skills_for_task(task, result, source_label="vector")
            skill_payloads = skill_payloads[:2]
            skill_names = skill_names[:2]
            if not skill_names:
                return f"Skill request fulfilled: VECTOR_RETRIEVE {query}\n\nNo relevant skills were retrieved."
            self._record_runtime_skill_event(current_step, "agent_request:vector_retrieve", query, skill_names)
            return self._format_retrieval_shortlist(
                f"Skill request fulfilled: VECTOR_RETRIEVE {query}",
                query,
                skill_names,
                "vector",
            )

        return ""

    @staticmethod
    def _clip_text(text, max_chars=1800):
        if not text or len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."

    @staticmethod
    def _recent_actions(messages, limit=2):
        actions = []
        for message in reversed(messages or []):
            if message.get("role") != "assistant":
                continue
            content = message.get("content", "")
            if not isinstance(content, str):
                continue
            match = re.search(r"Action:\s*(.+)", content, re.IGNORECASE)
            if match:
                actions.append(match.group(1).strip())
            if len(actions) >= limit:
                break
        actions.reverse()
        return actions

    def _runtime_trigger_reason(self, observation, current_step):
        observation_lower = (observation or "").lower()
        if current_step - self.runtime_last_injection_step < 3:
            return ""
        if self.runtime_skill_count >= 2:
            return ""
        failure_markers = [
            "nothing happens",
            "nothing happened",
            "you can't",
            "cannot",
            "can't",
            "not found",
            "don't see",
            "do not see",
        ]
        for marker in failure_markers:
            if marker in observation_lower:
                return f"runtime_failure:{marker}"
        return ""

    def _build_runtime_retrieval_query(self, task, messages, observation):
        base_query = self._build_targeted_retrieval_query(task)
        recent_actions = self._recent_actions(messages)
        parts = [base_query]
        if recent_actions:
            parts.append("recent_actions=" + ", ".join(recent_actions))
        compact_observation = " ".join((observation or "").split())
        if compact_observation:
            parts.append("runtime_observation=" + compact_observation[:400])
        return "\n".join(part for part in parts if part)

    def _format_runtime_skill_hint(self, skill_names, skill_payloads, trigger):
        if not skill_payloads:
            return ""
        char_budget = 5000 if self.mode == "sap" else 1200
        clipped_payloads = [self._clip_text(payload, char_budget) for payload in skill_payloads[:2]]
        header = [
            f"Additional runtime skill support was injected because: {trigger}.",
            "Use the following skill details only if they directly help recover and reach the shortest path to completion.",
        ]
        if skill_names:
            header.append("Selected skills: " + ", ".join(skill_names[:2]))
        return "\n\n".join(header + clipped_payloads)

    def maybe_get_runtime_skill_hint(self, task, messages, observation, current_step):
        trigger = self._runtime_trigger_reason(observation, current_step)
        if not trigger:
            return ""

        dynamic_query = self._build_runtime_retrieval_query(task, messages, observation)
        skill_names = []
        skill_payloads = []

        if self.mode in {"gos", "vector", "sap"} and self.rag:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            if self.mode == "gos":
                result = loop.run_until_complete(self.rag.async_retrieve(dynamic_query, top_n=2))
                skill_payloads, skill_names = self._filter_skills_for_task(task, result, source_label="gos")
            elif self.mode == "sap":
                result = loop.run_until_complete(self.rag.async_retrieve(dynamic_query, top_n=6))
                self._filter_children_from_retrieval(result, 2)
                skill_payloads, skill_names = self._filter_skills_for_task(task, result, source_label="sap")
                skill_payloads = self._apply_sap_substitution(
                    task, skill_payloads, skill_names)
            else:
                result = loop.run_until_complete(self.rag.async_retrieve_vector(dynamic_query, top_n=2))
                skill_payloads, skill_names = self._filter_skills_for_task(task, result, source_label="vector")

            skill_payloads = skill_payloads[:2]
            skill_names = skill_names[:2]
        if not skill_payloads:
            return ""

        self._record_runtime_skill_event(current_step, trigger, dynamic_query, skill_names)
        self._log(
            f"runtime skill injection triggered step={current_step} trigger={trigger} n_skills={len(skill_names)}"
        )
        return self._format_runtime_skill_hint(skill_names, skill_payloads, trigger)

    def get_runtime_skill_events(self):
        return list(self.runtime_skill_events)
