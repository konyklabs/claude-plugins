"""Unit tests for signoff's tests.py and tile.py.

The Playwright fixtures under ``fixtures/`` are real reporter output captured
on 2026-09-04 from Playwright 1.62 (a listing and a run of the same suite);
every other fixture is written inline here so the expectation and the input
sit next to each other.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / "skills"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
TESTS_SCRIPT = SKILLS / "tiling-coverage" / "scripts" / "tests.py"
TILE_SCRIPT = SKILLS / "tiling-coverage" / "scripts" / "tile.py"


def _load(name, path):
    """Import a script by path: the scripts are run by path, not installed."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tests_py = _load("signoff_tests", TESTS_SCRIPT)
tile_py = _load("signoff_tile", TILE_SCRIPT)


# --------------------------------------------------------------------------- helpers


def read(path):
    with open(str(path), "r", encoding="utf-8") as handle:
        return json.load(handle)


def write(path, payload):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return str(path)


def capture_cwd(report):
    """The directory the capture ran in: the parent of its rootDir."""
    return str(Path(read(report)["config"]["rootDir"]).parent)


def list_tests(*args):
    return tests_py.main([str(a) for a in args])


def by_id(payload):
    return {test["id"]: test for test in payload["tests"]}


# --------------------------------------------------------------------------- tests.py, playwright


def test_list_fixture_yields_three_claims_and_the_untagged_test(tmp_path):
    report = FIXTURES / "playwright-list.json"
    out = tmp_path / "tests.json"
    assert list_tests("--stack", "playwright-ts", "--input", report,
                      "--cwd", capture_cwd(report), "--out", out) == 0
    payload = read(out)
    assert payload["stack"] == "playwright-ts"
    found = by_id(payload)
    # Four specs: two at file level and two inside the `sign in` suite.
    assert sorted(found) == [
        "e2e/auth.spec.ts::rejects a wrong password",
        "e2e/auth.spec.ts::signs in with a valid password",
        "e2e/auth.spec.ts::static annotation claim",
        "e2e/auth.spec.ts::untagged test",
    ]
    claims = {test["id"]: test["tiles"] for test in payload["tests"] if test["tiles"]}
    assert claims == {
        "e2e/auth.spec.ts::signs in with a valid password": ["auth.sign-in.valid-password"],
        "e2e/auth.spec.ts::rejects a wrong password": ["auth.sign-in.wrong-password"],
        "e2e/auth.spec.ts::static annotation claim": ["docs.home.render.anonymous"],
    }
    untagged = found["e2e/auth.spec.ts::untagged test"]
    assert untagged["tiles"] == [] and untagged["annotations"] == []
    tagged = found["e2e/auth.spec.ts::signs in with a valid password"]
    # The tag is reported without its `@` and stays in `tags`; the title and
    # so the id lose it; `file` is rootDir joined and made relative to --cwd.
    assert tagged["tags"] == ["tile:auth.sign-in.valid-password"]
    assert tagged["file"] == "e2e/auth.spec.ts" and tagged["line"] == 4
    assert found["e2e/auth.spec.ts::rejects a wrong password"]["tags"] == [
        "tile:auth.sign-in.wrong-password", "smoke"]


def test_run_fixture_runtime_annotation_is_read(tmp_path):
    report = FIXTURES / "playwright-run.json"
    out = tmp_path / "tests.json"
    assert list_tests("--stack", "playwright-ts", "--input", report,
                      "--cwd", capture_cwd(report), "--out", out) == 0
    signs_in = by_id(read(out))["e2e/auth.spec.ts::signs in with a valid password"]
    # Pushed at run time, so it is in the run report and not in the listing.
    assert signs_in["annotations"] == [{"type": "tile", "description": "auth.sign-in.render-anonymous"}]
    assert signs_in["tiles"] == ["auth.sign-in.valid-password", "auth.sign-in.render-anonymous"]


def test_a_report_without_root_dir_is_read_relative_to_cwd(tmp_path):
    report = write(tmp_path / "report.json", {
        "config": {}, "suites": [{"title": "a.spec.ts", "file": "e2e/a.spec.ts", "specs": [
            {"title": "does a thing @tile:org.home.render.member", "file": "e2e/a.spec.ts", "line": 3,
             "tags": ["tile:org.home.render.member"], "tests": [{"annotations": [], "results": []}]}]}]})
    out = tmp_path / "tests.json"
    assert list_tests("--stack", "playwright-ts", "--input", report, "--cwd", tmp_path, "--out", out) == 0
    only = read(out)["tests"][0]
    assert only["id"] == "e2e/a.spec.ts::does a thing"
    assert only["tiles"] == ["org.home.render.member"]


