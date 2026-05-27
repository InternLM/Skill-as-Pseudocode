#!/usr/bin/env python3
"""
_parser_base.py — Phase 8.3: parse Anthropic Skills into typed
parent units suitable for downstream candidate proposal and contract
extraction.

For each skill:
  - YAML frontmatter (name, description, license)
  - SKILL.md procedural units (heading-bounded sections, with sub-units
    for bulleted lists, code blocks, tables)
  - Script signatures via static analysis:
      Python: argparse, click usage strings, return-shape heuristics
      shell: Usage: blocks and flag regexes
      JS/TS: commander / yargs (best-effort regex)
  - One-level reference list (markdown links to local files)
  - Provenance graph: each procedural unit → which scripts/refs it links to

Output: results_anthropic/parents.json — list of parent units, each
representing one Anthropic skill, with its decomposed procedural units.
"""
from __future__ import annotations
import ast, hashlib, json, re, sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent
OUT = ROOT / "results_anthropic"
OUT.mkdir(parents=True, exist_ok=True)
REPO = Path("/tmp/anthropic-skills")
SKILLS_DIR = REPO / "skills"


# ── YAML frontmatter ─────────────────────────────────────────────────────
FRONTMATTER_RE = re.compile(r"^---\n(.+?)\n---\n", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m: return {}, text
    fm_block = m.group(1)
    body = text[m.end():]
    fm = {}
    # Minimal YAML parser — handle key: value, with quoted strings, no nesting.
    cur_key = None; cur_val_lines = []
    for line in fm_block.splitlines():
        if not line.strip(): continue
        # Detect indented continuation
        if line[0] in " \t" and cur_key:
            cur_val_lines.append(line.strip())
            continue
        if cur_key is not None:
            fm[cur_key] = " ".join(cur_val_lines).strip()
            cur_key = None; cur_val_lines = []
        m2 = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$', line)
        if m2:
            k, v = m2.group(1), m2.group(2)
            v = v.strip()
            # Strip surrounding quotes
            if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or
                                   (v[0] == "'" and v[-1] == "'")):
                v = v[1:-1]
            if v:
                fm[k] = v
                cur_key = None
            else:
                cur_key = k; cur_val_lines = []
    if cur_key is not None:
        fm[cur_key] = " ".join(cur_val_lines).strip()
    return fm, body


# ── Markdown procedural unit segmentation ────────────────────────────────
HEADING_RE = re.compile(r'^(#{1,6})\s+(.*?)\s*$', re.MULTILINE)
CODE_FENCE_RE = re.compile(r'^```(\S*)?\s*\n(.*?)\n```', re.MULTILINE | re.DOTALL)
LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')


def segment_procedural_units(body: str) -> list[dict]:
    """Slice the markdown into heading-bounded sections."""
    # Strip code fences first to avoid heading regex catching them
    masks = []
    def mask(m):
        masks.append(m.group(0))
        return f"\n[[CODEFENCE_{len(masks)-1}]]\n"
    body_masked = CODE_FENCE_RE.sub(mask, body)

    headings = list(HEADING_RE.finditer(body_masked))
    units = []
    for i, h in enumerate(headings):
        level = len(h.group(1))
        title = h.group(2).strip()
        start = h.end()
        end = headings[i+1].start() if i+1 < len(headings) else len(body_masked)
        chunk_masked = body_masked[start:end].strip()
        # Re-substitute code fences
        for j, m_text in enumerate(masks):
            chunk_masked = chunk_masked.replace(f"[[CODEFENCE_{j}]]", m_text)

        # Within chunk, extract:
        #   - bullet list items
        #   - code blocks
        #   - inline links to local files
        bullets = re.findall(r'^[-*+]\s+(.+?)$', chunk_masked, flags=re.MULTILINE)
        code_blocks = []
        for cm in CODE_FENCE_RE.finditer(chunk_masked):
            code_blocks.append({"lang": cm.group(1) or "",
                                  "text": cm.group(2)[:1500]})
        # Links to local files (not http/https)
        links = []
        for lm in LINK_RE.finditer(chunk_masked):
            tgt = lm.group(2)
            if tgt.startswith("http"): continue
            links.append({"text": lm.group(1), "target": tgt})
        units.append({
            "level": level,
            "title": title,
            "text": chunk_masked[:2500],
            "n_bullets": len(bullets),
            "bullets_preview": bullets[:6],
            "code_blocks": code_blocks[:3],
            "local_links": links,
        })
    return units


