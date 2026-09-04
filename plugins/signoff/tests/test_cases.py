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
    return set(finding_lines(out))


def finding_lines(out):
    """One rule name per finding line, in order: a count, where rules() is a set."""
    found = []
    for line in out.splitlines():
        match = re.match(r"^\S+:\d+ ([a-z-]+): ", line)
        if match:
            found.append(match.group(1))
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
    assert finding_lines(out) == ["automated-mismatch"], out  # exactly one finding


def test_automated_case_without_a_test(tmp_path, capsys):
    write_case(tmp_path, case_text(meta=meta_with(automated="")))
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    assert finding_lines(out) == ["automated-mismatch"], out  # exactly one finding


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
    # One failure shape across all five scripts (F12): `path:line rule: reason`.
    assert capsys.readouterr().err.startswith("cases.py: %s:0 unreadable: " % broken)


def test_a_tiles_file_without_its_list_exits_two(tmp_path, capsys):
    write_case(tmp_path, case_text())
    tiles = write_json(tmp_path / "tiles.json", {"tiled_at": "2026-09-04T18:30:00Z"})
    with pytest.raises(SystemExit) as excinfo:
        cases.main(["check", str(tmp_path), "--tiles", tiles])
    assert excinfo.value.code == 2
    assert capsys.readouterr().err == "cases.py: %s:0 malformed: no `tiles` list\n" % tiles


# --------------------------------------------------------------------------- F3: the area


def test_an_area_that_is_a_path_is_one_bad_area_finding(tmp_path, capsys):
    """An area is joined into a file name by the gherkin export, so it is
    validated as its own rule before anything is built from it - and the id
    mismatch it also has is not reported twice."""
    write_case(tmp_path, case_text(meta=meta_with(area="../../x")))
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    assert finding_lines(out) == ["bad-area"], out


def test_a_bad_area_refuses_the_gherkin_export(tmp_path, capsys):
    root = tmp_path / "testcases"
    write_case(root, case_text(meta=meta_with(area="../../x")))
    out_dir = tmp_path / "features"
    code, out = run(capsys, "export", str(root), "--format", "gherkin", "--out", str(out_dir))
    assert code == 1
    assert "export refused" in out
    # Nothing was written anywhere, least of all where the area pointed.
    assert not out_dir.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["testcases"]


@pytest.mark.parametrize("area", ["Auth", "auth/deeper", "auth.login", "-auth", "2fa"])
def test_every_area_outside_the_pattern_is_a_bad_area(tmp_path, capsys, area):
    write_case(tmp_path, case_text(meta=meta_with(area=area)))
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    assert "bad-area" in rules(out), out


# --------------------------------------------------------------------------- F9: the steps table

CASE_WITH_A_TABLE_IN_ITS_PROSE = """# TC-auth-001: Sign in with a valid password

- area: auth
- tiles: auth.sign-in.valid-password
- role: anonymous
- priority: high
- status: manual
- automated:

## Preconditions

- a member account exists with a known password

## Steps

| # | Action | Expected |
|---|--------|----------|
| 1 | Open /login | The sign-in form shows email and password fields |
| 2 | Enter the email and password, press Sign in | The organisation page opens |

The accounts this case can be run with, for reference only:

| role | email |
|---|---|
| member | member@example.invalid |
| admin | admin@example.invalid |
"""


def test_a_table_in_the_prose_after_the_steps_is_prose(tmp_path, capsys):
    """The steps are the first table after `## Steps` only (F9)."""
    root = tmp_path / "testcases"
    write_case(root, CASE_WITH_A_TABLE_IN_ITS_PROSE)
    code, out = run(capsys, "check", str(root))
    assert code == 0, out

    out_dir = tmp_path / "features"
    code, _ = run(capsys, "export", str(root), "--format", "gherkin", "--out", str(out_dir))
    assert code == 0
    feature = (out_dir / "auth.feature").read_text(encoding="utf-8")
    assert feature.count("    When ") + feature.count("    And Open") == 1
    assert "    When Open /login" in feature
    assert "    And Enter the email and password, press Sign in" in feature
    # The reference table's cells are nowhere in the export.
    assert "member@example.invalid" not in feature
    assert "role" not in feature


def test_the_steps_table_is_still_read_when_prose_follows_it(tmp_path, capsys):
    """The same case through the CSV export: exactly two step rows."""
    root = tmp_path / "testcases"
    write_case(root, CASE_WITH_A_TABLE_IN_ITS_PROSE)
    out_path = tmp_path / "cases.csv"
    code, _ = run(capsys, "export", str(root), "--format", "azure-csv", "--out", str(out_path))
    assert code == 0
    rows = list(csv.reader(out_path.read_text(encoding="utf-8").splitlines()))
    assert [row[3] for row in rows[1:]] == ["1", "2"]


# --------------------------------------------------------------------------- F10: the size guards


def test_a_case_past_the_size_cap_is_not_read(tmp_path, capsys, monkeypatch):
    """The guard is the size, so the content is never parsed: this case has
    no sections at all and would otherwise report several findings."""
    write_case(tmp_path, "# TC-auth-001: Sign in\n")
    monkeypatch.setattr(cases.os.path, "getsize", lambda path: cases.MAX_CASE_BYTES + 1)
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    assert finding_lines(out) == ["too-large"], out


def test_a_control_character_in_a_value_never_reaches_the_finding(tmp_path, capsys):
    """F16: a case file cannot hold a newline inside a metadata value - that
    is two lines of Markdown - but it can hold an escape sequence, which a
    terminal would act on and a reader would never see."""
    write_case(tmp_path, case_text(
        meta=meta_with(priority="urgent\x1b[2K0 cases checked, 0 findings")))
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    assert finding_lines(out) == ["bad-value"], out
    assert "\x1b" not in out
    assert "urgent?[2K0 cases checked, 0 findings" in out
    assert out.strip().endswith("1 cases checked, 1 findings")


def test_a_very_long_value_is_cut_in_the_finding(tmp_path, capsys):
    write_case(tmp_path, case_text(meta=meta_with(priority="u" * 300)))
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    assert cases.CUT_MARK in out
    assert "u" * (cases.TEXT_CHARS + 1) not in out


def test_the_finding_path_is_the_case_files_own(tmp_path, capsys):
    """The path is capped like any other, so this asserts the environment's
    temporary path is short enough for the assertion above it to mean what it
    says, rather than failing mysteriously on a long TMPDIR."""
    path = write_case(tmp_path, case_text(meta=meta_with(priority="urgent")))
    assert len(str(path)) <= cases.TEXT_CHARS
    code, out = run(capsys, "check", str(tmp_path))
    assert code == 1
    assert out.splitlines()[0].startswith("%s:6 bad-value: " % path)


def test_a_tiles_file_past_the_size_cap_is_refused_unread(tmp_path, capsys, monkeypatch):
    write_case(tmp_path, case_text())
    tiles = write_json(tmp_path / "tiles.json", {"tiles": [
        {"id": "auth.sign-in.valid-password", "area": "auth", "status": "covered"}]})
    monkeypatch.setattr(cases.os.path, "getsize", lambda path: cases.MAX_JSON_BYTES + 1)
    with pytest.raises(SystemExit) as excinfo:
        cases.main(["check", str(tmp_path), "--tiles", tiles])
    assert excinfo.value.code == 2
    assert capsys.readouterr().err.startswith("cases.py: %s:0 too-large: " % tiles)


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
