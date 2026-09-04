"""Unit tests for report.py, the sign-off coverage report.

The tiles fixtures are written inline; the run report is the real Playwright
JSON captured on 2026-09-04 (tests/fixtures/playwright-run.json), copied and
edited in the test so one test fails, because a real run passes.
"""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[1]
          / "skills" / "signoff-report" / "scripts" / "report.py")
_spec = importlib.util.spec_from_file_location("signoff_report", SCRIPT)
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)

RUN_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "playwright-run.json"

# Six tiles: three covered, one manual, two uncovered, over two areas.
TILES = {
    "tiled_at": "2026-09-04T18:30:00Z",
    "tiles": [
        {"id": "auth.sign-in.valid-password", "area": "auth", "kind": "rule", "risk": "high",
         "tests": ["e2e/auth.spec.ts::signs in with a valid password"],
         "cases": ["TC-auth-001"], "status": "covered"},
        {"id": "auth.login.render.anonymous", "area": "auth", "kind": "render", "risk": "medium",
         "tests": ["e2e/auth.spec.ts::signs in with a valid password"],
         "cases": [], "status": "covered"},
        {"id": "auth.login.error.invalid-credentials", "area": "auth", "kind": "error",
         "risk": "medium", "tests": [], "cases": [], "status": "uncovered"},
        {"id": "org.members.remove.requires-admin", "area": "org", "kind": "rule", "risk": "high",
         "tests": [], "cases": [], "status": "uncovered"},
        {"id": "org.home.render.member", "area": "org", "kind": "render", "risk": "low",
         "tests": [], "cases": ["TC-org-001"], "status": "manual"},
        {"id": "org.members.render.admin", "area": "org", "kind": "render", "risk": "low",
         "tests": ["e2e/org.spec.ts::lists the members"], "cases": [], "status": "covered"},
    ],
    "gaps": ["org.members.remove.requires-admin", "auth.login.error.invalid-credentials"],
}


def write_json(path, data):
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def run(capsys, *argv):
    code = report.main(list(argv))
    return code, capsys.readouterr().out


def mark_failed(run_report, title_fragment, status):
    """Set the status of every test of the spec whose title holds the fragment."""
    hit = [0]

    def walk(node):
        for spec in node.get("specs") or []:
            if title_fragment in spec.get("title", ""):
                for test in spec.get("tests") or []:
                    test["status"] = status
                    hit[0] += 1
        for child in node.get("suites") or []:
            walk(child)

    for suite in run_report.get("suites") or []:
        walk(suite)
    assert hit[0], "the fixture has no spec whose title holds %r" % title_fragment
    return run_report


# --------------------------------------------------------------------------- counts


def test_summary_math(tmp_path, capsys):
    tiles = write_json(tmp_path / "tiles.json", TILES)
    out_path = tmp_path / "coverage.md"
    code, out = run(capsys, "--tiles", tiles, "--out", str(out_path))
    assert code == 0
    assert "6 tiles, 3 covered, 1 manual, 2 uncovered, 50% covered" in out
    assert out == out_path.read_text(encoding="utf-8")


def test_per_area_rows(tmp_path, capsys):
    tiles = write_json(tmp_path / "tiles.json", TILES)
    code, out = run(capsys, "--tiles", tiles, "--out", str(tmp_path / "coverage.md"))
    assert code == 0
    assert "| Area | Tiles | Covered | Manual | Uncovered | Percent |" in out
    assert "| auth | 3 | 2 | 0 | 1 | 67% |" in out
    assert "| org | 3 | 1 | 1 | 1 | 33% |" in out


def test_gaps_are_listed_in_the_order_the_tiles_file_ranks_them(tmp_path, capsys):
    tiles = write_json(tmp_path / "tiles.json", TILES)
    code, out = run(capsys, "--tiles", tiles, "--out", str(tmp_path / "coverage.md"))
    assert code == 0
    assert "1. `org.members.remove.requires-admin` (rule, high)" in out
    assert "2. `auth.login.error.invalid-credentials` (error, medium)" in out


def test_an_unknown_status_counts_as_uncovered(tmp_path, capsys):
    data = copy.deepcopy(TILES)
    data["tiles"][0]["status"] = "probably-fine"
    tiles = write_json(tmp_path / "tiles.json", data)
    code, out = run(capsys, "--tiles", tiles, "--out", str(tmp_path / "coverage.md"))
    assert code == 0
    assert "6 tiles, 2 covered, 1 manual, 3 uncovered, 33% covered" in out
    assert "Counted as uncovered: 1 tiles carry a status this report does not know" in out


# --------------------------------------------------------------------------- the diff


def test_diff_lists_new_removed_and_changed(tmp_path, capsys):
    previous = copy.deepcopy(TILES)
    previous["tiles"] = [tile for tile in previous["tiles"]
                         if tile["id"] != "org.members.render.admin"]
    previous["tiles"].append({"id": "auth.sign-out.clears-session", "area": "auth",
                              "kind": "rule", "risk": "medium", "status": "covered"})
    for tile in previous["tiles"]:
        if tile["id"] == "auth.login.render.anonymous":
            tile["status"] = "uncovered"
    tiles = write_json(tmp_path / "tiles.json", TILES)
    since = write_json(tmp_path / "previous.json", previous)
    code, out = run(capsys, "--tiles", tiles, "--since", since,
                    "--out", str(tmp_path / "coverage.md"))
    assert code == 0
    assert "## Tile diff" in out
    assert "New (1): `org.members.render.admin`" in out
    assert "Removed (1): `auth.sign-out.clears-session`" in out
    assert "Changed (1): `auth.login.render.anonymous` uncovered → covered" in out


