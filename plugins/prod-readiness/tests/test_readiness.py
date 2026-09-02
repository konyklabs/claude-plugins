"""Tests for the readiness-review scanner against synthetic app trees."""
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "readiness-review" / "scripts" / "readiness.py"
spec = importlib.util.spec_from_file_location("readiness", SCRIPT)
readiness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(readiness)

GIT = shutil.which("git")


def mkctx(is_git=False, archive=None, history=0):
    return {"archive": archive, "history": history, "is_git": is_git}


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def run_cli(args, cwd, env=None):
    return subprocess.run([sys.executable, str(SCRIPT)] + args, cwd=str(cwd), capture_output=True, text=True, env=env, check=False)


def init_git(root: Path):
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(root), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(root), check=True)


# --------------------------------------------------------------------------- CLI basics


def test_help_works():
    r = run_cli(["--help"], cwd=Path.cwd())
    assert r.returncode == 0
    assert "readiness.py" in r.stdout


def test_usage_error_exit_code(tmp_path):
    r = run_cli([str(tmp_path / "does-not-exist")], cwd=tmp_path)
    assert r.returncode == 2


def test_tier_precommit_runs_only_four_checks(tmp_path):
    r = run_cli([".", "--tier", "precommit", "--json"], cwd=tmp_path)
    data = json.loads(r.stdout)
    ids = sorted(c["id"] for c in data["checks"])
    assert ids == ["archive-hygiene", "credential-patterns", "html-sinks", "identifier-shapes"]


def test_default_tier_runs_all_nineteen_checks(tmp_path):
    r = run_cli([".", "--json"], cwd=tmp_path)
    data = json.loads(r.stdout)
    assert len(data["checks"]) == 19
    assert data["tier"] == "release"


def test_only_overrides_tier(tmp_path):
    r = run_cli([".", "--tier", "precommit", "--only", "dos-surface,tools", "--json"], cwd=tmp_path)
    data = json.loads(r.stdout)
    assert sorted(c["id"] for c in data["checks"]) == ["dos-surface", "tools"]


def test_json_writes_nothing_to_disk(tmp_path):
    run_cli([".", "--json"], cwd=tmp_path)
    assert not (tmp_path / ".readiness").exists()


def test_markdown_default_writes_report_json(tmp_path):
    # precommit tier on an empty tree: every check skips or passes, so this
    # also exercises the "no findings" exit-0 path without coupling to the
    # release tier's dos-surface verdict on a tree with no app code at all.
    r = run_cli([".", "--tier", "precommit"], cwd=tmp_path)
    assert r.returncode == 0
    report = tmp_path / ".readiness" / "report.json"
    assert report.exists()
    data = json.loads(report.read_text())
    assert data["tier"] == "precommit"
    assert "# Production readiness" in r.stdout


def test_out_flag_redirects_report_location(tmp_path):
    out = tmp_path / "custom-out"
    run_cli([".", "--out", str(out)], cwd=tmp_path)
    assert (out / "report.json").exists()
    assert not (tmp_path / ".readiness").exists()


def test_exit_code_reflects_fail_status(tmp_path):
    write(tmp_path / ".github" / "workflows" / "ci.yml", "jobs:\n  x:\n    steps:\n      - uses: some-org/some-action@v1\n")
    r = run_cli([".", "--only", "action-pinning", "--json"], cwd=tmp_path)
    data = json.loads(r.stdout)
    assert data["checks"][0]["status"] == "fail"
    assert r.returncode == 1


def test_exit_code_zero_when_nothing_fails(tmp_path):
    r = run_cli([".", "--only", "html-sinks", "--json"], cwd=tmp_path)
    data = json.loads(r.stdout)
    assert data["checks"][0]["status"] == "pass"
    assert r.returncode == 0


def test_determinism_two_runs_identical_json(tmp_path):
    write(tmp_path / "static" / "app.js", "el.innerHTML = data;\n")
    write(tmp_path / "app.py", "@app.get('/api/config')\ndef cfg():\n    return {}\n")
    a = run_cli([".", "--json"], cwd=tmp_path).stdout
    shutil.rmtree(tmp_path / ".readiness", ignore_errors=True)
    b = run_cli([".", "--json"], cwd=tmp_path).stdout
    assert a == b
    assert '"timestamp"' not in a


def test_markdown_never_contains_matched_secret_text(tmp_path):
    write(tmp_path / "leaky.py", 'api_key = "sk_live_abcdefghijklmnop1234"\n')
    r = run_cli([".", "--only", "credential-patterns"], cwd=tmp_path)
    # credential-patterns is unconfigured here (skip); use archive-hygiene against a built tar instead
    tar_path = tmp_path / "a.tar"
    with tarfile.open(tar_path, "w") as tf:
        p = tmp_path / "leaky.py"
        tf.add(p, arcname="leaky.py")
    r = run_cli([".", "--only", "archive-hygiene", "--archive", str(tar_path)], cwd=tmp_path)
    assert "sk_live_abcdefghijklmnop1234" not in r.stdout
    report = json.loads((tmp_path / ".readiness" / "report.json").read_text())
    assert "sk_live_abcdefghijklmnop1234" not in json.dumps(report)
    finding_notes = [f["note"] for c in report["checks"] for f in c["findings"]]
    assert any("stripe-live-key" in n for n in finding_notes)


