#!/usr/bin/env python3
"""Build the coverage tiles from a map and a rule inventory, tests laid over them.

A tile is one thing that can be verified: a mined rule, a screen rendered for
a role, or an error state of a screen. This script builds them from
``.qa/map.json`` and ``.qa/rules.json``, marks each with the tests from
``.qa/tests.json`` that claim it and, with ``--cases``, the human-written
cases that name it, then ranks what is left uncovered.

``plugins/signoff/formats.md`` is the only home of the formats, the status
values and the gap order; this script does not restate them.

Usage:
    tile.py --map .qa/map.json --rules .qa/rules.json --tests .qa/tests.json
            [--cases testcases] --out .qa/tiles.json

Exit 0 on valid input (a rule naming an unknown screen or flow is a warning
on stderr, not a failure), 2 on an input that cannot be read.

Standard library only, no network.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

# formats.md: when a rule carries no `risk`, it is derived from its kind.
RISK_BY_RULE_KIND = {"guard": "high", "validation": "medium", "error": "medium",
                     "transition": "medium", "flag": "low", "calculation": "low"}

# Fail closed: a rule kind this version does not know is ranked at the top of
# the gap list rather than hidden at the bottom of it.
UNKNOWN_RISK = "high"

# A render tile asks only whether a role can see the screen at all - the
# cheapest check there is, so it ranks below every rule that is not itself
# low. Fixed here rather than in the map: the explorer records screens, not
# how much a screen matters.
RENDER_RISK = "low"

# An error state is a rule of kind `error` seen from the screen's side, and
# formats.md derives medium for that kind; the same value keeps the two
# consistent.
ERROR_RISK = "medium"

# formats.md: gaps are ordered by risk, then kind, then id.
RISK_ORDER = {"high": 0, "medium": 1, "low": 2}
KIND_ORDER = {"rule": 0, "error": 1, "render": 2}

# formats.md: the `states` prefix that becomes an error tile.
ERROR_STATE_PREFIX = "error:"

# formats.md: a case that is only ever run by hand.
MANUAL_CASE_STATUS = "manual"

# UTC in the shape formats.md uses (`2026-09-04T18:30:00Z`).
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# The three lines of a test case this script needs. The full parser and the
# lint live in `recording-test-cases/scripts/cases.py`; this is a deliberate
# ten-line duplicate, because a script in one skill must not import a script
# from another (each is run by path, nothing is a package) and only the id,
# the tiles and the status matter for tiling.
CASE_TITLE_RE = re.compile(r"^#\s*(TC-[A-Za-z0-9-]+-\d+)\s*:")
CASE_META_RE = re.compile(r"^-\s*(tiles|status)\s*:\s*(.*)$")
CASE_FILE_RE = re.compile(r"^TC-.*\.md$")


def _now() -> str:
    return time.strftime(TIMESTAMP_FORMAT, time.gmtime())


def read_json(path: str, key: str, findings: List[str]) -> Optional[Dict[str, Any]]:
    """The object at `path`, or None with a `path:line rule` finding recorded."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        findings.append("%s:0 unreadable: %s" % (path, error))
        return None
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        findings.append('%s:0 malformed: no "%s" list' % (path, key))
        return None
    return data


# --------------------------------------------------------------------------- tiles


def _tile(tile_id: str, area: str, kind: str, risk: str, flow: Optional[str],
          rule: Optional[str], screen: Optional[str], role: Optional[str]) -> Dict[str, Any]:
    return {"id": tile_id, "area": area, "kind": kind, "flow": flow, "rule": rule,
            "screen": screen, "role": role, "risk": risk,
            "tests": [], "cases": [], "status": "uncovered"}


