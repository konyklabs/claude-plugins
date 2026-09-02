#!/usr/bin/env python3
"""Inventory a pytest suite without running it.

Parses every test module and conftest under the given roots with ``ast`` and
reports what a decomposition needs to know: tests and fixtures per file,
where each fixture is defined and used, duplicated names, markers (and which
are unregistered, when a pytest config is found), the largest files, and a
first cut of slices by directory with the fixtures those slices share.

Usage:
    inventory.py [ROOT ...] [--json] [--config pyproject.toml|pytest.ini|setup.cfg]

Defaults: ROOT is ``tests`` if it exists, else ``.``; the config is searched
upward from the first root. Output is markdown unless ``--json``.

Standard library only. Deterministic: same tree, same output, so the report
can be diffed between sessions.
"""
from __future__ import annotations

import ast
import configparser
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

TEST_FILE_RE = re.compile(r"^(test_.*|.*_test)\.py$")
# Directories pytest itself never collects from, plus the usual noise.
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", ".nox", "build", "dist", ".pytest_cache", ".mypy_cache"}


# --------------------------------------------------------------------------- parsing


def _decorator_name(d: ast.expr) -> str:
    """'pytest.mark.slow' for @pytest.mark.slow / @pytest.mark.slow(...); 'fixture' for @pytest.fixture."""
    if isinstance(d, ast.Call):
        d = d.func
    parts: List[str] = []
    while isinstance(d, ast.Attribute):
        parts.append(d.attr)
        d = d.value
    if isinstance(d, ast.Name):
        parts.append(d.id)
    return ".".join(reversed(parts))


def _kw(d: ast.expr, name: str) -> Optional[Any]:
    if isinstance(d, ast.Call):
        for k in d.keywords:
            if k.arg == name and isinstance(k.value, ast.Constant):
                return k.value.value
    return None


def _param_count(d: ast.expr) -> Optional[int]:
    """Number of cases in @pytest.mark.parametrize(names, [..]) when literal."""
    if isinstance(d, ast.Call) and len(d.args) >= 2 and isinstance(d.args[1], (ast.List, ast.Tuple)):
        return len(d.args[1].elts)
    return None


