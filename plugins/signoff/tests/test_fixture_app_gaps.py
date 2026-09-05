"""Integration test: the signoff scripts run against the fixture app end to
end and land on plugins/signoff/tests/fixture-app/expected.json.

Every script is run the way a skill runs it: as a subprocess of
``sys.executable``, from a temporary copy of the fixture app, so nothing is
written back into the repository. The fixture app's own Playwright suite
(``fixture-app/e2e``, see ``fixture-app/README.md``) is never run here - it
needs uvicorn and a browser; this test only parses its files with
``tests.py``, the way the skill does before a server exists to run them
against.
"""
import copy
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SIGNOFF = Path(__file__).resolve().parents[1]
FIXTURE_APP = SIGNOFF / "tests" / "fixture-app"
EXPECTED = json.loads((FIXTURE_APP / "expected.json").read_text(encoding="utf-8"))

TESTS_SCRIPT = SIGNOFF / "skills" / "tiling-coverage" / "scripts" / "tests.py"
TILE_SCRIPT = SIGNOFF / "skills" / "tiling-coverage" / "scripts" / "tile.py"
CASES_SCRIPT = SIGNOFF / "skills" / "recording-test-cases" / "scripts" / "cases.py"
REPORT_SCRIPT = SIGNOFF / "skills" / "signoff-report" / "scripts" / "report.py"

# The fixture app's own artifacts from having been run once locally; copying
# them into the temporary tree would not be wrong, just noise.
IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache")

# The tile the automated case claims, and the test that claims it in the
# suite (see fixture-app/e2e/test_auth.py). Looked up by tile id rather than
# hardcoding the test id, so a change to the test's own name is not this
# test's problem.
AUTOMATED_TILE = "auth.sign-in.valid-password"

CASE_AUTOMATED = """\
# TC-auth-001: Sign in with a valid password

- area: auth
- tiles: %s
- role: member
- priority: high
- status: automated
- automated: %s

## Preconditions

- a member account exists with a known password

## Steps

| # | Action | Expected |
|---|--------|----------|
| 1 | Open /login | The sign-in form shows email and password fields |
| 2 | Enter the email and password, press Sign in | The organisation page opens and shows the member's name |
"""

# A tile the suite leaves deliberately uncovered (fixture-app/README.md), so
# a manual case naming it never turns it `covered` and never overlaps the
# automated case's tile.
CASE_MANUAL = """\
# TC-org-001: Remove a member requires admin

- area: org
- tiles: org.members.remove.requires-admin
- role: admin
- priority: high
- status: manual
- automated:

## Preconditions

- an admin and a member both belong to the organisation

## Steps

| # | Action | Expected |
|---|--------|----------|
| 1 | Sign in as the admin and open Members | The members list shows the admin and the member |
| 2 | Remove the member | The member is removed and the admin's list no longer shows them |
"""


