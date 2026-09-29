import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "preflighting-workflows" / "scripts" / "preflight.py"
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "planted-workflows"
ANSWER_KEY = FIXTURE_ROOT / "ANSWER-KEY.md"

_KEY_LINE = re.compile(r"^- (\S+):(\d+)\s+(\S+)\s+—")


def parse_answer_key():
    entries = []
    for line in ANSWER_KEY.read_text().splitlines():
        m = _KEY_LINE.match(line)
        if m:
            entries.append((m.group(1), int(m.group(2)), m.group(3)))
    return entries


def _stub(bindir: Path, name: str, body: str, **payloads: str):
    """Write a stub tool. `body` is sh; `cat $NAME` prints payload NAME."""
    for key, text in payloads.items():
        (bindir / f"{name}.{key}").write_text(text)
        body = body.replace(f"cat ${key}", f"cat '{bindir / f'{name}.{key}'}'")
    p = bindir / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def run(root: Path, bindir: Path, *args, extra_env=None):
    env = {**os.environ, "PATH": f"{bindir}:/usr/bin:/bin"}
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(SCRIPT), str(root), *args], capture_output=True, text=True, env=env)


def _write_workflows(tmp_path: Path, files: dict) -> Path:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    for name, text in files.items():
        (wf / name).write_text(text)
    return tmp_path