def parse_module(path: Path, root: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    info: Dict[str, Any] = {
        "path": str(path.relative_to(root)) if root in path.parents or path == root else str(path),
        "lines": text.count("\n") + 1,
        "tests": [],
        "fixtures": [],
        "markers": defaultdict(int),
        "imports": [],
        "classes": [],
        "error": None,
    }
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        info["error"] = f"syntax error line {e.lineno}"
        return info

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                info["imports"] += [a.name for a in node.names]
            else:
                mod = ("." * node.level) + (node.module or "")
                info["imports"].append(mod)

    def visit_function(fn: ast.FunctionDef | ast.AsyncFunctionDef, cls: Optional[str]) -> None:
        decos = [(_decorator_name(d), d) for d in fn.decorator_list]
        names = [n for n, _ in decos]
        args = [a.arg for a in fn.args.args + fn.args.kwonlyargs if a.arg not in ("self", "cls")]
        if any(n in ("pytest.fixture", "fixture") for n in names):
            d = next(d for n, d in decos if n in ("pytest.fixture", "fixture"))
            info["fixtures"].append({
                "name": fn.name,
                "line": fn.lineno,
                "scope": _kw(d, "scope") or "function",
                "autouse": bool(_kw(d, "autouse")),
                "uses": args,
            })
            return
        if fn.name.startswith("test"):
            marks = [n.split(".", 2)[2] if n.startswith("pytest.mark.") else n[5:] for n in names if n.startswith("pytest.mark.") or n.startswith("mark.")]
            cases = 1
            for n, d in decos:
                if n.endswith("parametrize"):
                    c = _param_count(d)
                    if c:
                        cases *= c
            for m in marks:
                info["markers"][m] += 1
            info["tests"].append({
                "name": f"{cls}::{fn.name}" if cls else fn.name,
                "line": fn.lineno,
                "fixtures": args,
                "markers": marks,
                "cases": cases,
                "async": isinstance(fn, ast.AsyncFunctionDef),
            })

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visit_function(node, None)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            info["classes"].append(node.name)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit_function(sub, node.name)
    info["markers"] = dict(info["markers"])
    return info


def iter_test_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if root.is_file():
            yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for f in sorted(filenames):
                if f == "conftest.py" or TEST_FILE_RE.match(f):
                    yield Path(dirpath) / f


# --------------------------------------------------------------------------- config


def find_config(start: Path) -> Optional[Path]:
    for d in [start, *start.parents]:
        for name in ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"):
            p = d / name
            if p.exists():
                if name == "pyproject.toml" and "[tool.pytest" not in p.read_text(errors="replace"):
                    continue
                return p
    return None


def registered_markers(cfg: Optional[Path]) -> Optional[Set[str]]:
    """Marker names declared in the pytest config; None when no config was found."""
    if not cfg:
        return None
    text = cfg.read_text(errors="replace")
    names: Set[str] = set()
    if cfg.name == "pyproject.toml":
        m = re.search(r"markers\s*=\s*\[(.*?)\]", text, re.S)
        if m:
            for item in re.findall(r"[\"']([^\"']+)[\"']", m.group(1)):
                names.add(item.split(":")[0].split("(")[0].strip())
        return names
    cp = configparser.ConfigParser()
    try:
        cp.read_string(text)
    except configparser.Error:
        return names
    for section in ("pytest", "tool:pytest"):
        if cp.has_option(section, "markers"):
            for line in cp.get(section, "markers").splitlines():
                line = line.strip()
                if line:
                    names.add(line.split(":")[0].split("(")[0].strip())
    return names


# --------------------------------------------------------------------------- analysis


def analyse(roots: List[Path], config: Optional[Path]) -> Dict[str, Any]:
    base = roots[0] if roots[0].is_dir() else roots[0].parent
    modules = [parse_module(p, base) for p in iter_test_files(roots)]

    fixture_defs: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    fixture_uses: Dict[str, int] = defaultdict(int)
    test_names: Dict[str, List[str]] = defaultdict(list)
    markers: Dict[str, int] = defaultdict(int)
    total_tests = total_cases = 0
    for m in modules:
        for f in m["fixtures"]:
            fixture_defs[f["name"]].append({"file": m["path"], "line": f["line"], "scope": f["scope"], "autouse": f["autouse"]})
        for t in m["tests"]:
            total_tests += 1
            total_cases += t["cases"]
            test_names[t["name"].split("::")[-1]].append(m["path"])
            for fx in t["fixtures"]:
                fixture_uses[fx] += 1
        for f in m["fixtures"]:
            for fx in f["uses"]:
                fixture_uses[fx] += 1
        for k, v in m["markers"].items():
            markers[k] += v

    registered = registered_markers(config)
    builtin_marks = {"parametrize", "skip", "skipif", "xfail", "usefixtures", "filterwarnings", "asyncio", "anyio", "timeout", "django_db"}
    unregistered = sorted(k for k in markers if registered is not None and k not in registered and k not in builtin_marks)

    # Slices: one per top-level directory under the base (files at the base level form their own slice).
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for m in modules:
        parts = Path(m["path"]).parts
        key = parts[0] if len(parts) > 1 else "."
        groups[key].append(m)
    slices = []
    group_fixture_use: Dict[str, Set[str]] = {}
    for key, mods in sorted(groups.items()):
        used: Set[str] = set()
        for m in mods:
            for t in m["tests"]:
                used.update(t["fixtures"])
        group_fixture_use[key] = used
        slices.append({
            "slice": key,
            "files": [m["path"] for m in mods],
            "tests": sum(len(m["tests"]) for m in mods),
            "lines": sum(m["lines"] for m in mods),
            "command": f"pytest -q {base.name}/{key}" if key != "." else f"pytest -q {base.name} --ignore-glob='{base.name}/*/'",
        })
    shared = sorted(
        fx for fx in fixture_defs
        if sum(1 for used in group_fixture_use.values() if fx in used) > 1
    )

    return {
        "roots": [str(r) for r in roots],
        "config": str(config) if config else None,
        "totals": {"files": len(modules), "tests": total_tests, "cases": total_cases, "fixtures": sum(len(v) for v in fixture_defs.values()), "lines": sum(m["lines"] for m in modules)},
        "files": modules,
        "fixtures": {name: {"defs": defs, "used_by": fixture_uses.get(name, 0)} for name, defs in sorted(fixture_defs.items())},
        "duplicates": {
            "fixtures": {n: [d["file"] for d in defs] for n, defs in sorted(fixture_defs.items()) if len(defs) > 1},
            "tests": {n: files for n, files in sorted(test_names.items()) if len(files) > 1},
        },
        "unused_fixtures": sorted(n for n, defs in fixture_defs.items() if fixture_uses.get(n, 0) == 0 and not any(d["autouse"] for d in defs)),
        "markers": {"counts": dict(sorted(markers.items())), "registered": sorted(registered) if registered is not None else None, "unregistered": unregistered},
        "largest": sorted(({"path": m["path"], "lines": m["lines"], "tests": len(m["tests"])} for m in modules), key=lambda x: (-x["lines"], x["path"]))[:10],
        "errors": [{"path": m["path"], "error": m["error"]} for m in modules if m["error"]],
        "slices": slices,
        "shared_fixtures": shared,
    }


# --------------------------------------------------------------------------- rendering


def render_markdown(r: Dict[str, Any]) -> str:
    t = r["totals"]
    out = [f"# Test suite inventory: {', '.join(r['roots'])}", ""]
    out.append(f"{t['files']} files · {t['tests']} tests ({t['cases']} cases with parametrize) · {t['fixtures']} fixture definitions · {t['lines']} lines · config: {r['config'] or 'none found'}")
    out += ["", "## Slices (first cut, by directory)", "", "| slice | files | tests | lines | command |", "|---|---:|---:|---:|---|"]
    for s in r["slices"]:
        out.append(f"| {s['slice']} | {len(s['files'])} | {s['tests']} | {s['lines']} | `{s['command']}` |")
    if r["shared_fixtures"]:
        out += ["", "Fixtures used by more than one slice (level-0 candidates): " + ", ".join(f"`{f}`" for f in r["shared_fixtures"])]
    if r["duplicates"]["fixtures"]:
        out += ["", "## Fixture names defined in more than one file", ""]
        for n, files in r["duplicates"]["fixtures"].items():
            out.append(f"- `{n}`: " + ", ".join(files))
    if r["duplicates"]["tests"]:
        out += ["", "## Test names defined in more than one file", ""]
        for n, files in list(r["duplicates"]["tests"].items())[:25]:
            out.append(f"- `{n}`: " + ", ".join(files))
    if r["unused_fixtures"]:
        out += ["", "## Fixtures defined but never requested by name", "", ", ".join(f"`{f}`" for f in r["unused_fixtures"])]
    mk = r["markers"]
    if mk["counts"]:
        out += ["", "## Markers", "", ", ".join(f"`{k}` ×{v}" for k, v in mk["counts"].items())]
        if mk["unregistered"]:
            out.append("Unregistered (fail under --strict-markers): " + ", ".join(f"`{m}`" for m in mk["unregistered"]))
    out += ["", "## Largest files", "", "| file | lines | tests |", "|---|---:|---:|"]
    for f in r["largest"]:
        out.append(f"| {f['path']} | {f['lines']} | {f['tests']} |")
    if r["errors"]:
        out += ["", "## Files that did not parse", ""] + [f"- {e['path']}: {e['error']}" for e in r["errors"]]
    return "\n".join(out) + "\n"


def main(argv: List[str]) -> int:
    as_json = "--json" in argv
    args = [a for a in argv if a != "--json"]
    config: Optional[Path] = None
    if "--config" in args:
        i = args.index("--config")
        config = Path(args[i + 1])
        del args[i:i + 2]
    roots = [Path(a) for a in args] or [Path("tests") if Path("tests").exists() else Path(".")]
    for r in roots:
        if not r.exists():
            print(f"inventory: {r} does not exist", file=sys.stderr)
            return 2
    if config is None:
        config = find_config(roots[0].resolve() if roots[0].is_dir() else roots[0].resolve().parent)
    result = analyse([r.resolve() for r in roots], config)
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        sys.stdout.write(render_markdown(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
