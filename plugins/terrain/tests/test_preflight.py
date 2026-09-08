import json
import os
import stat
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "preflighting-tofu" / "scripts" / "preflight.py"


def _stub(bindir: Path, name: str, body: str, **payloads: str):
    """Write a stub tool. `body` is sh; `cat $NAME` prints payload NAME."""
    for key, text in payloads.items():
        (bindir / f"{name}.{key}").write_text(text)
        body = body.replace(f"cat ${key}", f"cat '{bindir / f'{name}.{key}'}'")
    p = bindir / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def _module(tmp_path: Path, initialized=True):
    m = tmp_path / "mod"
    m.mkdir()
    (m / "main.tf").write_text('resource "aws_s3_bucket" "b" {}\n')
    if initialized:
        (m / ".terraform").mkdir()
    return m


def run(module: Path, bindir: Path, *args):
    env = {**os.environ, "PATH": f"{bindir}:/usr/bin:/bin"}  # stubs shadow real tools; sh builtins still resolve
    return subprocess.run([sys.executable, str(SCRIPT), str(module), *args], capture_output=True, text=True, env=env)


def test_nothing_on_path_is_all_skips_and_exit_zero(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir()
    r = run(_module(tmp_path), bindir, "--json")
    rows = json.loads(r.stdout)
    assert {row["status"] for row in rows} == {"skip"}
    assert r.returncode == 0
    assert all(row["reason"] for row in rows)


def test_not_a_module_exits_one(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir()
    assert run(tmp_path, bindir).returncode == 1


def test_validate_skips_when_not_initialized(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir()
    _stub(bindir, "tofu", 'case "$1" in fmt) exit 0;; validate) echo "{}"; exit 0;; esac')
    rows = json.loads(run(_module(tmp_path, initialized=False), bindir, "--json").stdout)
    v = next(r for r in rows if r["tool"] == "validate")
    assert v["status"] == "skip" and "init -backend=false" in v["reason"]


def test_fmt_and_validate_fail_rows(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir()
    validate = json.dumps({"valid": False, "error_count": 1, "diagnostics": [
        {"severity": "error", "summary": "Unsupported argument", "detail": "SECRET-DETAIL", "range": {"filename": "main.tf", "start": {"line": 7}}}]})
    _stub(bindir, "tofu", 'case "$1" in fmt) echo main.tf; exit 3;; validate) cat $V; exit 1;; esac', V=validate)
    r = run(_module(tmp_path), bindir)
    assert r.returncode == 2
    assert "fmt        fail   unformatted=1" in r.stdout
    assert "main.tf:7 Unsupported argument" in r.stdout
    assert "SECRET-DETAIL" not in r.stdout


def test_tflint_trivy_checkov_parsed_to_path_line_rule(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir()
    _stub(bindir, "tofu", 'exit 0')
    tfl = json.dumps({"issues": [{"rule": {"name": "terraform_typed_variables", "severity": "warning"}, "message": "MSG <b>", "range": {"filename": "variables.tf", "start": {"line": 1}}}], "errors": []})
    _stub(bindir, "tflint", 'cat $P; exit 2', P=tfl)
    trv = json.dumps({"Results": [{"Target": "network.tf", "Misconfigurations": [{"ID": "AWS-0107", "Severity": "HIGH", "Title": "MSG", "CauseMetadata": {"StartLine": 12}}]}]})
    _stub(bindir, "trivy", 'cat $P; exit 1', P=trv)
    ckv = json.dumps({"summary": {"failed": 1, "passed": 9}, "results": {"failed_checks": [{"check_id": "CKV_AWS_249", "resource": "aws_ecs_task_definition.app", "file_path": "/ecs.tf", "file_line_range": [5, 30]}]}})
    _stub(bindir, "checkov", 'cat $P; exit 1', P=ckv)
    r = run(_module(tmp_path), bindir)
    assert r.returncode == 2
    assert "variables.tf:1 terraform_typed_variables" in r.stdout
    assert "network.tf:12 AWS-0107 HIGH" in r.stdout
    assert "ecs.tf:5 CKV_AWS_249" in r.stdout
    assert "MSG" not in r.stdout and "<b>" not in r.stdout


def test_tool_garbage_output_is_skip_not_pass(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir()
    _stub(bindir, "tofu", 'exit 0')
    _stub(bindir, "tflint", 'echo "Failed to load plugins"; exit 1')
    _stub(bindir, "trivy", 'echo nope; exit 0')
    rows = json.loads(run(_module(tmp_path), bindir, "--json").stdout)
    by = {r["tool"]: r for r in rows}
    assert by["tflint"]["status"] == "skip" and "tflint --init" in by["tflint"]["reason"]
    assert by["trivy"]["status"] == "skip"


def test_findings_are_capped(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir()
    _stub(bindir, "tofu", 'exit 0')
    issues = [{"rule": {"name": "r", "severity": "warning"}, "range": {"filename": "a.tf", "start": {"line": i}}} for i in range(100)]
    _stub(bindir, "tflint", 'cat $P; exit 2', P=json.dumps({"issues": issues, "errors": []}))
    r = run(_module(tmp_path), bindir)
    assert "… 60 more" in r.stdout
