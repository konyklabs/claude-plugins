"""Unit tests for cases.py, the Markdown test-case lint and exporter.

Every fixture is written inline: a clean case built from the skeleton in
plugins/signoff/formats.md, then one mutation per test so each finding is
attributable to exactly one break in the skeleton.
"""
import csv
import importlib.util
import json
import re
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[1]
          / "skills" / "recording-test-cases" / "scripts" / "cases.py")
_spec = importlib.util.spec_from_file_location("signoff_cases", SCRIPT)
cases = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cases)


# --------------------------------------------------------------------------- fixtures

DEFAULT_META = [
    ("area", "auth"),
    ("tiles", "auth.sign-in.valid-password, auth.login.render.anonymous"),
    ("role", "anonymous"),
    ("priority", "high"),
    ("status", "automated"),
    ("automated", "e2e/auth.spec.ts::signs in with a valid password"),
]
DEFAULT_PRECONDITIONS = ["a member account exists with a known password"]
DEFAULT_STEPS = [
    ("Open /login", "The sign-in form shows email and password fields"),
    ("Enter the email and password, press Sign in", "The organisation page opens"),
]


def meta_with(**over):
    """The metadata list with keys replaced; a value of None drops the key."""
    out = []
    for key, value in DEFAULT_META:
        if key in over:
            if over[key] is None:
                continue
            value = over[key]
        out.append((key, value))
    return out


def case_text(cid="TC-auth-001", title="Sign in with a valid password",
              meta=None, preconditions=None, steps=None):
    meta = DEFAULT_META if meta is None else meta
    preconditions = DEFAULT_PRECONDITIONS if preconditions is None else preconditions
    steps = DEFAULT_STEPS if steps is None else steps
    lines = ["# %s: %s" % (cid, title), ""]
    lines += ["- %s: %s" % (key, value) for key, value in meta]
    lines += ["", "## Preconditions", ""]
    lines += ["- %s" % item for item in preconditions]
    lines += ["", "## Steps", ""]
    lines += ["| # | Action | Expected |", "|---|--------|----------|"]
    lines += ["| %d | %s | %s |" % (n, action, expected)
              for n, (action, expected) in enumerate(steps, 1)]
    return "\n".join(lines) + "\n"


def write_case(root, text, name="TC-auth-001.md", area="auth"):
    directory = Path(root) / area
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def write_json(path, data):
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def run(capsys, *argv):
    code = cases.main(list(argv))
    return code, capsys.readouterr().out


def rules(out):
    """The rule names in a check's output."""
    found = set()
    for line in out.splitlines():
        match = re.match(r"^\S+:\d+ ([a-z-]+): ", line)
        if match:
            found.add(match.group(1))
    return found


# --------------------------------------------------------------------------- the skeleton


def test_clean_case_passes(tmp_path, capsys):
    write_case(tmp_path, case_text())
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 0, out
    assert out.strip().endswith("1 cases checked, 0 findings")


def test_title_id_mismatch(tmp_path, capsys):
    write_case(tmp_path, case_text(cid="TC-auth-001"), name="TC-auth-002.md")
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    assert rules(out) == {"title-id-mismatch"}, out


def test_missing_metadata_key(tmp_path, capsys):
    write_case(tmp_path, case_text(meta=meta_with(role=None)))
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    assert rules(out) == {"missing-metadata"}, out
    assert "the `role` line is missing" in out


def test_metadata_out_of_order(tmp_path, capsys):
    swapped = meta_with()
    swapped[3], swapped[4] = swapped[4], swapped[3]  # status before priority
    write_case(tmp_path, case_text(meta=swapped))
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    assert rules(out) == {"metadata-order"}, out


def test_bad_enum_value(tmp_path, capsys):
    write_case(tmp_path, case_text(meta=meta_with(priority="urgent")))
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    assert rules(out) == {"bad-value"}, out
    assert "not one of high, medium, low" in out


def test_automated_set_on_a_manual_case(tmp_path, capsys):
    write_case(tmp_path, case_text(meta=meta_with(status="manual")))
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    assert rules(out) == {"automated-mismatch"}, out


def test_automated_case_without_a_test(tmp_path, capsys):
    write_case(tmp_path, case_text(meta=meta_with(automated="")))
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    assert rules(out) == {"automated-mismatch"}, out


def test_empty_steps_table(tmp_path, capsys):
    write_case(tmp_path, case_text(steps=[]))
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    assert rules(out) == {"empty-steps"}, out