# --------------------------------------------------------------------------- archive-hygiene


def test_archive_hygiene_skips_without_git_or_archive(tmp_path):
    result = readiness.check_archive_hygiene(tmp_path, {}, mkctx(is_git=False))
    assert result["status"] == "skip"
    assert result["reason"] == "not a git repository and no --archive"


def test_archive_hygiene_flags_denied_names_and_secrets(tmp_path):
    tar_path = tmp_path / "bad.tar"
    with tarfile.open(tar_path, "w") as tf:
        env_file = write(tmp_path / "src" / ".env", "X=1\n")
        tf.add(env_file, arcname=".env")
        key_file = write(tmp_path / "src" / "id_rsa", "fake\n")
        tf.add(key_file, arcname="id_rsa")
        secret_file = write(tmp_path / "src" / "conf.py", 'password = "abcdefghijklmnop1234"\n')
        tf.add(secret_file, arcname="conf.py")
        clean_file = write(tmp_path / "src" / "clean.py", "x = 1\n")
        tf.add(clean_file, arcname="clean.py")
    result = readiness.check_archive_hygiene(tmp_path, {}, mkctx(archive=str(tar_path)))
    assert result["status"] == "fail"
    notes = {f["note"] for f in result["findings"]}
    assert "dotenv file" in notes
    assert "ssh private key" in notes
    assert "generic-secret-assignment" in notes
    for f in result["findings"]:
        assert "abcdefghijklmnop1234" not in f["note"]


def test_archive_hygiene_passes_on_clean_archive(tmp_path):
    tar_path = tmp_path / "clean.tar"
    with tarfile.open(tar_path, "w") as tf:
        clean_file = write(tmp_path / "src" / "app.py", "x = 1\n")
        tf.add(clean_file, arcname="app.py")
    result = readiness.check_archive_hygiene(tmp_path, {}, mkctx(archive=str(tar_path)))
    assert result["status"] == "pass"
    assert result["findings"] == []


def test_archive_hygiene_ignore_globs_exclude_fixtures(tmp_path):
    tar_path = tmp_path / "fx.tar"
    with tarfile.open(tar_path, "w") as tf:
        fx = write(tmp_path / "fixtures" / "sample.py", 'token = "abcdefghijklmnop1234"\n')
        tf.add(fx, arcname="fixtures/sample.py")
    config = {"archive_ignore_globs": ["fixtures/*"]}
    result = readiness.check_archive_hygiene(tmp_path, config, mkctx(archive=str(tar_path)))
    assert result["status"] == "pass"


@pytest.mark.skipif(GIT is None, reason="git not on PATH")
def test_archive_hygiene_against_real_git_archive(tmp_path):
    write(tmp_path / "app.py", "x = 1\n")
    write(tmp_path / ".env", "SECRET=1\n")
    write(tmp_path / ".gitignore", ".env\n")
    init_git(tmp_path)
    result = readiness.check_archive_hygiene(tmp_path, {}, mkctx(is_git=True))
    # .env is gitignored so it never enters the archive; the archive itself is clean.
    assert result["status"] == "pass"


@pytest.mark.skipif(GIT is None, reason="git not on PATH")
def test_archive_hygiene_reports_ignored_worktree_files_without_failing(tmp_path):
    write(tmp_path / "app.py", "x = 1\n")
    write(tmp_path / ".gitignore", "secrets.local\n")
    init_git(tmp_path)
    write(tmp_path / "secrets.local", "SECRET=1\n")
    result = readiness.check_archive_hygiene(tmp_path, {}, mkctx(is_git=True))
    assert result["status"] == "pass"
    notes = [f["note"] for f in result["findings"]]
    assert any("ignored files present in the working tree" in n for n in notes)


def test_archive_hygiene_archive_not_found_skips(tmp_path):
    result = readiness.check_archive_hygiene(tmp_path, {}, mkctx(archive=str(tmp_path / "nope.tar")))
    assert result["status"] == "skip"
    assert "not found" in result["reason"]


# --------------------------------------------------------------------------- history-secrets (external tool)


FAKE_GITLEAKS_HIT = """#!/bin/sh
for a in "$@"; do
  case "$a" in
    --report-path) shift; echo '[{"RuleID":"aws-access-key","File":"app.py","StartLine":3,"Secret":"AKIASECRETVALUE","Match":"AKIASECRETVALUE"}]' > "$1" ;;
  esac
  shift
done
exit 1
"""


