#!/usr/bin/env python3
"""Write the sign-off coverage report from a tiles file.

The report is what a person reads at the end of a cycle and what a pull
request quotes: how much of the application is covered, where the gaps are,
what moved since the last sign-off, and which tests are failing over which
tiles. The file formats are in ``plugins/signoff/formats.md``; this script
implements them and does not restate them.

The gaps are ranked here from the tiles themselves, never taken from the
tiles file's ``gaps`` key, so a stale key cannot hide a gap from the report;
a key that disagrees earns one ``warning:`` line on stderr.

Usage:
    report.py --tiles .qa/tiles.json [--since <tiles.json> | --since-ref <git ref>]
              [--run <playwright json report>] --out testcases/coverage.md

The report goes to ``--out`` and to stdout. Exit 0 when it was written, 2 when
an input could not be read, carries a field of the wrong type, or - for
``--run`` - is a listing rather than a run (fail closed: a tiles file that
will not parse is never an empty report, and a file in which nothing ran is
never a green one).

``--since-ref`` reads the previous tiles file out of a commit with ``git show``.
Git is an external tool already on PATH; this script opens no socket itself,
and when the ref or the file is absent the diff is a `skip` line rather than a
failure, so an old sign-off tag that no longer exists never blocks a report.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

# --------------------------------------------------------------------------- constants

# The three statuses formats.md closes. Anything else is counted as uncovered
# and named in a note: an unknown status is never quietly credited as coverage.
COVERED = "covered"
MANUAL = "manual"
UNCOVERED = "uncovered"
STATUSES = (COVERED, MANUAL, UNCOVERED)

# The path formats.md fixes for the tiles file inside a commit, used when the
# --tiles argument is not itself a repository-relative path.
DEFAULT_TILES_IN_REPO = ".qa/tiles.json"

# `git show` reads a local object store, so it returns at once; thirty seconds
# is a bound on a hung command, not a budget for a slow one.
GIT_TIMEOUT = 30

# A tiles file or a run report for an application of any size stays well under
# this; the cap is here so an unbounded file cannot be read into memory.
MAX_JSON_BYTES = 32 * 1024 * 1024

# The report is read by a person and by a model. The first fifty ranked gaps
# are the ones anyone acts on this cycle, and the rest are counted rather than
# listed so a large or hostile tiles file cannot swamp the reader.
GAP_LIMIT = 50

# Same reasoning for a run whose whole suite went red: a hundred failing tests
# already say "the run is broken", and the rest are counted.
FAILING_LIMIT = 100

# Playwright's per-test statuses that mean a test did not simply pass;
# `flaky` is included because a tile a flaky test claims is not covered proof.
FAILING_STATUSES = ("unexpected", "flaky")

# `json` keeps no line numbers and a whole-file problem has no line, so every
# refusal points at line 0 - the `path:line rule: reason` shape the other
# scripts in this plugin use, so one reader parses all five.
NO_LINE = 0

# A `--list` capture has the run report's shape with every test `skipped` and
# `results: []` (measured 2026-09-04, formats.md). A listing therefore reports
# no failure, which would read as a green run: a report is only read when at
# least one test actually ran.
RESULTS_KEY = "results"

# A tag in a test title, `@tile:...` or `@smoke`, is not part of its id.
TAG_TOKEN_RE = re.compile(r"(?:^|\s)@\S+")

# formats.md's gap order, restated here because the scripts share no code:
# risk (high, medium, low), then kind (rule, error, render), then id. A risk
# this version does not know ranks at the top and a kind it does not know at
# the bottom, the same way tile.py fails closed on both.
RISK_ORDER = {"high": 0, "medium": 1, "low": 2}
KIND_ORDER = {"rule": 0, "error": 1, "render": 2}
UNKNOWN_RISK_RANK = RISK_ORDER["high"]

# What a gap says when the only thing claiming it is a disabled test. Worded
# and spelled exactly as tile.py's own summary spells it: the two are read
# side by side, and a tile falsely claimed is worse news than an unwritten one.
DISABLED_NOTE = " (claimed by a disabled test: %s)"

# The report's fixed skeleton, kept here so a reader of the script sees the
# shape of the file it writes.
TITLE = "# Coverage"


# --------------------------------------------------------------------------- input

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


def _fail(path, rule, reason):
    """Refuse an input in the one shape every script in this plugin prints."""
    sys.stderr.write("report.py: %s:%d %s: %s\n" % (path, NO_LINE, rule, reason))
    raise SystemExit(2)


def _load_json_path(path, what):
    try:
        if os.path.getsize(path) > MAX_JSON_BYTES:
            _fail(path, "too-large", "%s is larger than %d bytes" % (what, MAX_JSON_BYTES))
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except SystemExit:
        raise
    except (OSError, ValueError) as exc:
        _fail(path, "unreadable", "%s could not be read (%s)" % (what, exc.__class__.__name__))


# The fields this report reads off a tile, and the type each must have when
# it is present. A tile whose `status` is a number would be counted as
# uncovered and a tile whose `tests` is a string would be walked character by
# character: fail closed on both rather than report something plausible.
TILE_STRINGS = ("id", "status", "area", "kind", "risk")
TILE_LISTS = ("tests", "skipped_tests", "cases")


def _tiles_of(data, path):
    """The tiles list and the gaps list of a tiles document, validated."""
    if not isinstance(data, dict) or not isinstance(data.get("tiles"), list):
        _fail(path, "malformed", "no `tiles` list")
    tiles = []
    for tile in data["tiles"]:
        if not isinstance(tile, dict):
            _fail(path, "bad-field", "a tile is not an object")
        if not tile.get("id"):
            _fail(path, "malformed", "a tile has no id")
        for field in TILE_STRINGS:
            if tile.get(field) is not None and not isinstance(tile[field], str):
                _fail(path, "bad-field", "`%s` of a tile is not a string" % field)
        for field in TILE_LISTS:
            if tile.get(field) is not None and not isinstance(tile[field], list):
                _fail(path, "bad-field", "`%s` of a tile is not a list" % field)
        for field in TILE_STRINGS:
            if tile.get(field) is not None:
                tile[field] = _safe(tile[field])
        for field in TILE_LISTS:
            # A test id is a path joined to a title, so it is capped as one.
            tile[field] = [_safe(item, TEXT_CHARS) for item in tile.get(field) or []]
        tiles.append(tile)
    gaps = data.get("gaps") or []
    if not isinstance(gaps, list):
        _fail(path, "bad-field", "`gaps` is not a list")
    # Returned for the disagreement warning below, never to report from.
    return tiles, [gap for gap in gaps if isinstance(gap, str)]


def rank_gaps(tiles):
    """The gap list derived from the tiles themselves, in formats.md's order.

    Never the tiles file's own `gaps` key: that key is a claim about the
    tiles, and a stale or hand-edited one would hide a gap from the report
    that the tiles it sits beside plainly show. A tile counts as a gap when
    its status is neither covered nor manual, which is the same fail-closed
    reading the counts use.
    """
    gaps = [tile for tile in tiles if tile.get("status") not in (COVERED, MANUAL)]
    gaps.sort(key=lambda tile: (
        RISK_ORDER.get(tile.get("risk"), UNKNOWN_RISK_RANK),
        KIND_ORDER.get(tile.get("kind"), len(KIND_ORDER)),
        tile["id"]))
    return [tile["id"] for tile in gaps]


def _git_show(ref, path):
    """(bytes, None) or (None, reason). The reason never quotes git's output:
    tool text is untrusted input to whatever reads this report."""
    try:
        proc = subprocess.run(
            ["git", "show", "%s:%s" % (ref, path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=GIT_TIMEOUT)
    except OSError:
        return None, "git is not on PATH"
    except subprocess.TimeoutExpired:
        return None, "git show timed out after %d seconds" % GIT_TIMEOUT
    if proc.returncode != 0:
        return None, "`git show %s:%s` failed (exit %d): the ref or the file is absent" % (
            ref, path, proc.returncode)
    return proc.stdout, None


def _ref_path(tiles_path):
    """Which path to read inside a commit: the given one when it is relative
    to the repository, the format's default when it is absolute."""
    if os.path.isabs(tiles_path):
        return DEFAULT_TILES_IN_REPO
    return os.path.normpath(tiles_path).replace(os.sep, "/")