def run(script, *args, **kwargs):
    """Run a signoff script the way a skill does: sys.executable, no shell."""
    cwd = kwargs.pop("cwd")
    completed = subprocess.run(
        [sys.executable, str(script)] + list(args), cwd=str(cwd),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return (completed.returncode, completed.stdout.decode("utf-8"),
            completed.stderr.decode("utf-8"))


@pytest.fixture
def tiled(tmp_path):
    """A fixture-app copy already tiled: tests.py then tile.py have run, and
    .qa/tests.json and .qa/tiles.json exist beside .qa/map.json and
    .qa/rules.json, which the copy inherits from the fixture."""
    copy_dir = tmp_path / "fixture-app"
    shutil.copytree(str(FIXTURE_APP), str(copy_dir), ignore=IGNORE)
    qa = copy_dir / ".qa"
    tests_out = qa / "tests.json"
    tiles_out = qa / "tiles.json"

    code, _out, err = run(TESTS_SCRIPT, "--stack", "pytest", "e2e",
                          "--out", str(tests_out), cwd=copy_dir)
    assert code == 0, err

    code, _out, err = run(TILE_SCRIPT, "--map", str(qa / "map.json"),
                          "--rules", str(qa / "rules.json"), "--tests", str(tests_out),
                          "--out", str(tiles_out), cwd=copy_dir)
    assert code == 0, err

    tests_doc = json.loads(tests_out.read_text(encoding="utf-8"))
    tiles_doc = json.loads(tiles_out.read_text(encoding="utf-8"))
    return copy_dir, qa, tests_doc, tiles_doc


def _write_cases(copy_dir, tests_doc):
    """The two minimal cases under testcases/ in the copy: one automated,
    naming the test the suite actually uses to claim AUTOMATED_TILE, one
    manual, naming a tile the suite leaves uncovered."""
    automated_id = next((t["id"] for t in tests_doc["tests"]
                         if AUTOMATED_TILE in t["tiles"]), None)
    assert automated_id, "no test in the fixture suite claims %s" % AUTOMATED_TILE

    testcases = copy_dir / "testcases"
    (testcases / "auth").mkdir(parents=True)
    (testcases / "org").mkdir(parents=True)
    (testcases / "auth" / "TC-auth-001.md").write_text(
        CASE_AUTOMATED % (AUTOMATED_TILE, automated_id), encoding="utf-8")
    (testcases / "org" / "TC-org-001.md").write_text(CASE_MANUAL, encoding="utf-8")
    return testcases


# --------------------------------------------------------------------------- tests.py + tile.py


def test_tiles_match_expected_json(tiled):
    """The gap the spike set out to close: tests.py and tile.py, run over
    the fixture app exactly as a skill would, reproduce expected.json's
    hand-derived tile count, covered set and ranked gap list exactly."""
    _copy_dir, _qa, _tests_doc, tiles_doc = tiled
    covered = sorted(tile["id"] for tile in tiles_doc["tiles"] if tile["status"] == "covered")

    assert len(tiles_doc["tiles"]) == EXPECTED["tiles"]
    assert covered == EXPECTED["covered"]
    assert tiles_doc["gaps"] == EXPECTED["gaps"]
    # Nothing in the suite claims a tile the map and the rules do not define.
    assert tiles_doc["unknown_claims"] == []


def test_the_suites_disabled_test_claims_a_tile_without_covering_it(tiled):
    """The fixture's one skipped test (fixture-app/e2e/test_auth.py) claims
    `auth.sign-out.clears-session`. Its claim is recorded apart, the tile
    stays uncovered, and the ranked gaps are the same as without it."""
    _copy_dir, _qa, tests_doc, tiles_doc = tiled
    skipped_ids = sorted(test["id"] for test in tests_doc["tests"] if test["skipped"])
    assert skipped_ids == sorted(
        test_id for ids in EXPECTED["skipped"].values() for test_id in ids)

    by_id = {tile["id"]: tile for tile in tiles_doc["tiles"]}
    for tile_id, test_ids in EXPECTED["skipped"].items():
        tile = by_id[tile_id]
        assert tile["skipped_tests"] == test_ids
        assert tile["tests"] == []
        assert tile["status"] == "uncovered"
        assert tile_id in tiles_doc["gaps"]


def test_the_report_marks_the_gap_the_disabled_test_claims(tiled):
    """report.py's gap list says so too, in the same words as tile.py."""
    copy_dir, qa, _tests_doc, _tiles_doc = tiled
    out_path = copy_dir / "testcases" / "coverage.md"
    code, out, err = run(REPORT_SCRIPT, "--tiles", str(qa / "tiles.json"),
                         "--out", str(out_path), cwd=copy_dir)
    assert code == 0, err
    for tile_id, test_ids in EXPECTED["skipped"].items():
        assert "`%s` (rule, medium) (claimed by a disabled test: %s)" % (
            tile_id, test_ids[0]) in out


def test_the_report_percent_is_the_rounded_one(tiled):
    """One percent rule (F5): the fixture is 7 covered of 26 tiles, 27%."""
    copy_dir, qa, _tests_doc, tiles_doc = tiled
    assert len(tiles_doc["tiles"]) == 26
    assert sum(1 for tile in tiles_doc["tiles"] if tile["status"] == "covered") == 7
    out_path = copy_dir / "testcases" / "coverage.md"
    code, out, err = run(REPORT_SCRIPT, "--tiles", str(qa / "tiles.json"),
                         "--out", str(out_path), cwd=copy_dir)
    assert code == 0, err
    assert "26 tiles, 7 covered, 0 manual, 19 uncovered, 27% covered" in out


# --------------------------------------------------------------------------- cases.py


def test_cases_check_flags_only_the_uncased_covered_tiles(tiled):
    copy_dir, qa, tests_doc, tiles_doc = tiled
    testcases = _write_cases(copy_dir, tests_doc)

    code, out, err = run(CASES_SCRIPT, "check", str(testcases),
                         "--tiles", str(qa / "tiles.json"), "--tests", str(qa / "tests.json"),
                         cwd=copy_dir)
    # Findings exist (the other covered tiles have no case), so the lint
    # itself must report non-clean.
    assert code == 1, err

    lines = out.splitlines()
    findings, summary = lines[:-1], lines[-1]
    assert findings, out
    assert all(re.match(r"^\S+:0 uncased-tile: ", line) for line in findings), findings

    named = set(re.findall(r"`([^`]+)`", "\n".join(findings)))
    covered = {tile["id"] for tile in tiles_doc["tiles"] if tile["status"] == "covered"}
    assert named == covered - {AUTOMATED_TILE}
    assert summary == "2 cases checked, %d findings" % len(findings)


def test_cases_export_azure_csv_header(tiled):
    copy_dir, _qa, tests_doc, _tiles_doc = tiled
    testcases = _write_cases(copy_dir, tests_doc)
    out_path = copy_dir / "export.csv"

    code, out, err = run(CASES_SCRIPT, "export", str(testcases),
                         "--format", "azure-csv", "--out", str(out_path), cwd=copy_dir)
    assert code == 0, err
    assert "2 cases exported as azure-csv" in out

    header = out_path.read_text(encoding="utf-8").splitlines()[0]
    assert header == ("ID,Work Item Type,Title,Test Step,Step Action,Step Expected,"
                      "Area Path,Assigned To,State")


# --------------------------------------------------------------------------- report.py


def test_report_summary_and_diff_against_since(tiled):
    copy_dir, qa, _tests_doc, tiles_doc = tiled
    # "a copy with one tile removed": the previous sign-off's tiles file,
    # missing a tile the current one has - the diff's `New` case.
    removed_id = tiles_doc["tiles"][0]["id"]
    since = copy.deepcopy(tiles_doc)
    since["tiles"] = [tile for tile in since["tiles"] if tile["id"] != removed_id]
    since_path = qa / "tiles-previous.json"
    since_path.write_text(json.dumps(since), encoding="utf-8")

    out_path = copy_dir / "testcases" / "coverage.md"
    code, out, err = run(REPORT_SCRIPT, "--tiles", str(qa / "tiles.json"),
                         "--since", str(since_path), "--out", str(out_path), cwd=copy_dir)
    assert code == 0, err
    assert out == out_path.read_text(encoding="utf-8")

    total = len(tiles_doc["tiles"])
    covered = sum(1 for tile in tiles_doc["tiles"] if tile["status"] == "covered")
    manual = sum(1 for tile in tiles_doc["tiles"] if tile["status"] == "manual")
    uncovered = total - covered - manual
    percent = int(round(100.0 * covered / total))
    summary = "%d tiles, %d covered, %d manual, %d uncovered, %d%% covered" % (
        total, covered, manual, uncovered, percent)

    assert summary in out
    assert "New (1): `%s`" % removed_id in out
    assert "Removed (0): none" in out
    assert "Changed (0): none" in out
