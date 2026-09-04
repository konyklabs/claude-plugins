#!/usr/bin/env python3
"""List an application's end-to-end tests and the tiles each one claims.

Two stacks:

* ``playwright-ts`` reads Playwright's JSON reporter output, either a saved
  report (``--input``) or a fresh listing (``--run``, which runs
  ``npx playwright test --list --reporter=json``: an external tool expected
  on PATH, never installed, and this script itself opens no socket).
* ``pytest`` parses the suite with ``ast``, without importing or running it.

The output is ``.qa/tests.json`` exactly as ``plugins/signoff/formats.md``
describes it. That file is the only home of the id and file formats; this
script does not restate them.

Usage:
    tests.py --stack playwright-ts (--input REPORT.json | --run) [--cwd DIR] --out .qa/tests.json
    tests.py --stack pytest TESTS_DIR [--cwd DIR] --out .qa/tests.json

Standard library only, no network. Deterministic: the same input yields the
same file apart from ``listed_at``.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Playwright's own listing command. Recorded in the output so a reader knows
# how the listing was produced (formats.md, `.qa/tests.json`).
LIST_COMMAND = ["npx", "playwright", "test", "--list", "--reporter=json"]

# Listing loads and type-checks every spec file. Two minutes is well past a
# cold TypeScript compile of a few hundred specs and still bounded, so a hung
# `npx` fails closed instead of holding the session open.
LIST_TIMEOUT_SECONDS = 120

# pytest's own default collection patterns (`python_files`), so this script
# reads the files pytest reads.
TEST_FILE_RE = re.compile(r"^(test_.*|.*_test)\.py$")

# pytest's default `python_classes`. A class outside this prefix is skipped:
# listing a method pytest never collects would mark a tile covered by a test
# that never runs, and over-claiming coverage is the one failure this tool
# must not have. A suite that overrides `python_classes` under-reports, which
# leaves the tile in the gap list instead.
TEST_CLASS_PREFIX = "Test"

# pytest's default `python_functions`.
TEST_FUNCTION_PREFIX = "test"

# Directories pytest never collects from, plus the usual noise.
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", ".nox",
             "build", "dist", ".pytest_cache", ".mypy_cache"}

# A claim carried by a tag. Measured 2026-09-04 (formats.md): Playwright
# strips the leading `@` from the tags it reports, whether the tag was given
# in the title or through `tag:`, so both spellings are accepted rather than
# trusting one observation of one version.
TILE_TAG_RE = re.compile(r"^@?tile:(.+)$")

# The annotation type formats.md reserves for a claim.
TILE_ANNOTATION_TYPE = "tile"

# A tag token inside a title: whitespace-delimited and starting with `@`.
# The id uses the title with these removed (formats.md, "Identifiers").
TITLE_TAG_RE = re.compile(r"(?:^|\s)@\S+")

# The decorator spellings that claim a tile in pytest: the full path, the
# `from pytest import mark` form, and a bare `tile` imported by name.
TILE_DECORATORS = {"pytest.mark.tile", "mark.tile", "tile"}

# UTC in the shape formats.md uses (`2026-09-04T18:20:00Z`).
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _now() -> str:
    return time.strftime(TIMESTAMP_FORMAT, time.gmtime())


def _strip_tag_tokens(title: str) -> str:
    """The title without its `@` tokens, collapsed and stripped."""
    return re.sub(r"\s{2,}", " ", TITLE_TAG_RE.sub("", title)).strip()


def _tiles_from(tags: Sequence[Any], annotations: Sequence[Dict[str, Any]]) -> List[str]:
    """Tile ids claimed by these tags and annotations, first appearance kept."""
    tiles: List[str] = []
    for tag in tags:
        match = TILE_TAG_RE.match(str(tag).strip())
        if match:
            tile = match.group(1).strip()
            if tile and tile not in tiles:
                tiles.append(tile)
    for annotation in annotations:
        if annotation.get("type") != TILE_ANNOTATION_TYPE:
            continue
        tile = (annotation.get("description") or "").strip()
        if tile and tile not in tiles:
            tiles.append(tile)
    return tiles


# --------------------------------------------------------------------------- playwright


def _walk_specs(suite: Dict[str, Any], found: List[Dict[str, Any]]) -> None:
    """Every spec of this suite and of the suites below it, in report order."""
    for spec in suite.get("specs") or []:
        if isinstance(spec, dict):
            found.append(spec)
    for child in suite.get("suites") or []:
        if isinstance(child, dict):
            _walk_specs(child, found)


def _spec_annotations(spec: Dict[str, Any]) -> List[Dict[str, str]]:
    """The union of the annotations over the spec's tests, first appearance kept.

    A spec run under several projects is one test here, so the projects'
    annotations merge. Result annotations are included because an annotation
    pushed at run time is reported there as well as on the test; a listing has
    no results and is unaffected. Only `type` and `description` are kept:
    that is the shape formats.md gives, and the `location` Playwright adds
    carries absolute paths from the machine that ran it.
    """
    seen: List[Dict[str, str]] = []
    keys = set()
    for test in spec.get("tests") or []:
        if not isinstance(test, dict):
            continue
        groups: List[Any] = [test.get("annotations") or []]
        for result in test.get("results") or []:
            if isinstance(result, dict):
                groups.append(result.get("annotations") or [])
        for group in groups:
            for annotation in group:
                if not isinstance(annotation, dict):
                    continue
                description = annotation.get("description")
                item = {"type": str(annotation.get("type", "")),
                        "description": "" if description is None else str(description)}
                key = (item["type"], item["description"])
                if key not in keys:
                    keys.add(key)
                    seen.append(item)
    return seen


def parse_playwright(report: Dict[str, Any], cwd: str) -> List[Dict[str, Any]]:
    """One entry per spec; a spec with several projects is one test."""
    config = report.get("config") or {}
    root_dir = config.get("rootDir") or ""
    specs: List[Dict[str, Any]] = []
    for suite in report.get("suites") or []:
        if isinstance(suite, dict):
            _walk_specs(suite, specs)

    tests: List[Dict[str, Any]] = []
    for spec in specs:
        spec_file = spec.get("file") or ""
        # `file` is relative to `config.rootDir` (measured, formats.md). A
        # report without a rootDir is read as relative to --cwd, which is the
        # directory such a report was produced in.
        absolute = os.path.join(root_dir or cwd, spec_file)
        relative = os.path.relpath(absolute, cwd)
        title = _strip_tag_tokens(str(spec.get("title") or ""))
        tags = list(spec.get("tags") or [])
        annotations = _spec_annotations(spec)
        tests.append({
            "id": relative + "::" + title,
            "title": title,
            "file": relative,
            "line": spec.get("line") or 0,
            "tags": tags,
            "annotations": annotations,
            "tiles": _tiles_from(tags, annotations),
        })
    return tests


def run_playwright_list(cwd: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """The report from a live listing, or (None, reason) - never a guess."""
    if shutil.which(LIST_COMMAND[0]) is None:
        return None, "skip: %s is not on PATH; run the listing where the suite lives" % LIST_COMMAND[0]
    try:
        finished = subprocess.run(LIST_COMMAND, cwd=cwd, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=LIST_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return None, "skip: %s did not finish in %ds" % (" ".join(LIST_COMMAND), LIST_TIMEOUT_SECONDS)
    except OSError as error:
        return None, "skip: cannot run %s (%s)" % (LIST_COMMAND[0], error)
    text = finished.stdout.decode("utf-8", errors="replace")
    report = _parse_report_text(text)
    if report is None:
        return None, "skip: %s exited %d without a JSON report" % (LIST_COMMAND[0], finished.returncode)
    return report, None


def _parse_report_text(text: str) -> Optional[Dict[str, Any]]:
    """The JSON object in this output, tolerating a wrapper npx may print."""
    try:
        parsed = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start:end + 1])
        except ValueError:
            return None
    return parsed if isinstance(parsed, dict) else None


# --------------------------------------------------------------------------- pytest


def _decorator_name(node: ast.expr) -> str:
    """'pytest.mark.tile' for @pytest.mark.tile(...); 'tile' for @tile(...)."""
    if isinstance(node, ast.Call):
        node = node.func
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _marks(node: ast.expr) -> Tuple[List[str], List[str], bool]:
    """(tile ids, other marker names, a tile claim with no id) of one function."""
    tiles: List[str] = []
    tags: List[str] = []
    empty_claim = False
    for decorator in getattr(node, "decorator_list", []):
        name = _decorator_name(decorator)
        if name in TILE_DECORATORS:
            arguments = decorator.args if isinstance(decorator, ast.Call) else []
            found = False
            for argument in arguments:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    found = True
                    if argument.value not in tiles:
                        tiles.append(argument.value)
            if not found:
                empty_claim = True
            continue
        parts = name.split(".")
        if len(parts) >= 2 and parts[-2] == "mark" and parts[-1] not in tags:
            tags.append(parts[-1])
    return tiles, tags, empty_claim


def _collect(body: Sequence[ast.stmt], prefix: List[str], relative: str,
             tests: List[Dict[str, Any]], warnings: List[str]) -> None:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith(TEST_FUNCTION_PREFIX):
                continue
            tiles, tags, empty_claim = _marks(node)
            if empty_claim:
                warnings.append("%s:%d empty-tile-claim: a tile mark with no id claims nothing"
                                % (relative, node.lineno))
            tests.append({
                "id": "::".join([relative] + prefix + [node.name]),
                "title": node.name,
                "file": relative,
                "line": node.lineno,
                "tags": tags,
                "annotations": [],
                "tiles": tiles,
            })
        elif isinstance(node, ast.ClassDef) and node.name.startswith(TEST_CLASS_PREFIX):
            _collect(node.body, prefix + [node.name], relative, tests, warnings)


def parse_pytest_tree(root: str, cwd: str, warnings: List[str]) -> List[Dict[str, Any]]:
    tests: List[Dict[str, Any]] = []
    for directory, subdirectories, filenames in os.walk(root):
        subdirectories[:] = sorted(d for d in subdirectories if d not in SKIP_DIRS)
        for filename in sorted(filenames):
            if not TEST_FILE_RE.match(filename):
                continue
            path = os.path.join(directory, filename)
            relative = os.path.relpath(path, cwd)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError as error:
                warnings.append("%s:0 unreadable: %s" % (relative, error))
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError as error:
                warnings.append("%s:%d unparsed: %s" % (relative, error.lineno or 0, error.msg))
                continue
            _collect(tree.body, [], relative, tests, warnings)
    return tests


# --------------------------------------------------------------------------- output


def write_json(path: str, payload: Dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", help="the tests directory (pytest stack)")
    parser.add_argument("--stack", required=True, choices=("playwright-ts", "pytest"))
    parser.add_argument("--input", help="a saved Playwright JSON report")
    parser.add_argument("--run", action="store_true", help="list the suite with npx")
    parser.add_argument("--cwd", default=None, help="paths are reported relative to this (default: the working directory)")
    parser.add_argument("--out", required=True, help="where to write tests.json")
    args = parser.parse_args(argv)

    cwd = os.path.abspath(args.cwd) if args.cwd else os.getcwd()
    warnings: List[str] = []

    if args.stack == "playwright-ts":
        if bool(args.input) == bool(args.run):
            print("tests.py: give exactly one of --input and --run", file=sys.stderr)
            return 2
        if args.run:
            report, reason = run_playwright_list(cwd)
            if report is None:
                print("tests.py: %s" % reason, file=sys.stderr)
                return 2
            command = " ".join(LIST_COMMAND)
        else:
            try:
                with open(args.input, "r", encoding="utf-8") as handle:
                    report = json.load(handle)
            except (OSError, ValueError) as error:
                print("tests.py: %s:0 unreadable: %s" % (args.input, error), file=sys.stderr)
                return 2
            if not isinstance(report, dict):
                print("tests.py: %s:0 malformed: not a JSON object" % args.input, file=sys.stderr)
                return 2
            command = "--input %s" % args.input
        tests = parse_playwright(report, cwd)
    else:
        if not args.path:
            print("tests.py: --stack pytest needs the tests directory", file=sys.stderr)
            return 2
        if not os.path.isdir(args.path):
            print("tests.py: %s:0 unreadable: not a directory" % args.path, file=sys.stderr)
            return 2
        tests = parse_pytest_tree(args.path, cwd, warnings)
        command = "--stack pytest %s" % args.path

    payload = {"stack": args.stack, "listed_at": _now(), "command": command, "tests": tests}
    try:
        write_json(args.out, payload)
    except OSError as error:
        print("tests.py: %s:0 unwritable: %s" % (args.out, error), file=sys.stderr)
        return 2

    for warning in warnings:
        print("tests.py: %s" % warning, file=sys.stderr)
    claimed = [t for t in tests if t["tiles"]]
    distinct = sorted({tile for t in tests for tile in t["tiles"]})
    print("%d tests, %d claiming %d tiles -> %s" % (len(tests), len(claimed), len(distinct), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