def test_empty_cell(tmp_path, capsys):
    write_case(tmp_path, case_text(steps=[("Open /login", "")]))
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    assert rules(out) == {"empty-cell"}, out


def test_missing_sections(tmp_path, capsys):
    # The title and the metadata, and nothing after them.
    write_case(tmp_path, case_text().split("## Preconditions")[0])
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    assert rules(out) == {"missing-section"}, out


def test_finding_line_format(tmp_path, capsys):
    path = write_case(tmp_path, case_text(meta=meta_with(priority="urgent")))
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    # `- priority:` is the sixth line of the file.
    assert out.splitlines()[0].startswith("%s:6 bad-value: " % path)


# --------------------------------------------------------------------------- tiles and tests


def test_dangling_tile(tmp_path, capsys):
    write_case(tmp_path, case_text(meta=meta_with(
        tiles="auth.sign-in.valid-password, auth.sign-in.ghost")))
    tiles = write_json(tmp_path / "tiles.json", {"tiles": [
        {"id": "auth.sign-in.valid-password", "area": "auth", "status": "covered"}]})
    code, out = run(capsys, "check", str(tmp_path), "--tiles", tiles)
    assert code == 1
    assert rules(out) == {"dangling-tile"}, out
    assert "auth.sign-in.ghost" in out


def test_uncased_covered_tile(tmp_path, capsys):
    write_case(tmp_path, case_text(meta=meta_with(tiles="auth.sign-in.valid-password")))
    tiles = write_json(tmp_path / "tiles.json", {"tiles": [
        {"id": "auth.sign-in.valid-password", "area": "auth", "status": "covered"},
        {"id": "auth.sign-out.clears-session", "area": "auth", "status": "covered"},
        {"id": "org.members.remove.requires-admin", "area": "org", "status": "uncovered"}]})
    code, out = run(capsys, "check", str(tmp_path), "--tiles", tiles)
    assert code == 1
    assert rules(out) == {"uncased-tile"}, out
    assert "%s:0 uncased-tile: " % tiles in out
    assert out.count("uncased-tile") == 1  # once per tile, and only the covered one


def test_dangling_automated_id(tmp_path, capsys):
    write_case(tmp_path, case_text())
    tests = write_json(tmp_path / "tests.json", {"tests": [
        {"id": "e2e/auth.spec.ts::some other test",
         "tiles": ["auth.sign-in.valid-password"]}]})
    code, out = run(capsys, "check", str(tmp_path), "--tests", tests)
    assert code == 1
    assert rules(out) == {"dangling-test"}, out


def test_automated_test_claims_no_case_tile(tmp_path, capsys):
    write_case(tmp_path, case_text())
    tests = write_json(tmp_path / "tests.json", {"tests": [
        {"id": "e2e/auth.spec.ts::signs in with a valid password",
         "tiles": ["credentials.create.name-validation"]}]})
    code, out = run(capsys, "check", str(tmp_path), "--tests", tests)
    assert code == 1
    assert rules(out) == {"test-tile-mismatch"}, out


def test_clean_case_with_both_files(tmp_path, capsys):
    write_case(tmp_path, case_text())
    tiles = write_json(tmp_path / "tiles.json", {"tiles": [
        {"id": "auth.sign-in.valid-password", "area": "auth", "status": "covered"},
        {"id": "auth.login.render.anonymous", "area": "auth", "status": "covered"}]})
    tests = write_json(tmp_path / "tests.json", {"tests": [
        {"id": "e2e/auth.spec.ts::signs in with a valid password",
         "tiles": ["auth.sign-in.valid-password", "auth.login.render.anonymous"]}]})
    code, out = run(capsys, "check", str(tmp_path), "--tiles", tiles, "--tests", tests)
    assert code == 0, out


def test_unreadable_tiles_file_exits_two(tmp_path, capsys):
    write_case(tmp_path, case_text())
    broken = tmp_path / "tiles.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        cases.main(["check", str(tmp_path), "--tiles", str(broken)])
    assert excinfo.value.code == 2


# --------------------------------------------------------------------------- exports