def build_tiles(mapping: Dict[str, Any], rules: Dict[str, Any],
                warnings: List[str], rules_path: str) -> List[Dict[str, Any]]:
    """Every rule, every screen x role, every error state - in that order."""
    screens = [s for s in mapping.get("screens") or [] if isinstance(s, dict)]
    screen_ids = {str(s.get("id")) for s in screens}
    flow_ids = {str(f.get("id")) for f in mapping.get("flows") or [] if isinstance(f, dict)}

    tiles: List[Dict[str, Any]] = []
    seen: Dict[str, str] = {}

    def add(tile: Dict[str, Any]) -> None:
        if tile["id"] in seen:
            warnings.append("%s:0 duplicate-tile: %s is already a %s tile"
                            % (rules_path, tile["id"], seen[tile["id"]]))
            return
        seen[tile["id"]] = tile["kind"]
        tiles.append(tile)

    for rule in rules.get("rules") or []:
        if not isinstance(rule, dict) or not rule.get("id"):
            warnings.append("%s:0 malformed-rule: a rule with no id was skipped" % rules_path)
            continue
        rule_id = str(rule["id"])
        risk = rule.get("risk") or RISK_BY_RULE_KIND.get(str(rule.get("kind")), UNKNOWN_RISK)
        flow = rule.get("flow")
        if flow and str(flow) not in flow_ids:
            warnings.append("%s:0 unknown-flow: rule %s names flow %s" % (rules_path, rule_id, flow))
        for screen in rule.get("screens") or []:
            if str(screen) not in screen_ids:
                warnings.append("%s:0 unknown-screen: rule %s names screen %s"
                                % (rules_path, rule_id, screen))
        add(_tile(rule_id, str(rule.get("area") or ""), "rule", str(risk),
                  str(flow) if flow else None, rule_id, None, None))

    for screen in screens:
        screen_id = str(screen.get("id"))
        area = str(screen.get("area") or "")
        for role in screen.get("roles") or []:
            add(_tile("%s.render.%s" % (screen_id, role), area, "render", RENDER_RISK,
                      None, None, screen_id, str(role)))

    for screen in screens:
        screen_id = str(screen.get("id"))
        area = str(screen.get("area") or "")
        for state in screen.get("states") or []:
            text = str(state)
            if not text.startswith(ERROR_STATE_PREFIX):
                continue
            slug = text[len(ERROR_STATE_PREFIX):]
            add(_tile("%s.error.%s" % (screen_id, slug), area, "error", ERROR_RISK,
                      None, None, screen_id, None))
    return tiles


# --------------------------------------------------------------------------- overlays


def read_cases(directory: str, findings: List[str]) -> List[Dict[str, Any]]:
    """Each case's id, tiles and status - the three lines tiling needs."""
    cases: List[Dict[str, Any]] = []
    for parent, subdirectories, filenames in os.walk(directory):
        subdirectories[:] = sorted(subdirectories)
        for filename in sorted(filenames):
            if not CASE_FILE_RE.match(filename):
                continue
            path = os.path.join(parent, filename)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    lines = handle.read().splitlines()
            except OSError as error:
                findings.append("%s:0 unreadable: %s" % (path, error))
                continue
            case_id = os.path.splitext(filename)[0]
            tiles: List[str] = []
            status = ""
            for line in lines:
                title = CASE_TITLE_RE.match(line)
                if title:
                    case_id = title.group(1)
                    continue
                meta = CASE_META_RE.match(line)
                if not meta:
                    continue
                if meta.group(1) == "tiles":
                    tiles = [t.strip() for t in meta.group(2).split(",") if t.strip()]
                else:
                    status = meta.group(2).strip()
            cases.append({"id": case_id, "tiles": tiles, "status": status, "path": path})
    return cases


def overlay(tiles: List[Dict[str, Any]], tests: List[Dict[str, Any]],
            cases: List[Dict[str, Any]]) -> None:
    """Set `tests`, `cases` and `status` on every tile."""
    by_id = {tile["id"]: tile for tile in tiles}
    for test in tests:
        if not isinstance(test, dict):
            continue
        for tile_id in test.get("tiles") or []:
            tile = by_id.get(str(tile_id))
            if tile is not None and test.get("id") not in tile["tests"]:
                tile["tests"].append(test.get("id"))
    manual: Dict[str, bool] = {}
    for case in cases:
        for tile_id in case["tiles"]:
            tile = by_id.get(tile_id)
            if tile is None:
                continue
            if case["id"] not in tile["cases"]:
                tile["cases"].append(case["id"])
            if case["status"] == MANUAL_CASE_STATUS:
                manual[tile_id] = True
    for tile in tiles:
        if tile["tests"]:
            tile["status"] = "covered"
        elif manual.get(tile["id"]):
            tile["status"] = MANUAL_CASE_STATUS
        else:
            tile["status"] = "uncovered"