# --------------------------------------------------------------------------- counting


def _pct(part, total):
    """A whole percent. No tiles is 0%, never 100%: an empty map is not done."""
    if total <= 0:
        return "0%"
    # One percent rule (formats.md): round to the nearest whole, 0 with no tiles.
    return "%d%%" % int(round(100.0 * part / total))


def _counts(tiles):
    """(total, covered, manual, uncovered) for a list of tiles."""
    covered = sum(1 for tile in tiles if tile.get("status") == COVERED)
    manual = sum(1 for tile in tiles if tile.get("status") == MANUAL)
    # Everything that is not covered or manual counts as uncovered, including
    # a status this script does not know: fail closed.
    return len(tiles), covered, manual, len(tiles) - covered - manual


def _areas(tiles):
    groups = {}
    for tile in tiles:
        groups.setdefault(tile.get("area") or "unknown", []).append(tile)
    return groups


# --------------------------------------------------------------------------- the run report


def _clean_title(title):
    """A test's title without its `@` tokens, as formats.md defines the id."""
    return _safe(" ".join(TAG_TOKEN_RE.sub(" ", str(title)).split()), TEXT_CHARS)


def _walk_specs(node, found, path):
    """Every spec below this node. Each list and each object is type-checked
    before it is walked, so a hand-edited report is a refusal, not a
    TypeError from four levels down."""
    for key in ("specs", "suites"):
        if node.get(key) is not None and not isinstance(node[key], list):
            _fail(path, "bad-field", "`%s` is not a list" % key)
    for spec in node.get("specs") or []:
        if not isinstance(spec, dict):
            _fail(path, "bad-field", "a spec is not an object")
        found.append(spec)
    for child in node.get("suites") or []:
        if not isinstance(child, dict):
            _fail(path, "bad-field", "a suite is not an object")
        _walk_specs(child, found, path)