def _empty_bindir(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    return bindir


# --------------------------------------------------------------------------
# The answer key is the contract
# --------------------------------------------------------------------------


def test_every_planted_defect_is_reported(tmp_path):
    bindir = _empty_bindir(tmp_path)  # no actionlint/zizmor noise, no network
    r = run(FIXTURE_ROOT, bindir, "--org", "konyklabs", "--json")
    assert r.returncode == 2, r.stdout + r.stderr
    doc = json.loads(r.stdout)
    seen = set()
    for row in doc["rows"]:
        if row["check"] in ("actionlint", "zizmor"):
            continue
        for f in row["findings"]:
            seen.add((f["path"], f["line"], f["rule"]))
    key = parse_answer_key()
    assert key, f"parsed 0 entries from {ANSWER_KEY}"
    expected = {(f".github/workflows/{file}", line, rule) for file, line, rule in key}
    # Both directions (A5): a planted defect never reported fails, and so
    # does a structural or parse finding the answer key does not list.
    assert not expected - seen, f"planted defects never reported: {sorted(expected - seen)}"
    assert not seen - expected, f"false positives: {sorted(seen - expected)}"


def test_clean_fixture_alone_has_no_structural_finding_and_exits_zero(tmp_path):
    clean_text = (FIXTURE_ROOT / ".github" / "workflows" / "clean.yml").read_text()
    root = _write_workflows(tmp_path, {"clean.yml": clean_text})
    bindir = _empty_bindir(tmp_path)
    r = run(root, bindir, "--org", "konyklabs", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    doc = json.loads(r.stdout)
    structural = [row for row in doc["rows"] if row["check"] not in ("actionlint", "zizmor")]
    for row in structural:
        assert row["findings"] == [], row
    assert doc["ok"] is True


# --------------------------------------------------------------------------
# --allow-unpinned
# --------------------------------------------------------------------------


def test_allow_unpinned_removes_matching_uses_unpinned_rows(tmp_path):
    bindir = _empty_bindir(tmp_path)
    baseline = run(FIXTURE_ROOT, bindir, "--org", "konyklabs", "--json")
    base_rows = {row["check"]: row for row in json.loads(baseline.stdout)["rows"]}
    base_findings = {(f["path"], f["line"]) for f in base_rows["uses-unpinned"]["findings"]}
    assert (".github/workflows/ci.yml", 13) in base_findings  # actions/checkout@v4, unpinned by default

    allowed = run(FIXTURE_ROOT, bindir, "--org", "konyklabs", "--json", "--allow-unpinned", "actions/checkout@v4")
    rows = {row["check"]: row for row in json.loads(allowed.stdout)["rows"]}
    findings = {(f["path"], f["line"]) for f in rows["uses-unpinned"]["findings"]}
    assert (".github/workflows/ci.yml", 13) not in findings  # exempted by the prefix
    assert (".github/workflows/ci.yml", 15) in findings      # setup-node still unpinned: prefix was specific


def test_default_allowlist_exempts_konyklabs_github_reusable_workflows(tmp_path):
    root = _write_workflows(tmp_path, {"caller.yml": (
        # No timeout-minutes: GitHub rejects it on a job that calls a
        # reusable workflow (M9).
        "name: caller\n\njobs:\n  call:\n    permissions:\n      contents: read\n"
        "    uses: konyklabs/.github/.github/workflows/x.yml@main\n"
    )})
    bindir = _empty_bindir(tmp_path)
    r = run(root, bindir, "--json")  # no --allow-unpinned: exercises the built-in default
    rows = {row["check"]: row for row in json.loads(r.stdout)["rows"]}
    assert rows["uses-unpinned"]["findings"] == []


# --------------------------------------------------------------------------
# External tools: missing, stubbed, timed out
# --------------------------------------------------------------------------


def test_no_tools_on_path_both_tool_rows_are_skip(tmp_path):
    root = _write_workflows(tmp_path, {"clean.yml": (FIXTURE_ROOT / ".github" / "workflows" / "clean.yml").read_text()})
    bindir = _empty_bindir(tmp_path)
    r = run(root, bindir, "--json")
    rows = {row["check"]: row for row in json.loads(r.stdout)["rows"]}
    assert rows["actionlint"]["status"] == "skip" and rows["actionlint"]["note"]
    assert rows["zizmor"]["status"] == "skip" and rows["zizmor"]["note"]


def test_stubbed_tools_are_parsed_to_path_line_rule(tmp_path):
    root = _write_workflows(tmp_path, {"clean.yml": (FIXTURE_ROOT / ".github" / "workflows" / "clean.yml").read_text()})
    bindir = _empty_bindir(tmp_path)
    al = json.dumps([{"message": "shellcheck warning", "filepath": ".github/workflows/clean.yml", "line": 21, "column": 1, "kind": "shellcheck"}])
    _stub(bindir, "actionlint", 'cat $P; exit 1', P=al)
    zz = json.dumps([{"ident": "template-injection", "determinations": {"severity": "high"},
                       "locations": [{"symbolic": {"key_path": ""}, "concrete": {"path": ".github/workflows/clean.yml", "location": {"start_point": {"row": 20}}}}]}])
    _stub(bindir, "zizmor", 'cat $P; exit 1', P=zz)
    r = run(root, bindir, "--json")
    rows = {row["check"]: row for row in json.loads(r.stdout)["rows"]}
    assert rows["actionlint"]["status"] == "fail" and rows["actionlint"]["count"] == 1
    assert rows["actionlint"]["findings"][0] == {"path": ".github/workflows/clean.yml", "line": 21, "rule": "actionlint/shellcheck"}
    assert rows["zizmor"]["status"] == "fail" and rows["zizmor"]["count"] == 1
    assert rows["zizmor"]["findings"][0]["rule"] == "zizmor/template-injection"
    assert rows["zizmor"]["findings"][0]["line"] == 21  # 0-based row + 1


def test_tool_that_hangs_past_timeout_is_skip_not_pass(tmp_path):
    root = _write_workflows(tmp_path, {"clean.yml": (FIXTURE_ROOT / ".github" / "workflows" / "clean.yml").read_text()})
    bindir = _empty_bindir(tmp_path)
    _stub(bindir, "actionlint", "sleep 5; echo '[]'")
    r = run(root, bindir, "--json", extra_env={"PREFLIGHT_TOOL_TIMEOUT": "1"})
    rows = {row["check"]: row for row in json.loads(r.stdout)["rows"]}
    assert rows["actionlint"]["status"] == "skip"
    assert "timeout" in rows["actionlint"]["note"].lower() or rows["actionlint"]["note"] == "timeout"


def test_tool_garbage_output_is_skip_not_pass(tmp_path):
    root = _write_workflows(tmp_path, {"clean.yml": (FIXTURE_ROOT / ".github" / "workflows" / "clean.yml").read_text()})
    bindir = _empty_bindir(tmp_path)
    _stub(bindir, "zizmor", "echo 'not json'; exit 1")
    r = run(root, bindir, "--json")
    rows = {row["check"]: row for row in json.loads(r.stdout)["rows"]}
    assert rows["zizmor"]["status"] == "skip"


# --------------------------------------------------------------------------
# Exit codes
# --------------------------------------------------------------------------


def test_exit_code_two_with_a_blocking_finding():
    bindir_holder = FIXTURE_ROOT  # any empty-tool PATH works; use real PATH minus tools via env override below
    r = subprocess.run([sys.executable, str(SCRIPT), str(FIXTURE_ROOT), "--org", "konyklabs"], capture_output=True, text=True)
    assert r.returncode == 2, r.stdout + r.stderr


def test_exit_code_zero_for_clean_fixture_alone(tmp_path):
    root = _write_workflows(tmp_path, {"clean.yml": (FIXTURE_ROOT / ".github" / "workflows" / "clean.yml").read_text()})
    bindir = _empty_bindir(tmp_path)
    r = run(root, bindir, "--org", "konyklabs")
    assert r.returncode == 0, r.stdout + r.stderr


def test_exit_code_one_when_no_workflows_directory(tmp_path):
    bindir = _empty_bindir(tmp_path)
    r = run(tmp_path, bindir)
    assert r.returncode == 1


def test_exit_code_one_when_workflows_directory_is_empty(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    bindir = _empty_bindir(tmp_path)
    r = run(tmp_path, bindir)
    assert r.returncode == 1


# --------------------------------------------------------------------------
# --json shape and the no-leaked-text rule
# --------------------------------------------------------------------------


def test_json_shape(tmp_path):
    bindir = _empty_bindir(tmp_path)
    r = run(FIXTURE_ROOT, bindir, "--org", "konyklabs", "--json")
    doc = json.loads(r.stdout)
    assert set(doc.keys()) >= {"ok", "root", "rows"}
    assert isinstance(doc["ok"], bool)
    for row in doc["rows"]:
        assert set(row.keys()) >= {"check", "status", "severity", "count", "findings", "note"}
        assert row["status"] in ("pass", "fail", "skip")
        assert row["severity"] in ("blocking", "minor")
        for f in row["findings"]:
            assert set(f.keys()) == {"path", "line", "rule"}


def test_no_workflow_text_leaks_beyond_paths_lines_and_rule_ids(tmp_path):
    bindir = _empty_bindir(tmp_path)
    text_out = run(FIXTURE_ROOT, bindir, "--org", "konyklabs")
    json_out = run(FIXTURE_ROOT, bindir, "--org", "konyklabs", "--json")
    for r in (text_out, json_out):
        assert "AKIAPLANTEDEXAMPLE0" not in r.stdout
        assert "AKIAPLANTEDEXAMPLE0" not in r.stderr


def test_findings_are_capped_at_twenty_per_rule(tmp_path):
    many = ["      - name: step{0}\n        uses: actions/checkout@v{0}\n".format(i) for i in range(30)]
    text = "name: many\n\njobs:\n  build:\n    permissions:\n      contents: read\n    timeout-minutes: 5\n    steps:\n" + "".join(many)
    root = _write_workflows(tmp_path, {"many.yml": text})
    bindir = _empty_bindir(tmp_path)
    r = run(root, bindir, "--org", "konyklabs")
    assert "…+" in r.stdout


# --------------------------------------------------------------------------
# Python 3.9 floor
# --------------------------------------------------------------------------


def test_script_compiles_under_python_3_9():
    py39 = shutil.which("python3.9")
    if not py39:
        import pytest
        pytest.skip(f"no python3.9 on PATH={os.environ.get('PATH', '')}")
    r = subprocess.run([py39, "-m", "py_compile", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_script_source_has_no_match_statement():
    src = SCRIPT.read_text()
    # A `match <expr>:` statement is 3.10+ grammar; a bare identifier named
    # `match` (e.g. `_KEY_RE.match(...)`) is not this pattern.
    assert not re.search(r"(?m)^\s*match\s+[^=\n]+:\s*$", src)


# --------------------------------------------------------------------------
# Review round fixes: hostile scenarios, one test each, exact rows asserted
# --------------------------------------------------------------------------

HOSTILE = Path(__file__).resolve().parent / "fixtures" / "hostile"
CANARY = "AKIAPLANTEDEXAMPLE0"
_TOOLS = ("actionlint", "zizmor")
W = ".github/workflows/w.yml"


def _json(root: Path, bindir: Path, *args):
    r = run(root, bindir, "--org", "konyklabs", "--json", *args)
    assert r.returncode in (0, 2), r.stdout + r.stderr
    return r, json.loads(r.stdout)


def _rows(doc) -> set:
    """Every (rule, path, line) the structural and parse rows reported."""
    out = set()
    for row in doc["rows"]:
        if row["check"] in _TOOLS:
            continue
        for f in row["findings"]:
            out.add((f["rule"], f["path"], f["line"]))
    return out


def _hostile(name: str, tmp_path: Path, *args):
    r, doc = _json(HOSTILE / name, _empty_bindir(tmp_path), *args)
    assert CANARY not in r.stdout + r.stderr
    return r, doc


def test_b1_indentless_steps_sequence_is_parsed(tmp_path):
    r, doc = _hostile("indentless-steps", tmp_path)
    assert _rows(doc) == {("uses-unpinned", W, 8), ("expression-injection", W, 9)}
    assert r.returncode == 2


def test_b1_indentless_sequence_then_sibling_key_still_parsed(tmp_path):
    root = _write_workflows(tmp_path, {"w.yml": (
        "on: push\npermissions: {}\njobs:\n  a:\n    runs-on: x\n    steps:\n    - run: echo hi\n"
        "    timeout-minutes: 1\n  b:\n    runs-on: x\n    timeout-minutes: 1\n    steps:\n"
        "    - uses: a/b@v1\n")})
    r, doc = _json(root, _empty_bindir(tmp_path))
    # Job a's timeout after the indentless list is seen; job b is reached.
    assert _rows(doc) == {("uses-unpinned", W, 13)}


def test_b2_wrapped_plain_scalar_does_not_end_the_parse(tmp_path):
    r, doc = _hostile("wrapped-plain-scalar", tmp_path)
    assert _rows(doc) == {("uses-unpinned", W, 14), ("expression-injection", W, 15), ("timeout-missing", W, 11)}
    assert r.returncode == 2


def test_b2_plain_scalar_continuation_line_is_absorbed(tmp_path):
    r, doc = _hostile("contline", tmp_path)
    assert _rows(doc) == {("uses-unpinned", W, 15), ("expression-injection", W, 16), ("timeout-missing", W, 12)}
    assert r.returncode == 2


def test_b2_unplaceable_line_is_parse_incomplete_and_rest_is_checked(tmp_path):
    root = _write_workflows(tmp_path, {"w.yml": (
        "on: push\npermissions: {}\njobs:\n  a:\n    runs-on: x\n    timeout-minutes: 1\n"
        "      stray: 1\n    steps:\n      - run: echo \"${{ github.event.issue.title }}\"\n")})
    r, doc = _json(root, _empty_bindir(tmp_path))
    assert _rows(doc) == {("parse-incomplete", W, 7), ("expression-injection", W, 9)}
    rows = {row["check"]: row for row in doc["rows"]}
    assert rows["parse"]["status"] == "fail" and rows["parse"]["severity"] == "blocking"
    assert r.returncode == 2


def test_b2_unplaceable_line_alone_exits_two_not_zero(tmp_path):
    root = _write_workflows(tmp_path, {"w.yml": (
        "on: push\npermissions: {}\njobs:\n  a:\n    runs-on: x\n    timeout-minutes: 1\n"
        "    steps:\n      - - nested\n")})
    r, doc = _json(root, _empty_bindir(tmp_path))
    assert _rows(doc) == {("parse-incomplete", W, 8)}
    assert r.returncode == 2 and doc["ok"] is False


def test_b3_deep_nesting_is_parse_error_not_a_pass(tmp_path):
    r, doc = _hostile("deep", tmp_path)
    assert _rows(doc) == {("parse-error", W, 1)}
    assert r.returncode == 2


def test_b3_any_parser_exception_is_parse_error_without_its_text(tmp_path, monkeypatch, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("preflight_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def boom(text):
        raise ValueError(CANARY)

    monkeypatch.setattr(mod, "parse_workflow_full", boom)
    monkeypatch.setenv("PATH", str(_empty_bindir(tmp_path)))
    root = _write_workflows(tmp_path, {"w.yml": "on: push\n"})
    rc = mod.main([str(root), "--org", "konyklabs", "--json"])
    out = capsys.readouterr()
    assert rc == 2
    assert CANARY not in out.out + out.err
    assert _rows(json.loads(out.out)) == {("parse-error", W, 1)}


def test_m1_run_block_as_first_item_key_keeps_env_out_of_the_body(tmp_path):
    r, doc = _hostile("run-block-first-key", tmp_path)
    assert _rows(doc) == set()
    assert r.returncode == 0


def test_m2_quoted_values_are_unquoted_before_rules_read_them(tmp_path):
    r, doc = _hostile("quoted-values", tmp_path)
    assert _rows(doc) == set()  # quoted SHA pin, quoted ./local, quoted own-org callee
    assert r.returncode == 0
    r2 = run(HOSTILE / "quoted-values", tmp_path / "bin", "--org", "someone-else", "--json")
    assert _rows(json.loads(r2.stdout)) == {("secrets-inherit-external", W, 6)}


def test_m3_pull_request_target_head_refs_flagged_base_ref_not(tmp_path):
    r, doc = _hostile("prt-refs", tmp_path)
    assert _rows(doc) == {("pull-request-target-checkout", W, 18)}  # line 11 is the base sha
    assert r.returncode == 2


def test_m3_merge_ref_quoted_ref_and_head_ref_flagged(tmp_path):
    sha = "0123456789abcdef0123456789abcdef01234567"
    step = "      - uses: actions/checkout@" + sha + " # v4\n        with:\n          ref: {}\n"
    root = _write_workflows(tmp_path, {"w.yml": (
        "on: [push, pull_request_target]\npermissions: {}\njobs:\n  a:\n    runs-on: x\n    timeout-minutes: 1\n    steps:\n"
        + step.format('"refs/pull/${{ github.event.pull_request.number }}/merge"')
        + step.format("${{ github.head_ref }}")
        + step.format("${{ github.event.pull_request.head.ref }}")
        + step.format("${{ github.event.pull_request.base.ref }}")
        + step.format("main"))})
    r, doc = _json(root, _empty_bindir(tmp_path))
    assert _rows(doc) == {("pull-request-target-checkout", W, n) for n in (10, 13, 16)}


def test_m4_injection_forms_inside_functions_and_operators(tmp_path):
    r, doc = _hostile("injection-forms", tmp_path)
    # 8 repository.name, 9 head_commit.id, 10 number and sha: not flagged
    assert _rows(doc) == {("expression-injection", W, n) for n in (11, 12, 13)}
    assert r.returncode == 2


def test_m4_injection_path_list(tmp_path):
    flagged = [
        "github.head_ref", "github.event.pull_request.head.ref", "github.event.pull_request.head.label",
        "github.event.workflow_run.head_branch", "github.event.commits[0].message", "github.event.commits",
        "github.event.head_commit.author.email", "github.event.head_commit.message",
        "github['event']['issue']['title']", "github.event.pages[0].page_name",
        "github.event.repository.default_branch", "github.event.comment.body", "GitHub.Event.Issue.Title",
    ]
    clean = [
        "github.event.repository.name", "github.event.head_commit.id", "github.event.pull_request.number",
        "github.sha", "github.event.pull_request.head.sha", "github.ref", "github.event.ref",
    ]
    steps = "".join("      - run: echo \"${{%s}}\"\n" % e for e in flagged + clean)
    root = _write_workflows(tmp_path, {"w.yml": (
        "on: push\npermissions: {}\njobs:\n  a:\n    runs-on: x\n    timeout-minutes: 1\n    steps:\n" + steps
        + "      - uses: actions/github-script@0123456789abcdef0123456789abcdef01234567 # v7\n"
        "        with:\n          script: |\n            core.info(`${{ github.event.issue.body }}`)\n")})
    r, doc = _json(root, _empty_bindir(tmp_path))
    expected = {("expression-injection", W, 8 + i) for i in range(len(flagged))}
    expected.add(("expression-injection", W, 8 + len(flagged) + len(clean) + 3))
    assert _rows(doc) == expected


def test_m5_actionlint_non_integer_line_is_skip_and_never_echoed(tmp_path):
    root = _write_workflows(tmp_path, {"clean.yml": (FIXTURE_ROOT / ".github" / "workflows" / "clean.yml").read_text()})
    for i, payload in enumerate([
        [{"filepath": "a.yml", "line": CANARY, "kind": "x", "message": "m"}],   # the deep lens's stub
        [{"filepath": "a.yml", "line": 1.5, "kind": "x"}],
        [{"filepath": 7, "line": 1, "kind": "x"}],
        [{"filepath": "a.yml", "kind": "x"}],
        [{"filepath": "a.yml", "line": True, "kind": "x"}],
        {"filepath": "a.yml", "line": 1, "kind": "x"},
        [CANARY],
    ]):
        bindir = tmp_path / f"bin{i}"
        bindir.mkdir()
        _stub(bindir, "actionlint", "cat $P; exit 1", P=json.dumps(payload))
        r = run(root, bindir, "--json")
        assert r.returncode in (0, 2), (i, r.stdout + r.stderr)
        assert CANARY not in r.stdout + r.stderr
        rows = {row["check"]: row for row in json.loads(r.stdout)["rows"]}
        assert rows["actionlint"]["status"] == "skip" and rows["actionlint"]["note"] == "unparseable output", i


def test_m5_zizmor_malformed_items_are_skip(tmp_path):
    root = _write_workflows(tmp_path, {"clean.yml": (FIXTURE_ROOT / ".github" / "workflows" / "clean.yml").read_text()})
    for i, payload in enumerate([[1, 2], {"a": 1}, [{"ident": 5}]]):
        bindir = tmp_path / f"bin{i}"
        bindir.mkdir()
        _stub(bindir, "zizmor", "cat $P; exit 14", P=json.dumps(payload))
        r = run(root, bindir, "--json")
        rows = {row["check"]: row for row in json.loads(r.stdout)["rows"]}
        assert rows["zizmor"]["status"] == "skip" and rows["zizmor"]["note"] == "unparseable output", i


def test_m6_zizmor_given_path_shape_and_fallback_note(tmp_path):
    root = _write_workflows(tmp_path, {"clean.yml": (FIXTURE_ROOT / ".github" / "workflows" / "clean.yml").read_text()})
    zz = json.dumps([
        {"ident": "artipacked", "determinations": {"severity": "medium"},
         "locations": [{"symbolic": {"key": {"Local": {"given_path": ".github/workflows/clean.yml"}}},
                        "concrete": {"location": {"start_point": {"row": 19}}}}]},
        {"ident": "excessive-permissions", "determinations": {"severity": "high"}, "locations": []},
    ])
    bindir = _empty_bindir(tmp_path)
    _stub(bindir, "zizmor", "cat $P; exit 14", P=zz)
    r = run(root, bindir, "--json")
    row = {row["check"]: row for row in json.loads(r.stdout)["rows"]}["zizmor"]
    assert row["status"] == "fail" and row["count"] == 2
    assert row["findings"][0] == {"path": ".github/workflows/clean.yml", "line": 20, "rule": "zizmor/artipacked"}
    assert row["findings"][1] == {"path": "?", "line": 0, "rule": "zizmor/excessive-permissions"}
    assert "location not found" in row["note"]


def test_m7_json_is_capped_with_truncated_count_and_root_is_a_basename(tmp_path):
    r, doc = _hostile("finding-cap", tmp_path)
    row = {row["check"]: row for row in doc["rows"]}["uses-unpinned"]
    assert row["count"] == 25 and len(row["findings"]) == 20 and row["truncated"] == 5
    assert doc["root"] == "finding-cap"
    assert "/" not in doc["root"]
    text = run(HOSTILE / "finding-cap", tmp_path / "bin", "--org", "konyklabs")
    assert "…+5" in text.stdout


def test_m8_flow_mapping_trigger_and_block_scalar(tmp_path):
    r, doc = _hostile("flow-trigger", tmp_path)
    # on: {pull_request_target: {}}; `run: >` folded block; docker digest pin is fine
    assert _rows(doc) == {("pull-request-target-checkout", W, 10), ("expression-injection", W, 12)}
    assert r.returncode == 2


def test_m8_flow_sequence_trigger_and_flow_step_item(tmp_path):
    root = _write_workflows(tmp_path, {"w.yml": (
        "on: [pull_request_target]\npermissions: {contents: read}\njobs:\n  a:\n    runs-on: x\n    timeout-minutes: 1\n    steps:\n"
        "      - {uses: actions/checkout@v6, with: {ref: ${{ github.event.pull_request.head.sha }}}}\n"
        "      - {run: 'echo ${{ github.event.issue.title }}'}\n")})
    r, doc = _json(root, _empty_bindir(tmp_path))
    assert _rows(doc) == {("uses-unpinned", W, 8), ("pull-request-target-checkout", W, 8), ("expression-injection", W, 9)}


def test_m8_unreadable_flow_value_is_parse_incomplete(tmp_path):
    root = _write_workflows(tmp_path, {"w.yml": (
        "on: push\npermissions: {}\njobs:\n  a:\n    runs-on: x\n    timeout-minutes: 1\n    steps:\n"
        "      - uses: actions/checkout@v6\n        with: {ref: [a: b]}\n")})
    r, doc = _json(root, _empty_bindir(tmp_path))
    assert _rows(doc) == {("uses-unpinned", W, 8), ("parse-incomplete", W, 9)}
    assert r.returncode == 2


def test_m10_version_comment_after_a_quoted_value(tmp_path):
    sha = "0123456789abcdef0123456789abcdef01234567"
    root = _write_workflows(tmp_path, {"w.yml": (
        "on: push\npermissions: {}\njobs:\n  a:\n    runs-on: x\n    timeout-minutes: 1\n    steps:\n"
        f"      - uses: \"actions/checkout@{sha}\" # v4\n"
        f"      - uses: 'actions/setup-node@{sha}'\n")})
    r, doc = _json(root, _empty_bindir(tmp_path))
    assert _rows(doc) == {("uses-version-comment-missing", W, 9)}


def test_compact_indentless_steps_under_pull_request_target(tmp_path):
    r, doc = _hostile("compact", tmp_path)
    assert _rows(doc) == {("uses-unpinned", W, 9), ("pull-request-target-checkout", W, 11), ("expression-injection", W, 12)}
    assert r.returncode == 2


def test_no_hostile_fixture_leaks_the_canary(tmp_path):
    bindir = _empty_bindir(tmp_path)
    for d in sorted(p for p in HOSTILE.iterdir() if p.is_dir()):
        for extra in ((), ("--json",)):
            r = run(d, bindir, "--org", "konyklabs", *extra)
            assert r.returncode in (0, 2), d.name
            assert CANARY not in r.stdout + r.stderr, d.name


# --------------------------------------------------------------------------
# Second batch of review findings (A1-A6)
# --------------------------------------------------------------------------


def test_a1_checkout_of_the_pr_head_repository_is_flagged(tmp_path):
    r, doc = _hostile("prt-merge-and-repository", tmp_path)
    # 11 and 17: refs/pull/N/merge (bare and quoted); 14: repository is the fork
    assert _rows(doc) == {("pull-request-target-checkout", W, n) for n in (11, 14, 17)}
    assert r.returncode == 2


def test_a1_repository_with_a_ref_is_flagged_on_both_lines(tmp_path):
    sha = "0123456789abcdef0123456789abcdef01234567"
    root = _write_workflows(tmp_path, {"w.yml": (
        "on: pull_request_target\npermissions: {}\njobs:\n  a:\n    runs-on: x\n    timeout-minutes: 1\n    steps:\n"
        f"      - uses: actions/checkout@{sha} # v4\n        with:\n"
        "          repository: ${{ github.event.pull_request.head.repo.full_name }}\n"
        "          ref: ${{ github.event.pull_request.head.sha }}\n"
        f"      - uses: actions/checkout@{sha} # v4\n        with:\n"
        "          repository: ${{ github.repository }}\n")})
    r, doc = _json(root, _empty_bindir(tmp_path))
    assert _rows(doc) == {("pull-request-target-checkout", W, 10), ("pull-request-target-checkout", W, 11)}


def test_a2_brackets_and_whole_untrusted_objects_are_flagged(tmp_path):
    r, doc = _hostile("flow-steps", tmp_path)
    # 8 and 9: unpinned (9 is a `uses:` whose value sits on the next line, A4);
    # 11 `||`, 12 format(), 13 bracket access, 14 toJSON(github.event.issue),
    # 16 a `>-` folded block
    assert _rows(doc) == {("uses-unpinned", W, 8), ("uses-unpinned", W, 9)} | {
        ("expression-injection", W, n) for n in (11, 12, 13, 14, 16)}


def test_a2_whole_object_list(tmp_path):
    flagged = ["toJSON(github.event.issue)", "github.event.pull_request", "toJSON(github.event.comment)",
               "github.event.review", "github.event.discussion", "toJSON(github.event.head_commit)",
               "join(github.event.commits.*.message, ' ')", "toJSON(github.event)"]
    clean = ["toJSON(github.event.repository)", "github.event.pull_request.head.sha", "toJSON(matrix)"]
    steps = "".join("      - run: echo \"${{ %s }}\"\n" % e for e in flagged + clean)
    root = _write_workflows(tmp_path, {"w.yml": (
        "on: push\npermissions: {}\njobs:\n  a:\n    runs-on: x\n    timeout-minutes: 1\n    steps:\n" + steps)})
    r, doc = _json(root, _empty_bindir(tmp_path))
    assert _rows(doc) == {("expression-injection", W, 8 + i) for i in range(len(flagged))}


def test_a3_many_unclosed_expression_openers_scan_in_linear_time(tmp_path):
    import time
    line = "      - run: echo " + "${{" * 20000 + "\n"
    root = _write_workflows(tmp_path, {"w.yml": (
        "on: push\npermissions: {}\njobs:\n  a:\n    runs-on: x\n    timeout-minutes: 1\n    steps:\n" + line
        + "      - run: echo \"${{ github.event.issue.title }}\"\n")})
    bindir = _empty_bindir(tmp_path)
    t0 = time.monotonic()
    r, doc = _json(root, bindir)
    elapsed = time.monotonic() - t0
    assert _rows(doc) == {("expression-injection", W, 9)}
    # The quadratic regex took about 16 s on this shape; linear is well
    # under one second including interpreter start-up.
    assert elapsed < 1.0, elapsed


def test_a4_uses_value_on_the_next_line_is_checked(tmp_path):
    root = _write_workflows(tmp_path, {"w.yml": (
        "on: push\npermissions: {}\njobs:\n  a:\n    runs-on: x\n    timeout-minutes: 1\n    steps:\n"
        "      - uses:\n          actions/setup-node@v4\n        with:\n          node-version: 22\n")})
    r, doc = _json(root, _empty_bindir(tmp_path))
    assert _rows(doc) == {("uses-unpinned", W, 8)}


def test_a5_quoted_pins_fixture_rows(tmp_path):
    r, doc = _hostile("quoted-pins", tmp_path)
    # 9 quoted SHA with comment and 10 quoted ./local: clean; 11 SHA with no
    # comment; 12 SHA with comment. SHA shape only (A8): 12 is not verified.
    assert _rows(doc) == {("uses-version-comment-missing", W, 11)}
    assert r.returncode == 0


def test_a6_canary_in_tool_messages_and_parse_failures_never_printed(tmp_path):
    root = _write_workflows(tmp_path, {
        "stray.yml": ("on: push\npermissions: {}\njobs:\n  a:\n    runs-on: x\n    timeout-minutes: 1\n"
                      f"      {CANARY}: {CANARY}\n    steps:\n      - run: echo hi\n"),
        "flow.yml": f"on: push\npermissions: {{contents: [{CANARY}: x]}}\njobs: {{}}\n",
        "deep.yml": "on: push\njobs:\n" + "".join("  " * (i + 1) + f"k{i}:\n" for i in range(100))
                    + "  " * 101 + f"z: {CANARY}\n",
    })
    bindir = _empty_bindir(tmp_path)
    al = json.dumps([{"message": CANARY, "filepath": ".github/workflows/stray.yml", "line": 7, "column": 1,
                      "kind": "syntax-check", "snippet": CANARY}])
    _stub(bindir, "actionlint", "cat $P; echo " + CANARY + " >&2; exit 1", P=al)
    zz = json.dumps([{"ident": "template-injection", "desc": CANARY, "url": CANARY,
                      "determinations": {"severity": "high", "confidence": "high"},
                      "locations": [{"symbolic": {"key": {"Local": {"given_path": ".github/workflows/stray.yml"}},
                                                  "annotation": CANARY},
                                     "concrete": {"location": {"start_point": {"row": 6}}, "feature": CANARY}}]}])
    _stub(bindir, "zizmor", "cat $P; echo " + CANARY + " >&2; exit 14", P=zz)
    for extra in ((), ("--json",)):
        r = run(root, bindir, "--org", "konyklabs", *extra)
        assert r.returncode == 2, r.stdout + r.stderr
        assert CANARY not in r.stdout, extra
        assert CANARY not in r.stderr, extra
        if extra:
            doc = json.loads(r.stdout)
            got = _rows(doc)
            assert ("parse-incomplete", ".github/workflows/stray.yml", 7) in got
            assert ("parse-incomplete", ".github/workflows/flow.yml", 2) in got
            assert ("parse-error", ".github/workflows/deep.yml", 1) in got
            rows = {row["check"]: row for row in doc["rows"]}
            assert rows["actionlint"]["count"] == 1 and rows["zizmor"]["count"] == 1