def test_azure_csv_header_and_row_per_step(tmp_path, capsys):
    write_case(tmp_path, case_text())
    out_path = tmp_path / "out" / "cases.csv"
    code, _ = run(capsys, "export", str(tmp_path), "--format", "azure-csv", "--out", str(out_path))
    assert code == 0
    text = out_path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == (
        "ID,Work Item Type,Title,Test Step,Step Action,Step Expected,"
        "Area Path,Assigned To,State")
    rows = list(csv.reader(text.splitlines()))
    assert len(rows) == 1 + len(DEFAULT_STEPS)
    assert [row[3] for row in rows[1:]] == ["1", "2"]
    assert [row[2] for row in rows[1:]] == ["TC-auth-001: Sign in with a valid password"] * 2
    assert rows[1] == ["", "Test Case", "TC-auth-001: Sign in with a valid password", "1",
                       "Open /login", "The sign-in form shows email and password fields",
                       "auth", "", "Design"]


def test_gherkin_one_scenario_per_case(tmp_path, capsys):
    root = tmp_path / "testcases"
    write_case(root, case_text())
    write_case(root, case_text(cid="TC-auth-002", title="Reject a wrong password",
                               meta=meta_with(status="manual", automated="")),
               name="TC-auth-002.md")
    write_case(root, case_text(cid="TC-org-001", title="See the members list",
                               meta=meta_with(area="org", status="planned", automated="",
                                              tiles="org.home.render.member")),
               name="TC-org-001.md", area="org")
    out_dir = tmp_path / "features"
    code, _ = run(capsys, "export", str(root), "--format", "gherkin", "--out", str(out_dir))
    assert code == 0
    auth = (out_dir / "auth.feature").read_text(encoding="utf-8")
    org = (out_dir / "org.feature").read_text(encoding="utf-8")
    assert auth.startswith("Feature: auth")
    assert auth.count("  Scenario: ") == 2
    assert "  Scenario: TC-auth-001 Sign in with a valid password" in auth
    assert "    Given a member account exists with a known password" in auth
    assert "    When Open /login" in auth
    assert "    Then The sign-in form shows email and password fields" in auth
    assert "    And Enter the email and password, press Sign in" in auth
    assert "    And The organisation page opens" in auth
    assert org.count("  Scenario: ") == 1
    assert sorted(p.name for p in out_dir.iterdir()) == ["auth.feature", "org.feature"]


def test_gherkin_skips_a_none_precondition(tmp_path, capsys):
    write_case(tmp_path, case_text(preconditions=["none"]))
    out_dir = tmp_path / "features"
    code, _ = run(capsys, "export", str(tmp_path), "--format", "gherkin", "--out", str(out_dir))
    assert code == 0
    assert "Given" not in (out_dir / "auth.feature").read_text(encoding="utf-8")


def test_markdown_index_names_every_case(tmp_path, capsys):
    root = tmp_path / "testcases"
    write_case(root, case_text())
    write_case(root, case_text(cid="TC-org-001", title="See the members list",
                               meta=meta_with(area="org", status="planned", automated="",
                                              tiles="org.home.render.member")),
               name="TC-org-001.md", area="org")
    out_path = root / "README.md"
    code, _ = run(capsys, "export", str(root), "--format", "markdown", "--out", str(out_path))
    assert code == 0
    text = out_path.read_text(encoding="utf-8")
    assert "| ID | Title | Status | Priority | Tiles | Automated |" in text
    assert "## auth" in text and "## org" in text
    for cid in ("TC-auth-001", "TC-org-001"):
        assert cid in text
    assert "| TC-org-001 | See the members list | planned | high | org.home.render.member |  |" in text


def test_export_refuses_on_a_lint_failure(tmp_path, capsys):
    write_case(tmp_path, case_text(meta=meta_with(status="broken")))
    out_path = tmp_path / "out" / "cases.csv"
    code, out = run(capsys, "export", str(tmp_path), "--format", "azure-csv", "--out", str(out_path))
    assert code == 1
    assert "export refused" in out
    assert not out_path.exists()


# --------------------------------------------------------------------------- index


def test_index_shape(tmp_path, capsys):
    path = write_case(tmp_path, case_text())
    code, out = run(capsys, "index", str(tmp_path))
    assert code == 0
    data = json.loads(out)
    assert data == [{
        "id": "TC-auth-001",
        "area": "auth",
        "tiles": ["auth.sign-in.valid-password", "auth.login.render.anonymous"],
        "status": "automated",
        "automated": ["e2e/auth.spec.ts::signs in with a valid password"],
        "path": str(path),
    }]


def test_cases_are_found_recursively_and_ids_are_unique(tmp_path, capsys):
    write_case(tmp_path, case_text(), area="auth/deeper")
    write_case(tmp_path, case_text(), area="org")
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    assert "duplicate-id" in rules(out), out
    assert "2 cases checked" in out