def test_one_spec_under_two_projects_is_one_test(tmp_path):
    report = write(tmp_path / "report.json", {
        "config": {"rootDir": str(tmp_path / "e2e")}, "suites": [{"title": "a.spec.ts", "specs": [
            {"title": "renders", "file": "a.spec.ts", "line": 2, "tags": [], "tests": [
                {"projectName": "chromium", "annotations": [{"type": "tile", "description": "org.home.render.member"}]},
                {"projectName": "firefox", "annotations": [{"type": "tile", "description": "org.home.render.admin"}]}]}]}]})
    out = tmp_path / "tests.json"
    assert list_tests("--stack", "playwright-ts", "--input", report, "--cwd", tmp_path, "--out", out) == 0
    payload = read(out)
    assert len(payload["tests"]) == 1
    assert payload["tests"][0]["tiles"] == ["org.home.render.member", "org.home.render.admin"]


def test_run_without_npx_exits_two(tmp_path):
    """Fail closed: no listing tool, no tests.json, a `skip` reason on stderr."""
    empty = tmp_path / "empty-path"
    empty.mkdir()
    out = tmp_path / "tests.json"
    finished = subprocess.run(
        [sys.executable, str(TESTS_SCRIPT), "--stack", "playwright-ts", "--run", "--out", str(out)],
        cwd=str(tmp_path), env={"PATH": str(empty)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert finished.returncode == 2, finished.stderr.decode()
    assert "skip:" in finished.stderr.decode() and "npx" in finished.stderr.decode()
    assert not out.exists()


def test_playwright_needs_exactly_one_source(tmp_path, capsys):
    assert list_tests("--stack", "playwright-ts", "--out", tmp_path / "tests.json") == 2
    assert "exactly one" in capsys.readouterr().err


def test_unreadable_input_exits_two(tmp_path, capsys):
    assert list_tests("--stack", "playwright-ts", "--input", tmp_path / "gone.json",
                      "--out", tmp_path / "tests.json") == 2
    assert "unreadable" in capsys.readouterr().err


# --------------------------------------------------------------------------- tests.py, pytest

MODULE = '''
import pytest
from pytest import mark


@pytest.mark.tile("auth.sign-in.valid-password")
@pytest.mark.smoke
def test_sign_in_valid(page):
    pass


@pytest.mark.tile("auth.sign-in.wrong-password", "auth.login.error.invalid-credentials")
def test_sign_in_wrong(page):
    pass


def test_untagged():
    pass


@pytest.mark.tile
def test_claims_nothing():
    pass


class TestCredentials:
    @mark.tile("credentials.create.name-validation")
    def test_name_validation(self):
        pass


class Helper:
    def test_not_collected(self):
        pass
'''

ALIAS_MODULE = '''
from signoff_marks import tile


@tile("docs.home.public")
async def test_docs_are_public():
    pass
'''


def build_tree(root):
    (root / "e2e").mkdir(parents=True)
    (root / "e2e" / "test_auth.py").write_text(MODULE, encoding="utf-8")
    (root / "e2e" / "docs_test.py").write_text(ALIAS_MODULE, encoding="utf-8")
    (root / "e2e" / "helpers.py").write_text("def test_ignored(): pass\n", encoding="utf-8")
    (root / "e2e" / "__pycache__").mkdir()
    (root / "e2e" / "__pycache__" / "test_stale.py").write_text("def test_stale(): pass\n", encoding="utf-8")
    return root / "e2e"


def test_pytest_tree_module_class_and_multi_argument_marks(tmp_path, capsys):
    tree = build_tree(tmp_path)
    out = tmp_path / "tests.json"
    assert list_tests("--stack", "pytest", tree, "--cwd", tmp_path, "--out", out) == 0
    payload = read(out)
    assert payload["stack"] == "pytest"
    found = by_id(payload)
    assert sorted(found) == [
        "e2e/docs_test.py::test_docs_are_public",
        "e2e/test_auth.py::TestCredentials::test_name_validation",
        "e2e/test_auth.py::test_claims_nothing",
        "e2e/test_auth.py::test_sign_in_valid",
        "e2e/test_auth.py::test_sign_in_wrong",
        "e2e/test_auth.py::test_untagged",
    ]
    # One mark, two ids.
    assert found["e2e/test_auth.py::test_sign_in_wrong"]["tiles"] == [
        "auth.sign-in.wrong-password", "auth.login.error.invalid-credentials"]
    # The tile mark is not a tag; the others are.
    assert found["e2e/test_auth.py::test_sign_in_valid"]["tags"] == ["smoke"]
    assert found["e2e/test_auth.py::test_sign_in_valid"]["tiles"] == ["auth.sign-in.valid-password"]
    assert found["e2e/test_auth.py::test_sign_in_valid"]["line"] == 8  # the def line, under two decorators
    # `mark.tile` on a method, and a bare imported `tile` on an async test.
    assert found["e2e/test_auth.py::TestCredentials::test_name_validation"]["tiles"] == [
        "credentials.create.name-validation"]
    assert found["e2e/docs_test.py::test_docs_are_public"]["tiles"] == ["docs.home.public"]
    # A mark with no id claims nothing and says so; a class outside pytest's
    # own `Test` prefix, a non-test module and __pycache__ are not read.
    assert found["e2e/test_auth.py::test_claims_nothing"]["tiles"] == []
    assert "empty-tile-claim" in capsys.readouterr().err


def test_pytest_needs_a_directory(tmp_path, capsys):
    assert list_tests("--stack", "pytest", tmp_path / "gone", "--out", tmp_path / "tests.json") == 2
    assert "unreadable" in capsys.readouterr().err


# --------------------------------------------------------------------------- tile.py

MAP = {
    "base_url": "http://localhost:8000",
    "explored_at": "2026-09-04T18:00:00Z",
    "roles": ["anonymous", "member", "admin"],
    "screens": [
        {"id": "auth.login", "area": "auth", "path": "/login", "title": "Sign in",
         "roles": ["anonymous"],
         "actions": [{"id": "submit", "kind": "submit", "label": "Sign in", "to": "org.home"}],
         "forms": [{"id": "login", "fields": ["email", "password"]}],
         "states": ["error:invalid-credentials", "loading"],
         "links": ["docs.home"]},
        {"id": "org.home", "area": "org", "path": "/", "title": "Home",
         "roles": ["member", "admin"], "actions": [], "states": ["error:forbidden"], "links": []},
        {"id": "docs.home", "area": "docs", "path": "/docs", "title": "Docs",
         "roles": ["anonymous", "member", "admin"], "actions": [], "states": [], "links": []},
    ],
    "flows": [{"id": "auth.sign-in", "area": "auth", "name": "Sign in", "role": "anonymous",
               "steps": ["auth.login", "org.home"]}],
}

RULES = {"mined_at": "2026-09-04T18:10:00Z", "rules": [
    {"id": "auth.sign-in.valid-password", "area": "auth", "flow": "auth.sign-in",
     "kind": "transition", "risk": "high", "statement": "A valid password signs the user in",
     "source": "app.py:41", "screens": ["auth.login", "org.home"]},
    {"id": "auth.session.required-for-app", "area": "auth", "flow": "auth.sign-in",
     "kind": "guard", "statement": "Every page but the sign-in page needs a session",
     "source": "app.py:20", "screens": ["org.home"]},
    {"id": "auth.sign-in.wrong-password", "area": "auth", "flow": "auth.sign-in",
     "kind": "error", "statement": "A wrong password shows an error",
     "source": "app.py:48", "screens": ["auth.login"]},
    {"id": "docs.home.public", "area": "docs", "flow": "docs.read",
     "kind": "flag", "statement": "The docs are readable without a session",
     "source": "app.py:60", "screens": ["docs.home", "docs.legacy"]},
]}

TESTS = {"stack": "playwright-ts", "listed_at": "2026-09-04T18:20:00Z",
         "command": "npx playwright test --list --reporter=json", "tests": [
             {"id": "e2e/auth.spec.ts::signs in with a valid password",
              "title": "signs in with a valid password", "file": "e2e/auth.spec.ts", "line": 4,
              "tags": ["tile:auth.sign-in.valid-password"],
              "annotations": [{"type": "tile", "description": "auth.login.render.anonymous"}],
              "tiles": ["auth.sign-in.valid-password", "auth.login.render.anonymous"]},
             {"id": "e2e/auth.spec.ts::claims a tile nothing defines",
              "title": "claims a tile nothing defines", "file": "e2e/auth.spec.ts", "line": 20,
              "tags": [], "annotations": [], "tiles": ["auth.login.render.ghost"]}]}

CASE_MANUAL = """# TC-auth-001: Sign in fails with a wrong password

- area: auth
- tiles: auth.login.error.invalid-credentials
- role: anonymous
- priority: medium
- status: manual
- automated:

## Preconditions

- a member account exists

## Steps

| # | Action | Expected |
|---|--------|----------|
| 1 | Open /login | The form shows |
"""

CASE_AUTOMATED = """# TC-auth-002: Sign in with a valid password

- area: auth
- tiles: auth.sign-in.valid-password
- role: anonymous
- priority: high
- status: automated
- automated: e2e/auth.spec.ts::signs in with a valid password

## Preconditions

- none

## Steps

| # | Action | Expected |
|---|--------|----------|
| 1 | Open /login | The form shows |
"""


def build_inputs(tmp_path, with_cases=True):
    write(tmp_path / "map.json", MAP)
    write(tmp_path / "rules.json", RULES)
    write(tmp_path / "tests.json", TESTS)
    if with_cases:
        cases = tmp_path / "testcases" / "auth"
        cases.mkdir(parents=True)
        (cases / "TC-auth-001.md").write_text(CASE_MANUAL, encoding="utf-8")
        (cases / "TC-auth-002.md").write_text(CASE_AUTOMATED, encoding="utf-8")
        (cases / "README.md").write_text("not a case\n", encoding="utf-8")
    return tmp_path


def run_tile(tmp_path, with_cases=True):
    build_inputs(tmp_path, with_cases)
    argv = ["--map", str(tmp_path / "map.json"), "--rules", str(tmp_path / "rules.json"),
            "--tests", str(tmp_path / "tests.json"), "--out", str(tmp_path / "tiles.json")]
    if with_cases:
        argv += ["--cases", str(tmp_path / "testcases")]
    code = tile_py.main(argv)
    return code, read(tmp_path / "tiles.json") if (tmp_path / "tiles.json").exists() else None


def test_tile_builds_rule_render_and_error_tiles(tmp_path):
    code, payload = run_tile(tmp_path)
    assert code == 0
    tiles = {tile["id"]: tile for tile in payload["tiles"]}
    # Four rules, three screens with 1 + 2 + 3 roles, two `error:` states.
    assert len(tiles) == 12
    assert sorted(t["id"] for t in payload["tiles"] if t["kind"] == "render") == [
        "auth.login.render.anonymous", "docs.home.render.admin", "docs.home.render.anonymous",
        "docs.home.render.member", "org.home.render.admin", "org.home.render.member"]
    assert sorted(t["id"] for t in payload["tiles"] if t["kind"] == "error") == [
        "auth.login.error.invalid-credentials", "org.home.error.forbidden"]
    rule = tiles["auth.sign-in.valid-password"]
    assert (rule["kind"], rule["area"], rule["flow"], rule["rule"], rule["risk"]) == (
        "rule", "auth", "auth.sign-in", "auth.sign-in.valid-password", "high")
    assert rule["screen"] is None and rule["role"] is None
    render = tiles["org.home.render.member"]
    assert (render["kind"], render["screen"], render["role"], render["flow"], render["rule"]) == (
        "render", "org.home", "member", None, None)
    error = tiles["auth.login.error.invalid-credentials"]
    assert (error["kind"], error["screen"], error["risk"]) == ("error", "auth.login", "medium")
    # Risk derived from kind when the rule does not carry one.
    assert tiles["auth.session.required-for-app"]["risk"] == "high"    # guard
    assert tiles["auth.sign-in.wrong-password"]["risk"] == "medium"    # error
    assert tiles["docs.home.public"]["risk"] == "low"                  # flag
    # A tile nothing defines is not invented from a test's claim.
    assert "auth.login.render.ghost" not in tiles


def test_tile_sets_the_three_statuses_and_counts_a_manual_case(tmp_path):
    code, payload = run_tile(tmp_path)
    assert code == 0
    status = {tile["id"]: tile["status"] for tile in payload["tiles"]}
    assert status["auth.sign-in.valid-password"] == "covered"
    assert status["auth.login.render.anonymous"] == "covered"
    assert status["auth.login.error.invalid-credentials"] == "manual"
    assert status["org.home.error.forbidden"] == "uncovered"
    tiles = {tile["id"]: tile for tile in payload["tiles"]}
    assert tiles["auth.sign-in.valid-password"]["tests"] == [
        "e2e/auth.spec.ts::signs in with a valid password"]
    assert tiles["auth.sign-in.valid-password"]["cases"] == ["TC-auth-002"]
    assert tiles["auth.login.error.invalid-credentials"]["cases"] == ["TC-auth-001"]
    # An automated case does not make an untested tile manual.
    assert [t for t in payload["tiles"] if t["status"] == "manual"] == [
        tiles["auth.login.error.invalid-credentials"]]


def test_tile_ranks_gaps_by_risk_then_kind_then_id(tmp_path):
    code, payload = run_tile(tmp_path)
    assert code == 0
    assert payload["gaps"] == [
        "auth.session.required-for-app",     # high, rule
        "auth.sign-in.wrong-password",       # medium, rule
        "org.home.error.forbidden",          # medium, error
        "docs.home.public",                  # low, rule
        "docs.home.render.admin",            # low, render, then by id
        "docs.home.render.anonymous",
        "docs.home.render.member",
        "org.home.render.admin",
        "org.home.render.member",
    ]


def test_tile_prints_counts_per_area_and_the_ranked_gaps(tmp_path, capsys):
    run_tile(tmp_path)
    out = capsys.readouterr().out
    assert "12 tiles: 2 covered, 1 manual, 9 uncovered (16% covered)" in out
    assert "| auth | 5 | 2 | 1 | 2 |" in out
    assert "| docs | 4 | 0 | 0 | 4 |" in out
    assert "| org | 3 | 0 | 0 | 3 |" in out
    assert "1. `auth.session.required-for-app` (rule, high risk)" in out


def test_tile_warns_on_an_unknown_screen_or_flow_without_failing(tmp_path, capsys):
    code, payload = run_tile(tmp_path)
    assert code == 0
    err = capsys.readouterr().err
    assert "unknown-flow: rule docs.home.public names flow docs.read" in err
    assert "unknown-screen: rule docs.home.public names screen docs.legacy" in err
    # The rule still becomes a tile: a warning is not a rejection.
    assert "docs.home.public" in [tile["id"] for tile in payload["tiles"]]


def test_tile_exits_two_on_an_unreadable_input(tmp_path, capsys):
    build_inputs(tmp_path)
    code = tile_py.main(["--map", str(tmp_path / "gone.json"), "--rules", str(tmp_path / "rules.json"),
                         "--tests", str(tmp_path / "tests.json"), "--out", str(tmp_path / "tiles.json")])
    assert code == 2
    err = capsys.readouterr().err
    assert "gone.json:0 unreadable" in err
    assert not (tmp_path / "tiles.json").exists()


def test_tile_exits_two_on_a_file_without_its_list(tmp_path, capsys):
    build_inputs(tmp_path)
    write(tmp_path / "rules.json", {"mined_at": "2026-09-04T18:10:00Z"})
    code = tile_py.main(["--map", str(tmp_path / "map.json"), "--rules", str(tmp_path / "rules.json"),
                         "--tests", str(tmp_path / "tests.json"), "--out", str(tmp_path / "tiles.json")])
    assert code == 2
    assert 'malformed: no "rules" list' in capsys.readouterr().err


def test_tile_without_cases_leaves_every_untested_tile_uncovered(tmp_path):
    code, payload = run_tile(tmp_path, with_cases=False)
    assert code == 0
    status = {tile["id"]: tile["status"] for tile in payload["tiles"]}
    assert status["auth.login.error.invalid-credentials"] == "uncovered"
    assert payload["gaps"][:3] == ["auth.session.required-for-app",
                                   "auth.sign-in.wrong-password",
                                   "auth.login.error.invalid-credentials"]
