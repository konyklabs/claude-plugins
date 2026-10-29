#!/usr/bin/env python3
"""Route-by-route fit check: does a vendorfake unit serve every call a consumer makes?

Two local inputs, no network, no subprocess:

- ``--routes``: the JSON array ``vendorfake routes --vendor V --json`` prints
  (objects carrying ``method``, ``path`` and ``operation_id``; other keys are
  ignored).
- ``--calls``: a text file, one ``METHOD /path[?query]`` per line, inventoried
  from the consumer's production code rather than its documentation. Blank
  lines and lines starting with ``#`` are ignored.

Each call gets one verdict: ``fit`` (a route serves it; its operation id is
printed), ``method`` (the path is served, but only under other methods, which
are listed) or ``missing``. The run exits 1 when any call is not a fit, 0 when
every call is, and 2 on a malformed input, naming the line.

Usage:
    python3 fit_check.py --routes routes.json --calls calls.txt [--mount VENDOR] [--json]

``--mount VENDOR`` strips a leading ``/VENDOR`` from every call, for a process
that mounts several vendors under ``/<vendor>/``. It is never guessed: a
prefixed call without it reads as ``missing``, which is the honest answer for
a base URL that is wrong.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# A vendorfake route template names a path parameter in braces, e.g. `{mId}`.
PLACEHOLDER_RE = re.compile(r"\{[^/{}]+\}")
# A path parameter matches exactly one path segment, the way the unit's router binds it.
SEGMENT = "[^/]+"
# A call line is an HTTP method token, whitespace, then the path; anything else is malformed.
CALL_RE = re.compile(r"^([A-Za-z]+)\s+(\S+)\s*$")

EXIT_FIT = 0
EXIT_NOT_FIT = 1
EXIT_BAD_INPUT = 2


class InputError(Exception):
    """A malformed routes or calls file; the message names where."""


def normalise(path: str) -> str:
    """Drop the query string and a trailing slash, so `/a/?x=1` and `/a` compare equal."""
    path = path.split("?", 1)[0].split("#", 1)[0]
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return path


def template_regex(template: str) -> "re.Pattern[str]":
    """An anchored regex for one route template, each placeholder one segment."""
    template = normalise(template)
    pieces = PLACEHOLDER_RE.split(template)
    return re.compile("^" + SEGMENT.join(re.escape(piece) for piece in pieces) + "$")


def load_routes(path: Path) -> List[Tuple[str, "re.Pattern[str]", str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InputError(f"{path}: not a readable JSON document ({exc.__class__.__name__})")
    if not isinstance(data, list):
        raise InputError(f"{path}: expected the JSON array `vendorfake routes --vendor V --json` prints")
    routes = []
    for index, row in enumerate(data):
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("method", "path", "operation_id")):
            raise InputError(f"{path}: entry {index} lacks a string method, path or operation_id")
        routes.append((row["method"].upper(), template_regex(row["path"]), row["operation_id"]))
    return routes


def load_calls(path: Path, mount: Optional[str]) -> List[Tuple[str, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise InputError(f"{path}: not readable ({exc.__class__.__name__})")
    prefix = "/" + mount.strip("/") if mount else None
    calls = []
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = CALL_RE.match(line)
        if match is None or not match.group(2).startswith("/"):
            raise InputError(f"{path}: line {number} is not `METHOD /path`")
        method, target = match.group(1).upper(), match.group(2)
        path_only = normalise(target)
        if prefix and (path_only == prefix or path_only.startswith(prefix + "/")):
            path_only = path_only[len(prefix):] or "/"
        calls.append((method, path_only))
    return calls


def judge(method: str, path: str, routes: List[Tuple[str, "re.Pattern[str]", str]]) -> Dict[str, Any]:
    same_method: List[str] = []
    other_methods: List[str] = []
    for route_method, pattern, operation_id in routes:
        if not pattern.match(path):
            continue
        if route_method == method:
            same_method.append(operation_id)
        elif route_method not in other_methods:
            other_methods.append(route_method)
    row: Dict[str, Any] = {"call": f"{method} {path}", "verdict": "missing", "operation_id": None, "methods": []}
    if same_method:
        row.update(verdict="fit", operation_id="|".join(same_method))
    elif other_methods:
        row.update(verdict="method", methods=sorted(other_methods))
    return row


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check a consumer's calls against a vendorfake route table.")
    parser.add_argument("--routes", required=True, type=Path, help="output of `vendorfake routes --vendor V --json`")
    parser.add_argument("--calls", required=True, type=Path, help="one `METHOD /path` per line")
    parser.add_argument("--mount", help="strip a leading /VENDOR from every call (multi-vendor mount)")
    parser.add_argument("--json", action="store_true", help="one JSON document on stdout")
    args = parser.parse_args(argv)
    try:
        routes = load_routes(args.routes)
        calls = load_calls(args.calls, args.mount)
    except InputError as exc:
        print(f"fit_check: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    rows = [judge(method, path, routes) for method, path in calls]
    counts = {verdict: sum(1 for r in rows if r["verdict"] == verdict) for verdict in ("fit", "method", "missing")}
    if args.json:
        print(json.dumps({"rows": rows, **counts}, indent=2))
    else:
        for r in rows:
            target = r["operation_id"] if r["verdict"] == "fit" else ", ".join(r["methods"]) if r["methods"] else "-"
            print(f"{r['verdict']:<8} {r['call']}  ->  {target}")
        print(f"fit_check: {counts['fit']} fit, {counts['method']} method-mismatch, {counts['missing']} missing")
    return EXIT_NOT_FIT if counts["method"] + counts["missing"] else EXIT_FIT


if __name__ == "__main__":
    sys.exit(main())