def failing_tests(run, path):
    """[(id, status)] for every test the run reports as unexpected or flaky."""
    if not isinstance(run, dict):
        _fail(path, "malformed", "the run report is not a JSON object")
    specs = []
    _walk_specs(run, specs, path)
    seen = []
    known = set()
    ran = False
    for spec in specs:
        test_id = _safe("%s::%s" % (_safe(spec.get("file", ""), TEXT_CHARS),
                                    _clean_title(spec.get("title", ""))), TEXT_CHARS)
        if spec.get("tests") is not None and not isinstance(spec["tests"], list):
            _fail(path, "bad-field", "`tests` of a spec is not a list")
        for test in spec.get("tests") or []:
            if not isinstance(test, dict):
                _fail(path, "bad-field", "a test is not an object")
            results = test.get(RESULTS_KEY)
            if results is not None and not isinstance(results, list):
                _fail(path, "bad-field", "`%s` of a test is not a list" % RESULTS_KEY)
            if results:
                ran = True
            status = test.get("status")
            if status is not None and not isinstance(status, str):
                _fail(path, "bad-field", "`status` of a test is not a string")
            status = _safe(status) if status is not None else status
            if status in FAILING_STATUSES and (test_id, status) not in known:
                known.add((test_id, status))
                seen.append((test_id, status))
    if not ran:
        _fail(path, "not-a-run-report",
              "no test carries a `%s` entry, so nothing in this file was run"
              % RESULTS_KEY)
    return seen