# ── Python script static analysis ────────────────────────────────────────
def parse_python_script(src: str) -> Optional[dict]:
    """Extract argparse / click signature and return shape from a Python file."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    info = {"args": [], "uses_argparse": False, "uses_click": False,
            "imports": [], "return_hints": [], "has_main": False}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names: info["imports"].append(n.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module: info["imports"].append(node.module)
        elif isinstance(node, ast.Call):
            f = node.func
            # argparse: parser.add_argument("--foo", ...)
            if isinstance(f, ast.Attribute) and f.attr == "add_argument":
                if node.args and isinstance(node.args[0], ast.Constant):
                    name = str(node.args[0].value)
                    required = False; help_text = None; arg_type = None
                    for kw in node.keywords:
                        if kw.arg == "required" and isinstance(kw.value, ast.Constant):
                            required = bool(kw.value.value)
                        elif kw.arg == "help" and isinstance(kw.value, ast.Constant):
                            help_text = str(kw.value.value)[:200]
                        elif kw.arg == "type" and isinstance(kw.value, ast.Name):
                            arg_type = kw.value.id
                    info["args"].append({"name": name, "required": required,
                                            "help": help_text, "type": arg_type})
                    info["uses_argparse"] = True
            # click: @click.option('--foo')
            elif isinstance(f, ast.Attribute) and f.attr in {"option", "argument"}:
                if isinstance(f.value, ast.Name) and f.value.id == "click":
                    if node.args and isinstance(node.args[0], ast.Constant):
                        name = str(node.args[0].value)
                        required = False
                        for kw in node.keywords:
                            if kw.arg == "required" and isinstance(kw.value, ast.Constant):
                                required = bool(kw.value.value)
                        info["args"].append({"name": name, "required": required,
                                                "help": None, "type": None})
                        info["uses_click"] = True
        elif isinstance(node, ast.If):
            # `if __name__ == "__main__":`
            t = node.test
            if (isinstance(t, ast.Compare) and
                isinstance(t.left, ast.Name) and t.left.id == "__name__"):
                info["has_main"] = True
        elif isinstance(node, ast.FunctionDef):
            if node.returns:
                info["return_hints"].append(ast.unparse(node.returns)[:80])

    return info


# ── Shell script analysis (Usage: blocks) ────────────────────────────────
USAGE_RE = re.compile(r'#\s*Usage\s*[:\-]?\s*(.+)', re.IGNORECASE)
FLAG_RE = re.compile(r'(?:^|\s)(-{1,2}[A-Za-z][A-Za-z0-9_-]*)')


def parse_shell_script(src: str) -> dict:
    usages = USAGE_RE.findall(src)
    flags = sorted(set(FLAG_RE.findall(src)))
    return {"usages": [u.strip()[:200] for u in usages[:5]], "flags": flags[:20]}


# ── JS/TS commander/yargs heuristic ──────────────────────────────────────
def parse_js_script(src: str) -> dict:
    options = []
    for m in re.finditer(r'\.option\(\s*["\']([^"\']+)["\']', src):
        options.append(m.group(1))
    for m in re.finditer(r'\.command\(\s*["\']([^"\']+)["\']', src):
        options.append(m.group(1))
    args = []
    for m in re.finditer(r'argv\.([a-zA-Z_][a-zA-Z0-9_]*)', src):
        args.append(m.group(1))
    return {"options": sorted(set(options))[:20],
            "argv_keys": sorted(set(args))[:20]}


# ── Per-skill parsing ─────────────────────────────────────────────────────
def parse_skill(skill_dir: Path) -> dict:
    name = skill_dir.name
    skill_md_path = skill_dir / "SKILL.md"
    if not skill_md_path.exists():
        return None
    text = skill_md_path.read_text(errors="ignore")
    fm, body = parse_frontmatter(text)
    units = segment_procedural_units(body)

    # Find scripts and parse each. Walk all dirs (not just `scripts/`) since
    # different skills place utility code in `core/`, `examples/`, `tools/`, etc.
    SKIP_DIRS = {"templates", "node_modules", "__pycache__", ".git",
                 "test", "tests"}
    SCRIPT_EXTS = {".py", ".sh", ".bash", ".js", ".ts", ".mjs"}
    scripts = []
    for f in sorted(skill_dir.rglob("*")):
        if not f.is_file(): continue
        if f.suffix not in SCRIPT_EXTS: continue
        rel = str(f.relative_to(skill_dir))
        # Skip __init__.py and dotfiles
        if f.name.startswith("_") or f.name.startswith("."): continue
        # Skip files inside skipped dirs
        parts = rel.split("/")
        if any(p in SKIP_DIRS for p in parts[:-1]): continue
        try: src = f.read_text(errors="ignore")
        except: continue
        sig = None
        if f.suffix == ".py":
            sig = parse_python_script(src)
        elif f.suffix in {".sh", ".bash"}:
            sig = parse_shell_script(src)
        elif f.suffix in {".js", ".ts", ".mjs"}:
            sig = parse_js_script(src)
        if sig is not None:
            scripts.append({"path": rel, "type": f.suffix.lstrip("."),
                            "signature": sig,
                            "n_chars": len(src)})

    # Find references (one-level, local only) — files SKILL.md links to
    refs_seen = set()
    for u in units:
        for link in u["local_links"]:
            tgt = link["target"].split("#")[0]
            if not tgt: continue
            ref_path = skill_dir / tgt
            if ref_path.exists() and ref_path.is_file():
                refs_seen.add(tgt)

    references = []
    for tgt in sorted(refs_seen):
        ref_path = skill_dir / tgt
        try:
            ref_text = ref_path.read_text(errors="ignore")[:1500]
        except: ref_text = ""
        references.append({"path": tgt, "type": ref_path.suffix.lstrip("."),
                           "text_preview": ref_text})

    return {
        "skill_id": f"anthropic_{name}",
        "name": fm.get("name") or name,
        "description": fm.get("description") or "",
        "license": fm.get("license") or "unknown",
        "n_units": len(units),
        "n_scripts": len(scripts),
        "n_refs": len(references),
        "frontmatter": fm,
        "procedural_units": units,
        "scripts": scripts,
        "references": references,
    }


def main():
    skills = []
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir(): continue
        s = parse_skill(d)
        if s: skills.append(s)

    out_path = OUT / "parents.json"
    out_path.write_text(json.dumps(skills, indent=2, ensure_ascii=False))

    # Report
    print(f"parsed {len(skills)} skills")
    print(f"{'skill':<25s} {'units':>6s} {'scripts':>8s} {'refs':>5s} {'name':<30s}")
    print("-" * 90)
    for s in skills:
        print(f"{s['skill_id'].replace('anthropic_',''):<25s} {s['n_units']:>6d} "
              f"{s['n_scripts']:>8d} {s['n_refs']:>5d} {s['name'][:30]:<30s}")
    print()
    print(f"Total procedural units: {sum(s['n_units'] for s in skills)}")
    print(f"Total scripts:          {sum(s['n_scripts'] for s in skills)}")
    print(f"Total scripts with argparse/click: "
          f"{sum(1 for s in skills for sc in s['scripts'] if sc['signature'].get('args'))}")


if __name__ == "__main__":
    main()