def make_fake_tool(bin_dir: Path, name: str, script: str) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    p = bin_dir / name
    p.write_text(script)
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def test_history_secrets_skips_when_gitleaks_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    (tmp_path / "empty-bin").mkdir()
    result = readiness.check_history_secrets(tmp_path, {}, mkctx(is_git=False))
    assert result["status"] == "skip"
    assert result["reason"] == "gitleaks not installed"
    assert "gitleaks.com" not in result["command"]
    assert "github.com/gitleaks/gitleaks/releases" in result["command"]


def test_history_secrets_parses_fake_gitleaks_report_without_secret_text(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    make_fake_tool(bin_dir, "gitleaks", FAKE_GITLEAKS_HIT)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    result = readiness.check_history_secrets(tmp_path, {}, mkctx(is_git=False))
    assert result["status"] == "fail"
    assert result["findings"] == [{"path": "app.py", "line": 3, "note": "aws-access-key"}]
    assert result["counts"] == {"aws-access-key": 1}
    dumped = json.dumps(result)
    assert "AKIASECRETVALUE" not in dumped


def test_history_secrets_flags_tip_only_checkout_independent_of_gitleaks(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    (tmp_path / "empty-bin").mkdir()
    write(tmp_path / ".github" / "workflows" / "secrets-scan.yml",
          "jobs:\n  scan:\n    steps:\n      - uses: actions/checkout@v6\n      - run: gitleaks detect\n")
    result = readiness.check_history_secrets(tmp_path, {}, mkctx(is_git=False))
    assert result["status"] == "fail"
    assert any("tip-only checkout" in f["note"] for f in result["findings"])


def test_history_secrets_fetch_depth_zero_avoids_finding(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    (tmp_path / "empty-bin").mkdir()
    write(tmp_path / ".github" / "workflows" / "secrets-scan.yml",
          "jobs:\n  scan:\n    steps:\n      - uses: actions/checkout@v6\n"
          "        with:\n          fetch-depth: 0\n      - run: gitleaks detect\n")
    result = readiness.check_history_secrets(tmp_path, {}, mkctx(is_git=False))
    assert result["status"] == "skip"  # gitleaks itself still not installed
    assert not any("tip-only checkout" in f["note"] for f in result["findings"])


# --------------------------------------------------------------------------- credential-patterns


def test_credential_patterns_skips_when_unconfigured(tmp_path):
    result = readiness.check_credential_patterns(tmp_path, {}, mkctx())
    assert result["status"] == "skip"


def test_credential_patterns_fails_on_a_hit(tmp_path):
    write(tmp_path / "app.py", 'key = "ACME-123456"\n')
    config = {"credential_patterns": [{"name": "acme-key", "regex": r"ACME-[0-9]{6}"}]}
    result = readiness.check_credential_patterns(tmp_path, config, mkctx())
    assert result["status"] == "fail"
    assert result["findings"] == [{"path": "app.py", "line": 1, "note": "acme-key"}]


def test_credential_patterns_passes_without_a_hit(tmp_path):
    write(tmp_path / "app.py", "x = 1\n")
    config = {"credential_patterns": [{"name": "acme-key", "regex": r"ACME-[0-9]{6}"}]}
    result = readiness.check_credential_patterns(tmp_path, config, mkctx())
    assert result["status"] == "pass"


@pytest.mark.skipif(GIT is None, reason="git not on PATH")
def test_credential_patterns_history_scan_finds_removed_secret(tmp_path):
    write(tmp_path / "app.py", 'key = "ACME-999999"\n')
    init_git(tmp_path)
    write(tmp_path / "app.py", "x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "remove secret"], cwd=str(tmp_path), check=True)
    config = {"credential_patterns": [{"name": "acme-key", "regex": r"ACME-[0-9]{6}"}]}
    result = readiness.check_credential_patterns(tmp_path, config, mkctx(is_git=True, history=5))
    assert result["status"] == "fail"
    assert any("acme-key @" in f["note"] for f in result["findings"])


# --------------------------------------------------------------------------- identifier-shapes


def test_identifier_shapes_skips_when_unconfigured(tmp_path):
    result = readiness.check_identifier_shapes(tmp_path, {}, mkctx())
    assert result["status"] == "skip"


def test_identifier_shapes_only_scans_allowed_dirs(tmp_path):
    config = {"identifier_patterns": [{"name": "order-id", "regex": r"ORD-\d{6}"}]}
    write(tmp_path / "src" / "app.py", "x = 'ORD-123456'\n")  # not in an allowed dir: ignored
    result = readiness.check_identifier_shapes(tmp_path, config, mkctx())
    assert result["status"] == "pass"
    assert result["findings"] == []


def test_identifier_shapes_flags_hit_under_tests_dir(tmp_path):
    config = {"identifier_patterns": [{"name": "order-id", "regex": r"ORD-\d{6}"}]}
    write(tmp_path / "tests" / "fixtures.py", "x = 'ORD-123456'\n")
    result = readiness.check_identifier_shapes(tmp_path, config, mkctx())
    assert result["status"] == "fail"
    assert result["findings"] == [{"path": "tests/fixtures.py", "line": 1, "note": "order-id"}]


# --------------------------------------------------------------------------- config-endpoint-secrets


def test_config_endpoint_secrets_pass_when_no_matching_routes(tmp_path):
    write(tmp_path / "app.py", "@app.get('/api/orders')\ndef orders():\n    return {}\n")
    result = readiness.check_config_endpoint_secrets(tmp_path, {}, mkctx())
    assert result["status"] == "pass"
    assert result["findings"] == []


def test_config_endpoint_secrets_review_without_absence_test(tmp_path):
    write(tmp_path / "app.py", "@app.get('/api/config')\ndef cfg():\n    return {'debug': True}\n")
    result = readiness.check_config_endpoint_secrets(tmp_path, {}, mkctx())
    assert result["status"] == "review"
    assert "absence test found" not in result["findings"][0]["note"]


def test_config_endpoint_secrets_pass_with_absence_test(tmp_path):
    write(tmp_path / "app.py", "@app.get('/api/config')\ndef cfg():\n    return {}\n")
    write(tmp_path / "tests" / "test_config.py",
          "def test_no_secret():\n    assert \"'secret' not in body\" and \"/api/config\" not in []\n")
    result = readiness.check_config_endpoint_secrets(tmp_path, {}, mkctx())
    assert result["status"] == "pass"
    assert "absence test found" in result["findings"][0]["note"]


# --------------------------------------------------------------------------- redaction-at-publish


def test_redaction_pass_when_no_stream_markers(tmp_path):
    write(tmp_path / "app.py", "x = 1\n")
    result = readiness.check_redaction_at_publish(tmp_path, {}, mkctx())
    assert result["status"] == "pass"
    assert result["counts"] == {"streams": 0}


def test_redaction_review_flags_missing_redaction_and_buffer_and_lists(tmp_path):
    write(tmp_path / "stream.py",
          "from sse_starlette import EventSourceResponse\n"
          "history = deque()\n"
          "ALLOWED = {'x'}\n")
    result = readiness.check_redaction_at_publish(tmp_path, {}, mkctx())
    assert result["status"] == "review"
    notes = [f["note"] for f in result["findings"]]
    assert "no redaction word in publisher" in notes
    assert "replay buffer present: redact before buffering" in notes
    assert any("allowlist word found" in n for n in notes)


def test_redaction_notes_when_redaction_word_present(tmp_path):
    write(tmp_path / "stream.py", "from sse_starlette import EventSourceResponse\n# redact before send\n")
    result = readiness.check_redaction_at_publish(tmp_path, {}, mkctx())
    assert "redaction in publisher" in [f["note"] for f in result["findings"]]


# --------------------------------------------------------------------------- debug-endpoint-exposure


def test_debug_endpoint_pass_when_no_debug_routes(tmp_path):
    write(tmp_path / "app.py", "@app.get('/api/orders')\ndef orders():\n    return {}\n")
    result = readiness.check_debug_endpoint_exposure(tmp_path, {}, mkctx())
    assert result["status"] == "pass"


def test_debug_endpoint_fails_without_loopback_check(tmp_path):
    write(tmp_path / "app.py", "@app.get('/debug/status')\ndef status():\n    return {}\n")
    result = readiness.check_debug_endpoint_exposure(tmp_path, {}, mkctx())
    assert result["status"] == "fail"
    assert result["findings"][0]["note"] == "no per-request loopback check"


def test_debug_endpoint_passes_with_loopback_check_and_flags_token_tripwire(tmp_path):
    write(tmp_path / "app.py",
          "@app.get('/debug/status')\ndef status(request):\n"
          "    if request.client.host not in ('127.0.0.1', '::1'):\n        raise Exception()\n"
          "    if token != 'x':\n        raise Exception()\n    return {}\n")
    result = readiness.check_debug_endpoint_exposure(tmp_path, {}, mkctx())
    assert result["status"] == "pass"
    notes = [f["note"] for f in result["findings"]]
    assert "per-request loopback check present" in notes
    assert "token is a tripwire, not auth" in notes


# --------------------------------------------------------------------------- html-sinks


def test_html_sinks_fail_on_innerhtml_assignment(tmp_path):
    write(tmp_path / "static" / "app.js", "el.innerHTML = userInput;\n")
    result = readiness.check_html_sinks(tmp_path, {}, mkctx())
    assert result["status"] == "fail"
    assert result["findings"] == [{"path": "static/app.js", "line": 1, "note": ".innerHTML ="}]
    assert result["counts"] == {"sinks": 1}


def test_html_sinks_pass_without_sinks(tmp_path):
    write(tmp_path / "static" / "app.js", "el.textContent = userInput;\n")
    result = readiness.check_html_sinks(tmp_path, {}, mkctx())
    assert result["status"] == "pass"
    assert result["counts"] == {"sinks": 0}


def test_html_sinks_ignores_minified_files(tmp_path):
    write(tmp_path / "static" / "app.min.js", "el.innerHTML = x;\n")
    result = readiness.check_html_sinks(tmp_path, {}, mkctx())
    assert result["status"] == "pass"


# --------------------------------------------------------------------------- async-terminal-states


def test_async_terminal_states_pass_without_candidates(tmp_path):
    write(tmp_path / "app.py", "def f():\n    return 1\n")
    result = readiness.check_async_terminal_states(tmp_path, {}, mkctx())
    assert result["status"] == "pass"


def test_async_terminal_states_review_notes_test_coverage(tmp_path):
    write(tmp_path / "app.py", "@app.post('/jobs')\ndef create(response_model=None):\n    return {'status': 'pending'}, 202\n")
    write(tmp_path / "tests" / "test_jobs.py", "def test_job_completed():\n    assert status == 'completed'\n")
    result = readiness.check_async_terminal_states(tmp_path, {}, mkctx())
    assert result["status"] == "review"
    assert result["counts"]["test_files_with_terminal_states"] == 1
    assert any("1 test files assert terminal states" in f["note"] for f in result["findings"])


def test_async_terminal_states_notes_absence_of_terminal_test(tmp_path):
    write(tmp_path / "app.py", "def poll():\n    return {'status': 'processing'}\n")
    result = readiness.check_async_terminal_states(tmp_path, {}, mkctx())
    assert result["status"] == "review"
    assert any("no test mentions a terminal state" in f["note"] for f in result["findings"])


# --------------------------------------------------------------------------- vendor-mode-probes


def test_vendor_mode_probes_pass_with_only_one_mode(tmp_path):
    write(tmp_path / "app.py", "key = 'sk_test_abc'\n")
    result = readiness.check_vendor_mode_probes(tmp_path, {}, mkctx())
    assert result["status"] == "pass"


def test_vendor_mode_probes_review_when_both_present(tmp_path):
    write(tmp_path / "app.py", "test_key = 'sk_test_abc'\nlive_key = 'sk_live_xyz'\n")
    result = readiness.check_vendor_mode_probes(tmp_path, {}, mkctx())
    assert result["status"] == "review"
    assert "probe capabilities, never infer them from a key" in result["findings"][0]["note"]


# --------------------------------------------------------------------------- idempotency-keys


def test_idempotency_keys_review_with_generation_nearby(tmp_path):
    write(tmp_path / "app.py", "key = str(uuid.uuid4())\nheaders['Idempotency-Key'] = key\n")
    result = readiness.check_idempotency_keys(tmp_path, {}, mkctx())
    assert result["status"] == "review"
    assert result["findings"][0]["note"] == "key generated nearby"


def test_idempotency_keys_review_without_generation_nearby(tmp_path):
    write(tmp_path / "app.py", "headers['Idempotency-Key'] = 'static'\n")
    result = readiness.check_idempotency_keys(tmp_path, {}, mkctx())
    assert result["status"] == "review"
    assert result["findings"][0]["note"] == "no key generation within 30 lines"


def test_idempotency_keys_fail_on_money_post_without_header(tmp_path):
    write(tmp_path / "app.py", "@app.post('/api/payments')\ndef pay():\n    return {}\n")
    result = readiness.check_idempotency_keys(tmp_path, {}, mkctx())
    assert result["status"] == "fail"
    assert "money-moving POST without an idempotency key" in result["findings"][0]["note"]


def test_idempotency_keys_pass_without_money_routes(tmp_path):
    write(tmp_path / "app.py", "@app.get('/api/orders')\ndef orders():\n    return {}\n")
    result = readiness.check_idempotency_keys(tmp_path, {}, mkctx())
    assert result["status"] == "pass"


# --------------------------------------------------------------------------- client-supplied-money


def test_client_supplied_money_review_without_recompute(tmp_path):
    write(tmp_path / "app.py",
          "@app.post('/api/orders')\n"
          "def order(body):\n"
          "    amount = body['amount']\n"
          "    httpx.post('https://pay.example/charge', json={'amount': amount})\n")
    result = readiness.check_client_supplied_money(tmp_path, {}, mkctx())
    assert result["status"] == "review"
    assert "client total forwarded without server recomputation" in result["findings"][0]["note"]


def test_client_supplied_money_pass_with_recompute(tmp_path):
    write(tmp_path / "app.py",
          "@app.post('/api/orders')\n"
          "def order(body):\n"
          "    amount = body['amount']\n"
          "    verified = calculate_total(body)\n"
          "    httpx.post('https://pay.example/charge', json={'amount': verified})\n")
    result = readiness.check_client_supplied_money(tmp_path, {}, mkctx())
    assert result["status"] == "pass"


# --------------------------------------------------------------------------- skipped-credentialed-tiers


def test_skipped_credentialed_tiers_pass_without_skips(tmp_path):
    write(tmp_path / "tests" / "test_a.py", "def test_a():\n    assert True\n")
    result = readiness.check_skipped_credentialed_tiers(tmp_path, {}, mkctx())
    assert result["status"] == "pass"


def test_skipped_credentialed_tiers_fail_without_workflow_secrets(tmp_path):
    write(tmp_path / "tests" / "test_live.py",
          "@pytest.mark.skipif(not os.getenv('API_TOKEN'), reason='no token')\ndef test_live():\n    pass\n")
    result = readiness.check_skipped_credentialed_tiers(tmp_path, {}, mkctx())
    assert result["status"] == "fail"
    assert any("credentialed tier can skip silently" in f["note"] for f in result["findings"])


def test_skipped_credentialed_tiers_review_with_workflow_secrets(tmp_path):
    write(tmp_path / "tests" / "test_live.py",
          "@pytest.mark.skipif(not os.getenv('API_TOKEN'), reason='no token')\ndef test_live():\n    pass\n")
    write(tmp_path / ".github" / "workflows" / "ci.yml", "env:\n  API_TOKEN: ${{ secrets.API_TOKEN }}\n")
    result = readiness.check_skipped_credentialed_tiers(tmp_path, {}, mkctx())
    assert result["status"] == "review"
    assert any("fork-PR-only" in f["note"] for f in result["findings"])


# --------------------------------------------------------------------------- contract-artifact-drift


def test_contract_artifact_drift_pass_without_artifacts(tmp_path):
    result = readiness.check_contract_artifact_drift(tmp_path, {}, mkctx())
    assert result["status"] == "pass"
    assert result["counts"] == {"artifacts": 0}


def test_contract_artifact_drift_fail_without_diff_in_ci(tmp_path):
    write(tmp_path / "pacts" / "consumer-provider.json", "{}\n")
    result = readiness.check_contract_artifact_drift(tmp_path, {}, mkctx())
    assert result["status"] == "fail"


def test_contract_artifact_drift_pass_with_diff_in_ci(tmp_path):
    write(tmp_path / "pacts" / "consumer-provider.json", "{}\n")
    write(tmp_path / ".github" / "workflows" / "ci.yml", "run: git diff --exit-code pacts\n")
    result = readiness.check_contract_artifact_drift(tmp_path, {}, mkctx())
    assert result["status"] == "pass"


# --------------------------------------------------------------------------- action-pinning


def test_action_pinning_fail_on_third_party_tag(tmp_path):
    write(tmp_path / ".github" / "workflows" / "ci.yml",
          "jobs:\n  x:\n    steps:\n      - uses: some-org/some-action@v1\n")
    result = readiness.check_action_pinning(tmp_path, {}, mkctx())
    assert result["status"] == "fail"
    assert "third-party" in result["findings"][0]["note"]


def test_action_pinning_review_on_first_party_tag(tmp_path):
    write(tmp_path / ".github" / "workflows" / "ci.yml",
          "jobs:\n  x:\n    steps:\n      - uses: actions/checkout@v4\n")
    result = readiness.check_action_pinning(tmp_path, {}, mkctx())
    assert result["status"] == "review"
    assert "tag-pinned first-party" in result["findings"][0]["note"]


def test_action_pinning_pass_on_sha_pinned_third_party(tmp_path):
    sha = "a" * 40
    write(tmp_path / ".github" / "workflows" / "ci.yml",
          f"jobs:\n  x:\n    steps:\n      - uses: some-org/some-action@{sha}\n")
    result = readiness.check_action_pinning(tmp_path, {}, mkctx())
    assert result["status"] == "pass"
    assert result["findings"] == []


def test_action_pinning_pass_without_workflows(tmp_path):
    result = readiness.check_action_pinning(tmp_path, {}, mkctx())
    assert result["status"] == "pass"


# --------------------------------------------------------------------------- runtime-version-drift


def test_runtime_version_drift_pass_with_one_source(tmp_path):
    write(tmp_path / "pyproject.toml", 'requires-python = ">=3.9"\n')
    result = readiness.check_runtime_version_drift(tmp_path, {}, mkctx())
    assert result["status"] == "pass"


def test_runtime_version_drift_fail_below_floor(tmp_path):
    write(tmp_path / "pyproject.toml", 'requires-python = ">=3.9"\n')
    write(tmp_path / "Dockerfile", "FROM python:3.8-slim\n")
    result = readiness.check_runtime_version_drift(tmp_path, {}, mkctx())
    assert result["status"] == "fail"


def test_runtime_version_drift_fail_on_disagreement(tmp_path):
    write(tmp_path / ".github" / "workflows" / "ci.yml",
          "jobs:\n  x:\n    strategy:\n      matrix:\n        python-version: [\"3.9\", \"3.13\"]\n")
    write(tmp_path / "Dockerfile", "FROM python:3.11-slim\n")
    result = readiness.check_runtime_version_drift(tmp_path, {}, mkctx())
    assert result["status"] == "fail"


# --------------------------------------------------------------------------- docs-endpoint-drift


def test_docs_endpoint_drift_skip_without_spec(tmp_path):
    result = readiness.check_docs_endpoint_drift(tmp_path, {}, mkctx())
    assert result["status"] == "skip"
    assert result["reason"] == "no OpenAPI document found"


def test_docs_endpoint_drift_review_flags_undocumented_and_underspecified(tmp_path):
    write(tmp_path / "openapi.json", json.dumps({"paths": {"/api/orders": {}}}))
    write(tmp_path / "docs" / "guide.md", "Call `/api/orders/{id}/cancel` to cancel an order.\n")
    result = readiness.check_docs_endpoint_drift(tmp_path, {}, mkctx())
    assert result["status"] == "review"
    assert result["counts"]["in_spec_not_documented"] == 1
    assert any("documented, not in spec" in f["note"] for f in result["findings"])


# --------------------------------------------------------------------------- dos-surface


def _dos_vulnerable_app(tmp_path):
    write(tmp_path / "app.py",
          "app = FastAPI(debug=True)\n"
          "app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True)\n"
          "@app.get('/api/items')\n"
          "def items(limit: int = 10):\n"
          "    return httpx.get('https://upstream.example/items')\n")
    write(tmp_path / "Dockerfile", "FROM python:3.12-slim\nCMD [\"python\", \"app.py\"]\n")


def _dos_hardened_app(tmp_path):
    write(tmp_path / "app.py",
          "MAX_CONTENT_LENGTH = 1_000_000\n"
          "limiter = Limiter()\n"
          "app = FastAPI()\n"
          "app.add_middleware(CORSMiddleware, allow_origins=['https://example.com'])\n"
          "@app.get('/api/items')\n"
          "def items(limit: int = Query(10, le=100)):\n"
          "    return httpx.get('https://upstream.example/items', timeout=5)\n"
          "@app.on_event('shutdown')\n"
          "def shutdown():\n    pass\n")
    write(tmp_path / "Dockerfile",
          "FROM python:3.12-slim@sha256:" + "b" * 64 + "\n"
          "HEALTHCHECK CMD curl -f http://localhost/ || exit 1\n"
          "USER appuser\n"
          "CMD [\"python\", \"app.py\", \"--limit-concurrency\", \"50\"]\n")


def test_dos_surface_fails_on_vulnerable_app(tmp_path):
    _dos_vulnerable_app(tmp_path)
    result = readiness.check_dos_surface(tmp_path, {}, mkctx())
    assert result["status"] == "fail"
    notes = [f["note"] for f in result["findings"]]
    assert "CORS wildcard origin with credentials allowed" in notes
    assert "debug flag enabled in non-test code" in notes
    assert any(n == "outbound call without timeout" for n in notes)


def test_dos_surface_passes_on_hardened_app(tmp_path):
    _dos_hardened_app(tmp_path)
    result = readiness.check_dos_surface(tmp_path, {}, mkctx())
    assert result["status"] in ("pass", "review")  # graceful shutdown / regex-dos absence keeps it out of fail
    notes = [f["note"] for f in result["findings"]]
    assert "CORS wildcard origin with credentials allowed" not in notes
    assert "debug flag enabled in non-test code" not in notes


def test_dos_surface_flags_regex_dos_candidate(tmp_path):
    write(tmp_path / "app.py", 're.compile(r"(a+)+")\n')
    result = readiness.check_dos_surface(tmp_path, {}, mkctx())
    notes = [f["note"] for f in result["findings"]]
    assert "regex DoS candidate" in notes


def test_dos_surface_pagination_cap_absent_is_flagged(tmp_path):
    write(tmp_path / "app.py", "@app.get('/api/items')\ndef items(limit: int = 1000):\n    return []\n")
    result = readiness.check_dos_surface(tmp_path, {}, mkctx())
    notes = [f["note"] for f in result["findings"]]
    assert "pagination cap absent" in notes


def test_dos_surface_graceful_shutdown_absent_is_review_not_fail_alone(tmp_path):
    write(tmp_path / "app.py", "x = 1\n")
    result = readiness.check_dos_surface(tmp_path, {}, mkctx())
    notes = [f["note"] for f in result["findings"]]
    assert "absent" in notes
    assert result["status"] in ("fail", "review")  # body-limit/rate-limit absence also present in this minimal app


# --------------------------------------------------------------------------- tools (external, fake + absence)


FAKE_BANDIT_HIGH = """#!/bin/sh
echo '{"results": [{"issue_severity": "HIGH"}, {"issue_severity": "LOW"}]}'
exit 1
"""

FAKE_PIP_AUDIT_CLEAN = """#!/bin/sh
echo '{"dependencies": [{"name": "x", "vulns": []}]}'
exit 0
"""


def test_tools_skip_when_nothing_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    (tmp_path / "empty-bin").mkdir()
    result = readiness.check_tools(tmp_path, {}, mkctx())
    assert result["status"] == "skip"
    assert "pip-audit" in result["reason"]
    assert "pypi.org/project/pip-audit" in result["reason"]


def test_tools_fail_on_high_severity_from_fake_bandit(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    make_fake_tool(bin_dir, "bandit", FAKE_BANDIT_HIGH)
    monkeypatch.setenv("PATH", str(bin_dir))
    result = readiness.check_tools(tmp_path, {}, mkctx())
    assert result["status"] == "fail"
    assert result["counts"]["bandit"] == {"HIGH": 1, "LOW": 1}
    assert any("bandit: 2 findings" in f["note"] for f in result["findings"])


def test_tools_pass_when_fake_tool_runs_clean(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    make_fake_tool(bin_dir, "pip-audit", FAKE_PIP_AUDIT_CLEAN)
    monkeypatch.setenv("PATH", str(bin_dir))
    result = readiness.check_tools(tmp_path, {}, mkctx())
    assert result["status"] == "pass"
    assert result["counts"]["pip_audit"] == {"vulnerable_packages": 0, "vulnerabilities": 0}


def test_tools_semgrep_skipped_without_config_note(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    (tmp_path / "empty-bin").mkdir()
    result = readiness.check_tools(tmp_path, {}, mkctx())
    assert any("semgrep" in r and "network" in r for r in [result["reason"]])


# --------------------------------------------------------------------------- disable / config wiring


def test_disabled_check_reported_as_skip(tmp_path):
    r = run_cli([".", "--json"], cwd=tmp_path, env={**os.environ, "READINESS_UNUSED": "1"})
    write(tmp_path / ".readiness.json", json.dumps({"disable": ["tools", "history-secrets"]}))
    r = run_cli([".", "--json"], cwd=tmp_path)
    data = json.loads(r.stdout)
    tools_check = next(c for c in data["checks"] if c["id"] == "tools")
    assert tools_check["status"] == "skip"
    assert tools_check["reason"] == "disabled via config"


# --------------------------------------------------------------------------- schema / fixed order


def test_every_check_has_false_positive_note_and_command(tmp_path):
    r = run_cli([".", "--json"], cwd=tmp_path)
    data = json.loads(r.stdout)
    for c in data["checks"]:
        assert c["false_positive_note"], c["id"]
        assert c["command"], c["id"]


def test_checks_emitted_in_fixed_order(tmp_path):
    r = run_cli([".", "--json"], cwd=tmp_path)
    data = json.loads(r.stdout)
    ids = [c["id"] for c in data["checks"]]
    assert ids == [
        "archive-hygiene", "history-secrets", "credential-patterns", "identifier-shapes",
        "config-endpoint-secrets", "redaction-at-publish", "debug-endpoint-exposure", "html-sinks",
        "async-terminal-states", "vendor-mode-probes", "idempotency-keys", "client-supplied-money",
        "skipped-credentialed-tiers", "contract-artifact-drift", "action-pinning", "runtime-version-drift",
        "docs-endpoint-drift", "dos-surface", "tools",
    ]


def test_markdown_hard_capped_around_150_lines(tmp_path):
    # Build many failing findings so the renderer would overflow without the cap.
    write(tmp_path / ".github" / "workflows" / "ci.yml",
          "jobs:\n  x:\n    steps:\n" + "".join(f"      - uses: some-org/action-{i}@v1\n" for i in range(200)))
    r = run_cli(["."], cwd=tmp_path)
    assert len(r.stdout.splitlines()) <= 151


# --------------------------------------------------------------------------- conductor's additions after the worker's delivery


def test_marker_less_dos_controls_are_not_applicable_without_a_web_framework(tmp_path):
    (tmp_path / "lib.py").write_text("def add(a, b):\n    return a + b\n")
    out = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path), "--only", "dos-surface", "--json"], capture_output=True, text=True)
    check = [c for c in json.loads(out.stdout)["checks"] if c["id"] == "dos-surface"][0]
    notes = [f["note"] for f in check["findings"]]
    for label in ("request body size limit", "rate limiting", "concurrency and keep-alive caps"):
        assert any(n.startswith(label) and "not applicable" in n for n in notes), label
    assert check["status"] != "fail"
    # the same tree with a web server present: the three controls are judged absent
    (tmp_path / "app.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    out = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path), "--only", "dos-surface", "--json"], capture_output=True, text=True)
    check = [c for c in json.loads(out.stdout)["checks"] if c["id"] == "dos-surface"][0]
    notes = " | ".join(f["note"] for f in check["findings"])
    assert "request body size limit absent" in notes and check["status"] == "fail"


def test_scanner_does_not_scan_its_own_source(tmp_path):
    import shutil, subprocess, sys, json
    # a copy of the scanner under its own name is still scanned; the running file itself is not
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "app.js").write_text("el.textContent = 'safe';\n")
    out = subprocess.run([sys.executable, str(SCRIPT), str(SCRIPT.parent.parent.parent.parent.parent), "--only", "html-sinks,redaction-at-publish", "--json"], capture_output=True, text=True)
    checks = {c["id"]: c for c in json.loads(out.stdout)["checks"]}
    own = str(SCRIPT.relative_to(SCRIPT.parents[4]))
    assert all(own not in f["path"] for c in checks.values() for f in c["findings"])