def _same_test(run_id, tile_test_id):
    """Whether a run report's test is the test a tile claims.

    The run report's `file` is relative to `config.rootDir` while a tile's test
    id carries that root joined to the working directory, so the two paths
    agree on a suffix and the titles agree exactly."""
    run_file, _, run_title = run_id.partition("::")
    tile_file, _, tile_title = tile_test_id.partition("::")
    if run_title != tile_title:
        return False
    if run_file == tile_file:
        return True
    return run_file.endswith("/" + tile_file) or tile_file.endswith("/" + run_file)


def tiles_of_test(run_id, tiles):
    """Every tile whose `tests` list names this test, in tile id order."""
    hit = set()
    for tile in tiles:
        for claimed in tile.get("tests") or []:
            if isinstance(claimed, str) and _same_test(run_id, claimed):
                hit.add(tile["id"])
    return sorted(hit)


# --------------------------------------------------------------------------- the report


def _summary_lines(tiles):
    total, covered, manual, uncovered = _counts(tiles)
    lines = ["%d tiles, %d covered, %d manual, %d uncovered, %s covered" % (
        total, covered, manual, uncovered, _pct(covered, total))]
    unknown = sorted({tile.get("status") for tile in tiles} - set(STATUSES) - {None})
    if unknown or any(tile.get("status") is None for tile in tiles):
        names = ", ".join("`%s`" % status for status in unknown) or "an absent status"
        lines.append("")
        lines.append("Counted as uncovered: %d tiles carry a status this report does not "
                     "know (%s)." % (
                         sum(1 for tile in tiles if tile.get("status") not in STATUSES), names))
    return lines


def _area_lines(tiles):
    lines = ["## By area", "",
             "| Area | Tiles | Covered | Manual | Uncovered | Percent |",
             "|---|---|---|---|---|---|"]
    for area, group in sorted(_areas(tiles).items()):
        total, covered, manual, uncovered = _counts(group)
        lines.append("| %s | %d | %d | %d | %d | %s |" % (
            area, total, covered, manual, uncovered, _pct(covered, total)))
    lines.append("")
    return lines


def _gap_lines(gaps, by_id):
    lines = ["## Gaps", ""]
    if not gaps:
        lines.extend(["No uncovered tiles.", ""])
        return lines
    for number, gap in enumerate(gaps[:GAP_LIMIT], 1):
        tile = by_id.get(gap) or {}
        detail = ", ".join(str(part) for part in (tile.get("kind"), tile.get("risk")) if part)
        disabled = tile.get("skipped_tests") or []
        lines.append("%d. `%s`%s%s" % (number, gap, " (%s)" % detail if detail else "",
                                       DISABLED_NOTE % disabled[0] if disabled else ""))
    if len(gaps) > GAP_LIMIT:
        lines.append("")
        lines.append("...and %d more." % (len(gaps) - GAP_LIMIT))
    lines.append("")
    return lines


def _diff_lines(tiles, previous, source, skip):
    lines = ["## Tile diff", ""]
    if skip:
        lines.extend(["skip: %s" % skip, ""])
        return lines
    now = {tile["id"]: tile.get("status") for tile in tiles}
    then = {tile["id"]: tile.get("status") for tile in previous}
    new = sorted(set(now) - set(then))
    removed = sorted(set(then) - set(now))
    changed = sorted(tile_id for tile_id in set(now) & set(then) if now[tile_id] != then[tile_id])
    lines.append("Since %s." % source)
    lines.append("")
    lines.append("New (%d): %s" % (len(new), ", ".join("`%s`" % t for t in new) or "none"))
    lines.append("")
    lines.append("Removed (%d): %s" % (len(removed), ", ".join("`%s`" % t for t in removed) or "none"))
    lines.append("")
    lines.append("Changed (%d): %s" % (
        len(changed),
        ", ".join("`%s` %s → %s" % (t, then[t], now[t]) for t in changed) or "none"))
    lines.append("")
    return lines