def test_diff_says_none_when_nothing_moved(tmp_path, capsys):
    tiles = write_json(tmp_path / "tiles.json", TILES)
    since = write_json(tmp_path / "previous.json", TILES)
    code, out = run(capsys, "--tiles", tiles, "--since", since,
                    "--out", str(tmp_path / "coverage.md"))
    assert code == 0
    assert "New (0): none" in out
    assert "Removed (0): none" in out
    assert "Changed (0): none" in out


def test_since_ref_skips_when_the_ref_is_absent(tmp_path, capsys, monkeypatch):
    tiles = write_json(tmp_path / "tiles.json", TILES)
    # Outside a repository, so `git show` fails the way a missing ref does.
    monkeypatch.chdir(tmp_path)
    code, out = run(capsys, "--tiles", tiles, "--since-ref", "no-such-ref-2026",
                    "--out", str(tmp_path / "coverage.md"))
    assert code == 0
    assert "## Tile diff" in out
    assert "skip: " in out
    assert "no-such-ref-2026" in out or "git is not on PATH" in out


# --------------------------------------------------------------------------- the run report


def test_failing_tests_are_mapped_to_tiles(tmp_path, capsys):
    run_report = mark_failed(json.loads(RUN_FIXTURE.read_text(encoding="utf-8")),
                             "signs in with a valid password", "unexpected")
    mark_failed(run_report, "rejects a wrong password", "flaky")
    tiles = write_json(tmp_path / "tiles.json", TILES)
    run_path = write_json(tmp_path / "run.json", run_report)
    code, out = run(capsys, "--tiles", tiles, "--run", run_path,
                    "--out", str(tmp_path / "coverage.md"))
    assert code == 0
    assert "## Failing tests by tile" in out
    # The id drops the `@tile:` token from the title; the tiles come from the
    # tiles file's own `tests` lists, and the run's file path is its suffix.
    assert ("| `auth.spec.ts::signs in with a valid password` | unexpected | "
            "`auth.login.render.anonymous`, `auth.sign-in.valid-password` |") in out
    # A flaky test nothing claims is still listed, and says so.
    assert "| `auth.spec.ts::rejects a wrong password` | flaky | no tile claimed |" in out
    # The third test in the fixture passed and is not in the table.
    assert "untagged test" not in out


def test_a_green_run_lists_no_failing_tests(tmp_path, capsys):
    tiles = write_json(tmp_path / "tiles.json", TILES)
    code, out = run(capsys, "--tiles", tiles, "--run", str(RUN_FIXTURE),
                    "--out", str(tmp_path / "coverage.md"))
    assert code == 0
    assert "No failing tests in the run report." in out


def test_failing_section_is_absent_without_a_run(tmp_path, capsys):
    tiles = write_json(tmp_path / "tiles.json", TILES)
    code, out = run(capsys, "--tiles", tiles, "--out", str(tmp_path / "coverage.md"))
    assert code == 0
    assert "Failing tests by tile" not in out
    assert "Tile diff" not in out


# --------------------------------------------------------------------------- failing closed


@pytest.mark.parametrize("body", ["{not json", '{"no": "tiles"}'])
def test_unreadable_tiles_file_exits_two(tmp_path, body):
    path = tmp_path / "tiles.json"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        report.main(["--tiles", str(path), "--out", str(tmp_path / "coverage.md")])
    assert excinfo.value.code == 2


def test_a_missing_run_report_exits_two(tmp_path):
    tiles = write_json(tmp_path / "tiles.json", TILES)
    with pytest.raises(SystemExit) as excinfo:
        report.main(["--tiles", tiles, "--run", str(tmp_path / "absent.json"),
                     "--out", str(tmp_path / "coverage.md")])
    assert excinfo.value.code == 2


def test_since_ref_diffs_what_git_returned(tmp_path, capsys, monkeypatch):
    """The skip path is tested against a real absent ref; this is the other
    half, the wiring from a successful read to the diff, without needing a
    repository whose history holds a tiles file."""
    previous = copy.deepcopy(TILES)
    previous["tiles"] = [tile for tile in previous["tiles"]
                         if tile["id"] != "org.members.render.admin"]
    payload = json.dumps(previous).encode("utf-8")
    monkeypatch.setattr(report, "_git_show", lambda ref, path: (payload, None))
    tiles = write_json(tmp_path / "tiles.json", TILES)
    code, out = run(capsys, "--tiles", tiles, "--since-ref", "signoff-2026-09-01",
                    "--out", str(tmp_path / "coverage.md"))
    assert code == 0
    assert "Since signoff-2026-09-01:.qa/tiles.json." in out
    assert "New (1): `org.members.render.admin`" in out
    assert "Removed (0): none" in out