def rank_gaps(tiles: List[Dict[str, Any]]) -> List[str]:
    """Uncovered ids by risk, then kind, then id (formats.md)."""
    gaps = [t for t in tiles if t["status"] == "uncovered"]
    gaps.sort(key=lambda t: (RISK_ORDER.get(t["risk"], RISK_ORDER[UNKNOWN_RISK]),
                             KIND_ORDER.get(t["kind"], len(KIND_ORDER)), t["id"]))
    return [t["id"] for t in gaps]


# --------------------------------------------------------------------------- report


def render(tiles: List[Dict[str, Any]], gaps: List[str], out: str) -> str:
    counts: Dict[str, Dict[str, int]] = {}
    for tile in tiles:
        area = counts.setdefault(tile["area"], {"tiles": 0, "covered": 0, "manual": 0, "uncovered": 0})
        area["tiles"] += 1
        area[tile["status"]] += 1
    total = len(tiles)
    covered = sum(1 for t in tiles if t["status"] == "covered")
    manual = sum(1 for t in tiles if t["status"] == MANUAL_CASE_STATUS)
    percent = (100 * covered // total) if total else 0
    by_id = {tile["id"]: tile for tile in tiles}

    lines = ["# Tiles", "",
             "%d tiles: %d covered, %d manual, %d uncovered (%d%% covered) -> %s"
             % (total, covered, manual, total - covered - manual, percent, out),
             "", "| area | tiles | covered | manual | uncovered |", "|---|---:|---:|---:|---:|"]
    for area in sorted(counts):
        row = counts[area]
        lines.append("| %s | %d | %d | %d | %d |"
                     % (area, row["tiles"], row["covered"], row["manual"], row["uncovered"]))
    lines += ["", "## Gaps, ranked", ""]
    if not gaps:
        lines.append("None: every tile is covered or has a manual case.")
    for position, tile_id in enumerate(gaps, start=1):
        tile = by_id[tile_id]
        lines.append("%d. `%s` (%s, %s risk)" % (position, tile_id, tile["kind"], tile["risk"]))
    return "\n".join(lines) + "\n"


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--map", required=True, dest="map_path")
    parser.add_argument("--rules", required=True, dest="rules_path")
    parser.add_argument("--tests", required=True, dest="tests_path")
    parser.add_argument("--cases", default=None, help="the testcases directory")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    findings: List[str] = []
    mapping = read_json(args.map_path, "screens", findings)
    rules = read_json(args.rules_path, "rules", findings)
    tests_file = read_json(args.tests_path, "tests", findings)
    cases: List[Dict[str, Any]] = []
    if args.cases:
        if os.path.isdir(args.cases):
            cases = read_cases(args.cases, findings)
        else:
            findings.append("%s:0 unreadable: not a directory" % args.cases)
    if findings:
        for finding in findings:
            print("tile.py: %s" % finding, file=sys.stderr)
        return 2

    # Past the `findings` gate every input parsed, so none of the three is None.
    warnings: List[str] = []
    tiles = build_tiles(mapping, rules, warnings, args.rules_path)
    overlay(tiles, tests_file.get("tests") or [], cases)
    gaps = rank_gaps(tiles)

    payload = {"tiled_at": _now(), "tiles": tiles, "gaps": gaps}
    directory = os.path.dirname(os.path.abspath(args.out))
    try:
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
    except OSError as error:
        print("tile.py: %s:0 unwritable: %s" % (args.out, error), file=sys.stderr)
        return 2

    for warning in warnings:
        print("tile.py: %s" % warning, file=sys.stderr)
    sys.stdout.write(render(tiles, gaps, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