def _failing_lines(failures, tiles):
    lines = ["## Failing tests by tile", ""]
    if not failures:
        lines.extend(["No failing tests in the run report.", ""])
        return lines
    lines.append("| Test | Status | Tiles |")
    lines.append("|---|---|---|")
    for test_id, status in failures[:FAILING_LIMIT]:
        claimed = tiles_of_test(test_id, tiles)
        shown = ", ".join("`%s`" % tile for tile in claimed) if claimed else "no tile claimed"
        lines.append("| `%s` | %s | %s |" % (test_id.replace("|", "\\|"), status, shown))
    if len(failures) > FAILING_LIMIT:
        lines.append("")
        lines.append("...and %d more." % (len(failures) - FAILING_LIMIT))
    lines.append("")
    return lines


def build_report(tiles, gaps, previous=None, source=None, skip=None, failures=None):
    """The whole report as one string."""
    by_id = {tile["id"]: tile for tile in tiles}
    lines = [TITLE, ""]
    lines.extend(_summary_lines(tiles))
    lines.append("")
    lines.extend(_area_lines(tiles))
    lines.extend(_gap_lines(gaps, by_id))
    if previous is not None or skip:
        lines.extend(_diff_lines(tiles, previous or [], source, skip))
    if failures is not None:
        lines.extend(_failing_lines(failures, tiles))
    return "\n".join(lines).rstrip("\n") + "\n"


# --------------------------------------------------------------------------- cli


def _parser():
    parser = argparse.ArgumentParser(
        prog="report.py", description="Write the sign-off coverage report.")
    parser.add_argument("--tiles", required=True, metavar="PATH", help="the tiles file to report on")
    previous = parser.add_mutually_exclusive_group()
    previous.add_argument("--since", metavar="PATH", help="a previous tiles file to diff against")
    previous.add_argument("--since-ref", metavar="REF",
                          help="a git ref whose tiles file to diff against")
    parser.add_argument("--run", metavar="PATH", help="a Playwright JSON run report")
    parser.add_argument("--out", required=True, metavar="PATH", help="where to write the report")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)

    tiles, claimed_gaps = _tiles_of(_load_json_path(args.tiles, "the tiles file"), args.tiles)
    gaps = rank_gaps(tiles)
    if claimed_gaps and claimed_gaps != gaps:
        # One line, on stderr, so the report itself stays the report.
        sys.stderr.write(
            "report.py: warning: %s lists %d gaps, the tiles in it rank %d; "
            "the report uses the ranking, not the `gaps` key\n"
            % (args.tiles, len(claimed_gaps), len(gaps)))

    previous = None
    source = None
    skip = None
    if args.since:
        # An explicit path that will not read is unreadable input, not a skip:
        # the caller named a file it expected to be there.
        previous, _ = _tiles_of(_load_json_path(args.since, "the previous tiles file"),
                                args.since)
        source = args.since
    elif args.since_ref:
        ref_path = _ref_path(args.tiles)
        source = "%s:%s" % (args.since_ref, ref_path)
        raw, skip = _git_show(args.since_ref, ref_path)
        if raw is not None:
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                skip = "the tiles file at %s is not valid JSON" % source
            else:
                if not isinstance(data, dict) or not isinstance(data.get("tiles"), list):
                    skip = "the tiles file at %s has no `tiles` list" % source
                else:
                    previous = [tile for tile in data["tiles"]
                                if isinstance(tile, dict) and tile.get("id")]

    failures = None
    if args.run:
        failures = failing_tests(_load_json_path(args.run, "the run report"), args.run)

    text = build_report(tiles, gaps, previous=previous, source=source, skip=skip, failures=failures)

    parent = os.path.dirname(os.path.abspath(args.out))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
