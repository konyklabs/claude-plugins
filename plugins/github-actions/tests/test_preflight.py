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
        for f in row["findings"]:
            seen.add((f["path"], f["line"], f["rule"]))
    key = parse_answer_key()
    assert key, f"parsed 0 entries from {ANSWER_KEY}"
    missing = [(file, line, rule) for file, line, rule in key if (f".github/workflows/{file}", line, rule) not in seen]
    assert not missing, f"planted defects never reported: {missing}\nreported: {sorted(seen)}"


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
        "name: caller\n\njobs:\n  call:\n    permissions:\n      contents: read\n"
        "    timeout-minutes: 5\n"
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
