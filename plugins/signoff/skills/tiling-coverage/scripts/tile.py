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

Exit 0 on valid input (a rule naming an unknown screen or flow, and a test
claiming a tile nothing defines, are warnings on stderr, not failures), 2 on
an input that cannot be read or that carries a field of the wrong type.

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
# The metadata block ends at the first `## ` heading, so a `- status:` line
# written in the prose after the steps table is prose. cases.py draws the
# block the same way and keeps the first value of a repeated key; the two
# parsers must agree, or a case would tile differently from how it lints.
CASE_HEADING_RE = re.compile(r"^##[ \t]+")

# formats.md: `.qa/tests.json` records a disabled test. A test that cannot run
# proves nothing, so its claims are listed apart and never make a tile covered.
SKIPPED_TEST_KEY = "skipped"

# The suffix a gap carries when the only thing claiming it is disabled. The
# reader needs to know the tile is not merely unwritten but falsely claimed.
DISABLED_NOTE = " (claimed by a disabled test: %s)"


# CLAUDE.md: a string that originates in a scanned repository is sanitized,
# length-capped and origin-marked before it reaches a report, because the
# report is untrusted input to the model that reads it. A newline inside an
# id is how a fake `## Coverage` heading gets into a Markdown report.
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# An id or an area: past the longest real one, still one line on a screen.
ID_CHARS = 120
# A title or a path, which legitimately runs longer than an id.
TEXT_CHARS = 200
# The cut is marked, so a value that was truncated never reads as a whole one.
CUT_MARK = "\u2026"


def _safe(value, cap=ID_CHARS):
    """One repository-born string, fit to print: control characters and
    newlines replaced, length capped, the cut marked."""
    text = CONTROL_RE.sub("?", str(value))
    return text if len(text) <= cap else text[:cap - 1] + CUT_MARK


# formats.md's ids are lower-case dotted slugs. Checked rather than trusted:
# an id is printed into a Markdown report and joined into file names, so one
# carrying a slash, a backtick or a newline is refused at the door.
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")

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
            "tests": [], "skipped_tests": [], "cases": [], "status": "uncovered"}


def build_tiles(mapping: Dict[str, Any], rules: Dict[str, Any],
                warnings: List[str], rules_path: str) -> List[Dict[str, Any]]:
    """Every rule, every screen x role, every error state - in that order."""
    screens = [s for s in mapping.get("screens") or [] if isinstance(s, dict)]
    screen_ids = {_safe(s.get("id")) for s in screens}
    flow_ids = {_safe(f.get("id")) for f in mapping.get("flows") or [] if isinstance(f, dict)}

    tiles: List[Dict[str, Any]] = []
    seen: Dict[str, str] = {}

    def add(tile: Dict[str, Any]) -> None:
        if not SAFE_ID_RE.match(tile["id"]):
            # Dropped, not renamed: a tile whose id this version cannot vouch
            # for would be printed into a Markdown report and named in a case.
            warnings.append("%s:0 bad-tile-id: %s is not %s and was dropped"
                            % (rules_path, tile["id"], SAFE_ID_RE.pattern))
            return
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
        rule_id = _safe(rule["id"])
        risk = _safe(rule.get("risk") or RISK_BY_RULE_KIND.get(str(rule.get("kind")), UNKNOWN_RISK))
        flow = _safe(rule["flow"]) if rule.get("flow") else ""
        if flow and flow not in flow_ids:
            warnings.append("%s:0 unknown-flow: rule %s names flow %s" % (rules_path, rule_id, flow))
        for screen in rule.get("screens") or []:
            if _safe(screen) not in screen_ids:
                warnings.append("%s:0 unknown-screen: rule %s names screen %s"
                                % (rules_path, rule_id, _safe(screen)))
        add(_tile(rule_id, _safe(rule.get("area") or ""), "rule", risk,
                  flow or None, rule_id, None, None))

    for screen in screens:
        screen_id = _safe(screen.get("id"))
        area = _safe(screen.get("area") or "")
        for role in screen.get("roles") or []:
            role = _safe(role)
            add(_tile(_safe("%s.render.%s" % (screen_id, role)), area, "render", RENDER_RISK,
                      None, None, screen_id, role))

    for screen in screens:
        screen_id = _safe(screen.get("id"))
        area = _safe(screen.get("area") or "")
        for state in screen.get("states") or []:
            text = _safe(state)
            if not text.startswith(ERROR_STATE_PREFIX):
                continue
            slug = text[len(ERROR_STATE_PREFIX):]
            add(_tile(_safe("%s.error.%s" % (screen_id, slug)), area, "error", ERROR_RISK,
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
            case_id = _safe(os.path.splitext(filename)[0])
            tiles: List[str] = []
            status = ""
            seen_keys = set()
            title_at = None
            for index, line in enumerate(lines):
                title = CASE_TITLE_RE.match(line)
                if title:
                    case_id = _safe(title.group(1))
                    title_at = index
                    break
            for line in lines[(title_at + 1) if title_at is not None else 0:]:
                if CASE_HEADING_RE.match(line):
                    break
                meta = CASE_META_RE.match(line)
                if not meta or meta.group(1) in seen_keys:
                    continue
                seen_keys.add(meta.group(1))
                if meta.group(1) == "tiles":
                    tiles = [_safe(t.strip()) for t in meta.group(2).split(",") if t.strip()]
                else:
                    status = _safe(meta.group(2).strip())
            cases.append({"id": case_id, "tiles": tiles, "status": status, "path": path})
    return cases


def overlay(tiles: List[Dict[str, Any]], tests: List[Dict[str, Any]],
            cases: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Set `tests`, `skipped_tests`, `cases` and `status` on every tile.

    Returns the claims on tiles nothing defines, in the order they were read:
    a renamed rule leaves its old id claimed by a test that now proves
    nothing, and dropping that silently is how a gap hides.
    """
    by_id = {tile["id"]: tile for tile in tiles}
    unknown: List[Dict[str, str]] = []
    unknown_seen = set()
    for test in tests:
        if not isinstance(test, dict):
            continue
        # A disabled test's claims go to `skipped_tests`, never to `tests`,
        # so `status` below cannot be computed as covered from one.
        field = "skipped_tests" if test.get(SKIPPED_TEST_KEY) else "tests"
        test_id = _safe(test.get("id"), TEXT_CHARS)
        for tile_id in test.get("tiles") or []:
            tile = by_id.get(_safe(tile_id))
            if tile is None:
                key = (test_id, _safe(tile_id))
                if key not in unknown_seen:
                    unknown_seen.add(key)
                    unknown.append({"test": key[0], "tile": key[1]})
                continue
            if test_id not in tile[field]:
                tile[field].append(test_id)
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
    return unknown


def rank_gaps(tiles: List[Dict[str, Any]]) -> List[str]:
    """Uncovered ids by risk, then kind, then id (formats.md)."""
    gaps = [t for t in tiles if t["status"] == "uncovered"]
    gaps.sort(key=lambda t: (RISK_ORDER.get(t["risk"], RISK_ORDER[UNKNOWN_RISK]),
                             KIND_ORDER.get(t["kind"], len(KIND_ORDER)), t["id"]))
    return [t["id"] for t in gaps]


def check_shapes(mapping: Dict[str, Any], map_path: str,
                 rules: Dict[str, Any], rules_path: str, findings: List[str]) -> None:
    """Every list this script walks, checked before it is walked.

    Fail closed: a field of the wrong type is a `path:line rule` finding and
    exit 2, never an AttributeError or a TypeError from the middle of a walk.
    """

    def want_list(value: Any, path: str, what: str) -> None:
        if value is not None and not isinstance(value, list):
            findings.append("%s:0 bad-field: %s is not a list" % (path, what))

    want_list(mapping.get("flows"), map_path, "flows")
    for screen in mapping.get("screens") or []:
        if not isinstance(screen, dict):
            findings.append("%s:0 bad-field: a screen is not an object" % map_path)
            continue
        name = str(screen.get("id", "<unnamed>"))
        # `actions` is checked with the two this script walks: a map whose
        # actions are not a list is malformed wherever it is read next.
        for field in ("roles", "actions", "states"):
            want_list(screen.get(field), map_path, "%s of screen %s" % (field, name))
    if isinstance(mapping.get("flows"), list):
        for flow in mapping["flows"]:
            if not isinstance(flow, dict):
                findings.append("%s:0 bad-field: a flow is not an object" % map_path)
    for rule in rules.get("rules") or []:
        if not isinstance(rule, dict):
            continue          # build_tiles already reports a rule that is not an object
        want_list(rule.get("screens"), rules_path,
                  "screens of rule %s" % str(rule.get("id", "<unnamed>")))


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
    # One percent rule (formats.md): round to the nearest whole, 0 with no tiles.
    percent = int(round(100.0 * covered / total)) if total else 0
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
        disabled = tile.get("skipped_tests") or []
        lines.append("%d. `%s` (%s, %s risk)%s"
                     % (position, tile_id, tile["kind"], tile["risk"],
                        DISABLED_NOTE % disabled[0] if disabled else ""))
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
    check_shapes(mapping, args.map_path, rules, args.rules_path, findings)
    if findings:
        for finding in findings:
            print("tile.py: %s" % finding, file=sys.stderr)
        return 2

    warnings: List[str] = []
    tiles = build_tiles(mapping, rules, warnings, args.rules_path)
    unknown_claims = overlay(tiles, tests_file.get("tests") or [], cases)
    for claim in unknown_claims:
        warnings.append("warning: %s claims unknown tile %s" % (claim["test"], claim["tile"]))
    gaps = rank_gaps(tiles)

    payload = {"tiled_at": _now(), "tiles": tiles, "gaps": gaps,
               "unknown_claims": unknown_claims}
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
