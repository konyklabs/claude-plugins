#!/usr/bin/env python3
"""Deterministic production-readiness scan of an application repository.

Runs a fixed, ordered set of checks (secrets, debug surfaces, correctness
traps, CI/supply-chain, docs drift, denial-of-service surface, and summarized
external tools) and emits a bounded, categorized report so a model reads a
table instead of the code.

Usage:
    readiness.py [ROOT] [--tier precommit|release] [--json] [--config PATH]
                 [--out DIR] [--archive PATH] [--history N] [--only ID[,ID]]

Defaults: ROOT is ``.``; ``--tier`` is ``release`` (the full set; ``precommit``
runs only the fast subset); ``--config`` is ``ROOT/.readiness.json`` if it
exists. Markdown goes to stdout and the full report to
``ROOT/.readiness/report.json`` (or ``--out DIR/report.json``) unless
``--json``, in which case the full JSON goes to stdout and nothing is
written. Exit 0 when no check is "fail", 1 when any is, 2 on usage error.

Findings never carry matched secret text — only a path, a line, and a rule
name — so this script's own output is safe to paste anywhere.

Standard library only. No network calls. External tools (gitleaks,
pip-audit, bandit, semgrep, osv-scanner, trivy, npm, lychee) are invoked only
if already on PATH; nothing is installed. Deterministic: same tree, same
output (the report carries no timestamps).
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- constants

# Directories that are never application code: VCS internals, dependency
# trees, build output and caches. Skipped everywhere a tree is walked.
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__",
    ".pytest_cache", ".mypy_cache", "vendor",
    # This script's own output directory, so a second run never scans a
    # prior report.json (which would otherwise make output depend on history).
    ".readiness",
}

# 1 MiB: large enough for any real source file, small enough that a vendored
# bundle or data blob does not blow up read time or produce noise findings.
MAX_FILE_BYTES = 1024 * 1024

# This file's own source contains every pattern it searches for; scanning it
# would report the scanner. Skipped by identity, so a copy under another name
# is still scanned.
SELF_PATH = Path(__file__).resolve()

# Markers that a repository runs a Python web server. The three DoS controls
# that have no per-site marker of their own (body limit, rate limit,
# concurrency cap) are judged only when one of these is present; a library
# or a tool repository is "not applicable", not "absent".
_WEB_FRAMEWORK_RE = re.compile(r"\b(fastapi|starlette|flask|django|aiohttp\.web|sanic|tornado|quart|litestar|falcon|bottle|uvicorn|gunicorn|hypercorn|waitress)\b")

# 256 KiB: the size ceiling for scanning an archive entry's content for
# key-shaped strings; larger entries are almost never source text.
ARCHIVE_TEXT_LIMIT = 256 * 1024

# External tools get up to five minutes; long enough for a full dependency
# scan, short enough that a hung tool does not stall the whole report.
TOOL_TIMEOUT = 300

TEST_FILE_RE = re.compile(r"^(test_.*|.*_test)\.py$")

HTML_SINK_EXTS = {".js", ".mjs", ".ts", ".jsx", ".tsx", ".html", ".htm", ".vue", ".svelte"}

# Directories whose contents are excluded from the identifier-shapes scan
# unless they sit under one of these (fixtures and docs are where synthetic
# lookalike identifiers legitimately live).
IDENTIFIER_SCAN_DIRS = {"tests", "test", "fixtures", "examples", "docs", "samples"}

# --- secret patterns, used by archive-hygiene and (indirectly) history-secrets docs.
# Every pattern names the credential format it targets; the last one is
# intentionally broad and is called out as noisy in its own false_positive_note.
SECRET_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws-access-key-id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("stripe-live-key", re.compile(r"sk_live_[0-9A-Za-z]{8,}")),
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")),
    ("github-pat", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("slack-token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.")),
    ("generic-secret-assignment", re.compile(r"(?i)\b(secret|password|api[_-]?key|token)\s*[=:]\s*['\"][^'\"\s]{16,}['\"]")),
]

_ARCHIVE_FP_NOTE = (
    "the generic secret-assignment pattern is noisy: it matches any long quoted "
    "string assigned to secret/password/api_key/token, including test fixtures "
    "and mocks; exclude those paths via archive_ignore_globs in .readiness.json"
)

# Path rules an archive entry is checked against, independent of content.
_ARCHIVE_NAME_RULES: List[Tuple[str, re.Pattern[str]]] = [
    ("dotenv file", re.compile(r"(^|/)\.env(\.[^/]*)?($|/)")),
    ("git internals", re.compile(r"(^|/)\.git/")),
    ("python bytecode cache", re.compile(r"(^|/)__pycache__/")),
    ("vendored node_modules", re.compile(r"(^|/)node_modules/")),
    ("pytest cache", re.compile(r"(^|/)\.pytest_cache")),
    ("private key file", re.compile(r"\.(pem|key|p12|pfx)$")),
    ("ssh private key", re.compile(r"(^|/)(id_rsa|id_ed25519)$")),
    ("macOS metadata", re.compile(r"(^|/)\.DS_Store$")),
]

_ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "options", "head", "websocket"}

_CONFIG_ROUTE_RE = re.compile(r"config|settings|env|bootstrap", re.IGNORECASE)

_STREAM_MARKERS_RE = re.compile(r"text/event-stream|EventSource|StreamingResponse|sse_starlette|websocket", re.IGNORECASE)
_REDACT_WORDS_RE = re.compile(r"redact|scrub|mask|sanitize", re.IGNORECASE)
_BUFFER_WORDS_RE = re.compile(r"\bdeque\b|\bbuffer\b|\bhistory\b|\breplay\b|\bbacklog\b", re.IGNORECASE)
_ALLOWLIST_WORDS_RE = re.compile(r"allow_?list|ALLOWED", re.IGNORECASE)
_DENYLIST_WORDS_RE = re.compile(r"deny_?list|BLOCKED|SENSITIVE_KEYS")

_DEBUG_ROUTE_RE = re.compile(r"/debug|/__|/inspector|/_internal|/admin")
_CLIENT_ADDR_RE = re.compile(r"request\.client\.host|client\.host|remote_addr|X-Forwarded-For", re.IGNORECASE)
_LOOPBACK_LITERAL_RE = re.compile(r"127\.0\.0\.1|::1|localhost")

_HTML_SINK_RE = re.compile(
    r"\.innerHTML\s*=|\.outerHTML\s*=|\binsertAdjacentHTML\(|\bdocument\.write\("
    r"|new\s+DOMParser\(|createContextualFragment\(|\beval\(|new\s+Function\("
)

_STATUS_202_RE = re.compile(r"status_code\s*=\s*202|\b202\b")
_PENDING_LITERAL_RE = re.compile(r"['\"](pending|processing|queued|in_progress)['\"]")
_TERMINAL_STATE_RE = re.compile(r"\b(failed|succeeded|completed|canceled|cancelled|rejected)\b", re.IGNORECASE)

_TEST_MODE_RE = re.compile(r"sk_test_|\bsandbox\b|\bTEST_MODE\b", re.IGNORECASE)
_LIVE_MODE_RE = re.compile(r"sk_live_|\bproduction\b|\bLIVE\b")

_IDEMPOTENCY_RE = re.compile(r"idempotency-key", re.IGNORECASE)
_KEY_GEN_RE = re.compile(r"uuid4\(\)|uuid\.uuid4\(\)|token_hex\(|token_urlsafe\(")
_MONEY_POST_RE = re.compile(r"payment|charge|order|transfer|refund|capture", re.IGNORECASE)

_MONEY_READ_RE = re.compile(
    r"body\[|payload\[|\.get\(\s*[\"']amount[\"']|\.get\(\s*[\"']total[\"']"
    r"|\.get\(\s*[\"']price[\"']|\.get\(\s*[\"']subtotal[\"']|\.amount\b"
    r"|data\[[\"']total[\"']\]|data\[[\"']amount[\"']\]",
    re.IGNORECASE,
)
_OUTBOUND_CALL_RE = re.compile(r"httpx\.|requests\.|client\.post\(|client\.request\(|\.post\(")
_RECOMPUTE_RE = re.compile(r"compute|calculate|recalc|sum\(|lookup|price_for|catalog|verify", re.IGNORECASE)

_SKIP_DECORATOR_RE = re.compile(r"skipif\(|pytest\.skip\(|mark\.skip\b")
_CRED_ENV_RE = re.compile(r"environ|getenv|TOKEN|KEY|SECRET|CREDENTIAL|PASSWORD")

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_USES_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)")

_ENDPOINT_MENTION_RE = re.compile(r"/api/[A-Za-z0-9_./{}-]+|/v[0-9]+/[A-Za-z0-9_./{}-]+")

_BODY_LIMIT_RE = re.compile(r"client_max_body_size|MAX_CONTENT_LENGTH|max_body|RequestSizeLimit|limit_request_body|max_request_size")
_RATE_LIMIT_RE = re.compile(r"slowapi|Limiter|ratelimit|RateLimit|throttle", re.IGNORECASE)
_CONCURRENCY_RE = re.compile(r"--limit-concurrency|limit_concurrency|--timeout-keep-alive|timeout_keep_alive|--max-requests|workers")
_REGEX_DOS_RE = re.compile(r"\([^()]*[+*]\)[+*{]|(\.\*){2,}")
_UPLOAD_RE = re.compile(r"UploadFile|request\.files|FileStorage")
_UPLOAD_SIZE_NEARBY_RE = re.compile(r"size|MAX_|content_length|SpooledTemporaryFile", re.IGNORECASE)
_CORS_STAR_RE = re.compile(r"allow_origins\s*=\s*\[\s*[\"']\*[\"']\s*\]|CORS_ORIGIN_ALLOW_ALL\s*=\s*True")
_ALLOW_CREDS_RE = re.compile(r"allow_credentials\s*=\s*True")
_DEBUG_FLAG_RE = re.compile(r"\bdebug\s*=\s*True\b|\bDEBUG\s*=\s*True\b|\breload\s*=\s*True\b")
_SHUTDOWN_RE = re.compile(r"lifespan|on_event\(\s*[\"']shutdown[\"']\s*\)|signal\.signal|SIGTERM")
_OUTBOUND_CALL_LINE_RE = re.compile(r"\bhttpx\.\w+\(|\brequests\.\w+\(")


# --------------------------------------------------------------------------- generic filesystem helpers


def iter_files(root: Path):
    """Walk root, skipping SKIP_DIRS and minified JS, in deterministic order."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in sorted(filenames):
            if f.endswith(".min.js"):
                continue
            path = Path(dirpath) / f
            if path.resolve() == SELF_PATH:
                continue
            yield path


def read_text(path: Path) -> Optional[str]:
    """UTF-8 with replacement; None for files over MAX_FILE_BYTES or unreadable."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
    except OSError:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _matches_any_glob(path: str, globs: List[str]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in globs)


def which(name: str) -> Optional[str]:
    return shutil.which(name)


def run_tool(cmd: List[str], cwd: Path, timeout: int = TOOL_TIMEOUT) -> Tuple[Optional[int], str, str, Optional[str]]:
    """Run cmd; returns (returncode, stdout, stderr, error_kind). error_kind is
    'not found' or 'timeout' when the process never produced a returncode."""
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode, p.stdout, p.stderr, None
    except FileNotFoundError:
        return None, "", "", "not found"
    except subprocess.TimeoutExpired:
        return None, "", "", "timeout"
    except OSError as e:
        return None, "", str(e), "error"


def is_git_repo(root: Path) -> bool:
    rc, out, _err, _kind = run_tool(["git", "rev-parse", "--is-inside-work-tree"], root, timeout=30)
    return rc == 0 and out.strip() == "true"


def _dockerfiles(root: Path):
    for path in iter_files(root):
        if path.name == "Dockerfile" or path.name.startswith("Dockerfile."):
            yield path


def _compose_files(root: Path):
    for path in iter_files(root):
        n = path.name
        if any(fnmatch.fnmatch(n, pat) for pat in ("docker-compose*.yml", "docker-compose*.yaml", "compose*.yml", "compose*.yaml")):
            yield path


def _tracked_or_walked_files(root: Path, ctx: Dict[str, Any]):
    if ctx["is_git"]:
        rc, out, _err, _kind = run_tool(["git", "ls-files"], root, timeout=60)
        if rc == 0:
            for name in sorted(l for l in out.splitlines() if l.strip()):
                p = root / name
                if p.is_file():
                    yield p
            return
    for p in iter_files(root):
        yield p


# --------------------------------------------------------------------------- result shape


def make_check(
    id_: str, title: str, group: str, tier: str,
    status: str = "pass", reason: str = "",
    findings: Optional[List[Dict[str, Any]]] = None,
    counts: Optional[Dict[str, Any]] = None,
    fp_note: str = "", command: str = "",
) -> Dict[str, Any]:
    return {
        "id": id_,
        "title": title,
        "group": group,
        "tier": tier,
        "status": status,
        "reason": reason,
        "findings": findings or [],
        "counts": counts or {},
        "false_positive_note": fp_note,
        "command": command,
    }


def finding(path: str, line: int, note: str) -> Dict[str, Any]:
    return {"path": path, "line": line, "note": note}


# --------------------------------------------------------------------------- archive helpers


def _archive_name_rule(name: str) -> Optional[str]:
    norm = name.replace("\\", "/")
    for title, pat in _ARCHIVE_NAME_RULES:
        if pat.search(norm):
            return title
    return None


def _list_archive(path: Path) -> List[Tuple[str, int, bool]]:
    """Deterministically sorted (name, size, is_file) for every entry."""
    if zipfile.is_zipfile(str(path)):
        with zipfile.ZipFile(str(path)) as zf:
            entries = [(i.filename, i.file_size, not i.filename.endswith("/")) for i in zf.infolist()]
    else:
        with tarfile.open(str(path)) as tf:
            entries = [(m.name, m.size, m.isfile()) for m in tf.getmembers()]
    entries.sort(key=lambda e: e[0])
    return entries


def _read_archive_entry(path: Path, name: str) -> Optional[bytes]:
    try:
        if zipfile.is_zipfile(str(path)):
            with zipfile.ZipFile(str(path)) as zf:
                return zf.read(name)
        with tarfile.open(str(path)) as tf:
            member = tf.getmember(name)
            f = tf.extractfile(member)
            return f.read() if f else None
    except (OSError, KeyError):
        return None


# --------------------------------------------------------------------------- route (AST) helpers


def _decorator_call_info(dec: ast.expr) -> Optional[Tuple[str, Optional[str]]]:
    """(METHOD, path-or-None) for @app.get("/x"), @router.post(...), or Flask
    @app.route("/x", methods=["POST"]); None for anything else."""
    if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
        return None
    attr = dec.func.attr
    path = None
    if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
        path = dec.args[0].value
    if attr == "route":
        methods = None
        for kw in dec.keywords:
            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                methods = [e.value for e in kw.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        return (",".join(m.upper() for m in methods) if methods else "GET", path)
    if attr in _ROUTE_METHODS:
        return (attr.upper(), path)
    return None


def find_routes(root: Path) -> List[Dict[str, Any]]:
    routes: List[Dict[str, Any]] = []
    for path in iter_files(root):
        if path.suffix != ".py":
            continue
        text = read_text(path)
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    info = _decorator_call_info(dec)
                    if info is None:
                        continue
                    method, route_path = info
                    routes.append({
                        "file": path, "rel": rel(path, root), "line": node.lineno,
                        "func": node.name, "method": method, "route_path": route_path or "",
                        "node": node, "text": text,
                    })
    routes.sort(key=lambda r: (r["rel"], r["line"]))
    return routes


def _routes(root: Path, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    if "routes" not in ctx:
        ctx["routes"] = find_routes(root)
    return ctx["routes"]


def _function_source(route: Dict[str, Any]) -> Optional[str]:
    node = route.get("node")
    text = route.get("text")
    if node is None or text is None:
        return None
    lines = text.splitlines()
    end = getattr(node, "end_lineno", None) or min(len(lines), node.lineno - 1 + 60)
    return "\n".join(lines[node.lineno - 1: end])


def _has_pagination_param(route: Dict[str, Any]) -> bool:
    node = route.get("node")
    if node is None:
        return False
    names = [a.arg for a in node.args.args + node.args.kwonlyargs]
    return any(a in ("limit", "page_size", "per_page") for a in names)


# --------------------------------------------------------------------------- checks


def check_archive_hygiene(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "archive-hygiene", "Archive hygiene", "secrets", "precommit"
    command = "git archive --format=tar HEAD"
    archive_arg = ctx.get("archive")
    tmp_path: Optional[Path] = None
    try:
        if archive_arg:
            archive_path = Path(archive_arg)
            if not archive_path.exists():
                return make_check(cid, title, group, tier, status="skip",
                                   reason=f"archive not found: {archive_arg}",
                                   fp_note=_ARCHIVE_FP_NOTE, command=f"inspect {archive_arg}")
        elif ctx["is_git"]:
            fd, tmp_name = tempfile.mkstemp(suffix=".tar")
            os.close(fd)
            tmp_path = Path(tmp_name)
            rc, _out, err, _kind = run_tool(["git", "archive", "--format=tar", "HEAD", "-o", str(tmp_path)], root, timeout=120)
            if rc != 0:
                return make_check(cid, title, group, tier, status="skip",
                                   reason=f"git archive failed: {(err or '').strip()[:200]}",
                                   fp_note=_ARCHIVE_FP_NOTE, command=command)
            archive_path = tmp_path
        else:
            return make_check(cid, title, group, tier, status="skip",
                               reason="not a git repository and no --archive",
                               fp_note=_ARCHIVE_FP_NOTE, command=command)

        ignore_globs = config.get("archive_ignore_globs") or []
        entries = _list_archive(archive_path)
        findings: List[Dict[str, Any]] = []
        for name, _size, _is_file in entries:
            rule = _archive_name_rule(name)
            if rule:
                findings.append(finding(name, 0, rule))
        for name, size, is_file in entries:
            if not is_file or size > ARCHIVE_TEXT_LIMIT or _matches_any_glob(name, ignore_globs):
                continue
            data = _read_archive_entry(archive_path, name)
            if data is None:
                continue
            text = data.decode("utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                for rule_name, pat in SECRET_PATTERNS:
                    if pat.search(line):
                        findings.append(finding(name, lineno, rule_name))

        status = "fail" if findings else "pass"
        counts = {"entries": len(entries), "findings": len(findings)}

        if ctx["is_git"]:
            rc, out, _err, _kind = run_tool(["git", "ls-files", "--others", "--ignored", "--exclude-standard"], root, timeout=30)
            if rc == 0:
                names = sorted(l for l in out.splitlines() if l.strip())
                if names:
                    findings.append(finding(
                        ", ".join(names[:3]), 0,
                        f"{len(names)} ignored files present in the working tree; a zip built from the working tree would ship them",
                    ))
                    counts["ignored_in_worktree"] = len(names)

        return make_check(cid, title, group, tier, status=status, findings=findings, counts=counts,
                           fp_note=_ARCHIVE_FP_NOTE, command=command)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def check_history_secrets(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "history-secrets", "Secrets in git history", "secrets", "release"
    fp_note = ("gitleaks has known false positives on high-entropy test fixtures and "
               "generated lockfile hashes; triage each RuleID before treating a hit as real")
    command = "gitleaks detect --source <root> --no-banner --report-format json --report-path <tmp>"
    findings: List[Dict[str, Any]] = []
    counts: Dict[str, Any] = {}
    status, reason = "pass", ""

    if which("gitleaks"):
        fd, tmp_name = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        tmp_report = Path(tmp_name)
        try:
            _rc, _out, err, kind = run_tool(
                ["gitleaks", "detect", "--source", str(root), "--no-banner",
                 "--report-format", "json", "--report-path", str(tmp_report)],
                root, timeout=300,
            )
            if kind == "timeout":
                status, reason = "skip", "gitleaks timed out after 300s"
            elif kind == "not found":
                status, reason = "skip", "gitleaks not installed"
                command += "; install from https://github.com/gitleaks/gitleaks/releases (pin version and checksum)"
            elif kind == "error":
                status, reason = "skip", f"gitleaks could not run: {err.strip()[:200]}"
            else:
                try:
                    report_text = tmp_report.read_text(encoding="utf-8", errors="replace")
                    report = json.loads(report_text) if report_text.strip() else []
                except (OSError, json.JSONDecodeError):
                    report = []
                by_rule: Dict[str, int] = {}
                for item in report:
                    rule_id = item.get("RuleID", "unknown")
                    by_rule[rule_id] = by_rule.get(rule_id, 0) + 1
                    findings.append(finding(item.get("File", ""), item.get("StartLine", 0), rule_id))
                counts = dict(sorted(by_rule.items()))
                status = "fail" if findings else "pass"
        finally:
            tmp_report.unlink(missing_ok=True)
    else:
        status, reason = "skip", "gitleaks not installed"
        command += "; install from https://github.com/gitleaks/gitleaks/releases (pin version and checksum)"

    wf_dir = root / ".github" / "workflows"
    if wf_dir.is_dir():
        for wf in sorted(wf_dir.glob("*.y*ml")):
            text = read_text(wf)
            if text is None:
                continue
            if re.search(r"gitleaks|secret", text, re.IGNORECASE) and re.search(r"actions/checkout", text) and not re.search(r"fetch-depth:\s*0", text):
                line_no = 1
                for i, l in enumerate(text.splitlines(), 1):
                    if "actions/checkout" in l:
                        line_no = i
                        break
                findings.append(finding(rel(wf, root), line_no, "tip-only checkout: history is not scanned"))
                status = "fail"

    findings.sort(key=lambda f: (f["path"], f["line"]))
    return make_check(cid, title, group, tier, status=status, reason=reason, findings=findings,
                       counts=counts, fp_note=fp_note, command=command)


def check_credential_patterns(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "credential-patterns", "Credential pattern scan", "secrets", "precommit"
    fp_note = ("hosted push protection only recognizes registered partner token formats; "
               "an org-specific format needs its own regex here")
    command = "grep -nE '<regex>' $(git ls-files)"
    raw_patterns = config.get("credential_patterns") or []
    if not raw_patterns:
        return make_check(cid, title, group, tier, status="skip",
                           reason="no credential_patterns configured; hosted push protection only knows registered partner formats, add your own",
                           fp_note=fp_note, command=command)

    patterns = []
    for p in raw_patterns:
        try:
            patterns.append((p["name"], p["regex"], re.compile(p["regex"])))
        except (KeyError, re.error):
            continue

    findings: List[Dict[str, Any]] = []
    for path in _tracked_or_walked_files(root, ctx):
        text = read_text(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, _regex_str, pat in patterns:
                if pat.search(line):
                    findings.append(finding(rel(path, root), lineno, name))

    history_n = ctx.get("history", 0)
    if ctx["is_git"] and history_n > 0:
        rc, out, _err, _kind = run_tool(["git", "rev-list", "-n", str(history_n), "HEAD"], root, timeout=30)
        revs = out.split() if rc == 0 else []
        for rev_hash in revs:
            short = rev_hash[:8]
            for name, regex_str, _pat in patterns:
                rc2, out2, _err2, _kind2 = run_tool(["git", "grep", "-nE", regex_str, rev_hash], root, timeout=60)
                if rc2 != 0:
                    continue
                for line in out2.splitlines():
                    parts = line.split(":", 3)
                    if len(parts) >= 3:
                        lineno = int(parts[2]) if parts[2].isdigit() else 0
                        findings.append(finding(parts[1], lineno, f"{name} @ {short}"))

    status = "fail" if findings else "pass"
    return make_check(cid, title, group, tier, status=status, findings=findings, fp_note=fp_note, command=command)


def check_identifier_shapes(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "identifier-shapes", "Identifier shape scan", "secrets", "precommit"
    fp_note = ("long digit runs are also coordinates, phone and card numbers; match the "
               "exact shape (prefix, length, separators) or do not ship the check")
    command = "grep -nE '<regex>' <tests|fixtures|examples|docs|samples>/**"
    raw_patterns = config.get("identifier_patterns") or []
    if not raw_patterns:
        return make_check(cid, title, group, tier, status="skip",
                           reason="no identifier_patterns configured", fp_note=fp_note, command=command)

    patterns = []
    for p in raw_patterns:
        try:
            patterns.append((p["name"], re.compile(p["regex"])))
        except (KeyError, re.error):
            continue

    findings: List[Dict[str, Any]] = []
    for path in iter_files(root):
        relp = rel(path, root)
        parts = Path(relp).parts[:-1]
        if not any(part in IDENTIFIER_SCAN_DIRS for part in parts):
            continue
        text = read_text(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, pat in patterns:
                if pat.search(line):
                    findings.append(finding(relp, lineno, name))

    status = "fail" if findings else "pass"
    return make_check(cid, title, group, tier, status=status, findings=findings, fp_note=fp_note, command=command)


def check_config_endpoint_secrets(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "config-endpoint-secrets", "Config/settings endpoint secret exposure", "secrets", "release"
    fp_note = "the field list is not evidence; assert the secret is absent from the serialized response"
    command = "grep route decorators for config/settings/env/bootstrap paths, then grep tests for the route path and 'not in'"

    routes = [r for r in _routes(root, ctx) if r["route_path"] and _CONFIG_ROUTE_RE.search(r["route_path"])]
    if not routes:
        return make_check(cid, title, group, tier, status="pass", fp_note=fp_note, command=command)

    test_texts = []
    for path in iter_files(root):
        if path.suffix == ".py" and "test" in path.name.lower():
            text = read_text(path)
            if text:
                test_texts.append(text)

    findings: List[Dict[str, Any]] = []
    all_covered = True
    for r in routes:
        covered = any(r["route_path"] in t and "not in" in t for t in test_texts)
        note = f"route {r['route_path']} exposes config-like data" + (" (absence test found)" if covered else "")
        findings.append(finding(r["rel"], r["line"], note))
        all_covered = all_covered and covered

    status = "pass" if all_covered else "review"
    return make_check(cid, title, group, tier, status=status, findings=findings, fp_note=fp_note, command=command)


def check_redaction_at_publish(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "redaction-at-publish", "Redaction at stream publish", "debug-surfaces", "release"
    fp_note = ("word-presence is a proxy; a file can say 'redact' in a comment and still leak, "
               "or redact correctly without any of these words appearing")
    command = "grep -l 'text/event-stream|EventSource|StreamingResponse|sse_starlette|websocket' **/*.py"

    stream_files = []
    for path in iter_files(root):
        if path.suffix != ".py":
            continue
        text = read_text(path)
        if text and _STREAM_MARKERS_RE.search(text):
            stream_files.append((path, text))

    if not stream_files:
        return make_check(cid, title, group, tier, status="pass", counts={"streams": 0}, fp_note=fp_note, command=command)

    findings: List[Dict[str, Any]] = []
    for path, text in stream_files:
        relp = rel(path, root)
        findings.append(finding(relp, 1, "redaction in publisher" if _REDACT_WORDS_RE.search(text) else "no redaction word in publisher"))
        if _BUFFER_WORDS_RE.search(text):
            findings.append(finding(relp, 1, "replay buffer present: redact before buffering"))
        allow_m = _ALLOWLIST_WORDS_RE.search(text)
        deny_m = _DENYLIST_WORDS_RE.search(text)
        if allow_m:
            findings.append(finding(relp, 1, f"allowlist word found: {allow_m.group(0)}"))
        if deny_m:
            findings.append(finding(relp, 1, f"denylist word found: {deny_m.group(0)}"))

    return make_check(cid, title, group, tier, status="review", findings=findings,
                       counts={"streams": len(stream_files)}, fp_note=fp_note, command=command)


def check_debug_endpoint_exposure(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "debug-endpoint-exposure", "Debug endpoint exposure", "debug-surfaces", "release"
    fp_note = "presence of the words is a proxy for the check, not a proof the guard runs on every branch before the handler body"
    command = "grep route decorators for /debug, /__, /inspector, /_internal, /admin"

    routes = [r for r in _routes(root, ctx) if r["route_path"] and _DEBUG_ROUTE_RE.search(r["route_path"])]
    if not routes:
        return make_check(cid, title, group, tier, status="pass", fp_note=fp_note, command=command)

    findings: List[Dict[str, Any]] = []
    any_fail = False
    for r in routes:
        text = r["text"]
        has_addr = bool(_CLIENT_ADDR_RE.search(text))
        has_loopback = bool(_LOOPBACK_LITERAL_RE.search(text))
        lines = text.splitlines()
        window = "\n".join(lines[max(0, r["line"] - 1): r["line"] - 1 + 40])
        has_token = "token" in window.lower()
        if has_addr and has_loopback:
            findings.append(finding(r["rel"], r["line"], "per-request loopback check present"))
        else:
            findings.append(finding(r["rel"], r["line"], "no per-request loopback check"))
            any_fail = True
        if has_token:
            findings.append(finding(r["rel"], r["line"], "token is a tripwire, not auth"))

    status = "fail" if any_fail else "pass"
    return make_check(cid, title, group, tier, status=status, findings=findings, fp_note=fp_note, command=command)


def check_html_sinks(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "html-sinks", "HTML sinks", "debug-surfaces", "precommit"
    fp_note = "a sink fed a constant is safe; the finding is the sink, the auditor decides the source"
    command = "grep -nE '<sink-regex>' **/*.{js,mjs,ts,jsx,tsx,html,htm,vue,svelte}"

    findings: List[Dict[str, Any]] = []
    for path in iter_files(root):
        if path.suffix not in HTML_SINK_EXTS:
            continue
        text = read_text(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            m = _HTML_SINK_RE.search(line)
            if m:
                findings.append(finding(rel(path, root), lineno, m.group(0).strip()))

    status = "fail" if findings else "pass"
    return make_check(cid, title, group, tier, status=status, findings=findings,
                       counts={"sinks": len(findings)}, fp_note=fp_note, command=command)


def check_async_terminal_states(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "async-terminal-states", "Async terminal states", "correctness", "release"
    fp_note = "the scanner flags candidate async-status code; only a human can tell whether the terminal states are actually tested elsewhere"
    command = "grep -nE '202|pending|processing|queued|in_progress' **/*.py; look for a while loop near sleep( and .get("

    sites: List[Dict[str, Any]] = []
    for path in iter_files(root):
        if path.suffix != ".py":
            continue
        text = read_text(path)
        if text is None:
            continue
        lines = text.splitlines()
        hit_lines = set()
        for i, line in enumerate(lines):
            if _STATUS_202_RE.search(line) or _PENDING_LITERAL_RE.search(line):
                hit_lines.add(i)
        for i, line in enumerate(lines):
            if "while" in line:
                window = "\n".join(lines[i: i + 15])
                if "sleep(" in window and ".get(" in window:
                    hit_lines.add(i)
        for i in sorted(hit_lines):
            sites.append(finding(rel(path, root), i + 1, "async terminal-state candidate"))

    if not sites:
        return make_check(cid, title, group, tier, status="pass", fp_note=fp_note, command=command)

    test_files_with_terminal = 0
    for path in iter_files(root):
        if path.suffix == ".py" and TEST_FILE_RE.match(path.name):
            text = read_text(path)
            if text and _TERMINAL_STATE_RE.search(text):
                test_files_with_terminal += 1

    note = (f"{test_files_with_terminal} test files assert terminal states"
            if test_files_with_terminal else "no test mentions a terminal state")
    findings = sites + [finding("", 0, note)]
    return make_check(cid, title, group, tier, status="review", findings=findings,
                       counts={"sites": len(sites), "test_files_with_terminal_states": test_files_with_terminal},
                       fp_note=fp_note, command=command)


def check_vendor_mode_probes(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "vendor-mode-probes", "Vendor mode probes", "correctness", "release"
    fp_note = "'sandbox'/'production' are common words outside payment context; treat a hit as a prompt to check, not proof of a real key-mode branch"
    command = "grep -lE 'sk_test_|sandbox|TEST_MODE' **/*.py; grep -lE 'sk_live_|production|LIVE' **/*.py"

    findings: List[Dict[str, Any]] = []
    for path in iter_files(root):
        if path.suffix != ".py" or TEST_FILE_RE.match(path.name):
            continue
        text = read_text(path)
        if text is None:
            continue
        if _TEST_MODE_RE.search(text) and _LIVE_MODE_RE.search(text):
            findings.append(finding(rel(path, root), 1, "probe capabilities, never infer them from a key"))

    status = "review" if findings else "pass"
    return make_check(cid, title, group, tier, status=status, findings=findings, fp_note=fp_note, command=command)


def check_idempotency_keys(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "idempotency-keys", "Idempotency keys", "correctness", "release"
    fp_note = "the upstream client may add the header itself; confirm before fixing"
    command = "grep -niE 'idempotency-key' **/*"

    sites = []
    for path in iter_files(root):
        text = read_text(path)
        if text is None:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if _IDEMPOTENCY_RE.search(line):
                sites.append((path, i, lines))

    if sites:
        findings = []
        for path, i, lines in sites:
            window = "\n".join(lines[max(0, i - 30): i + 30])
            note = "key generated nearby" if _KEY_GEN_RE.search(window) else "no key generation within 30 lines"
            findings.append(finding(rel(path, root), i + 1, note))
        return make_check(cid, title, group, tier, status="review", findings=findings, fp_note=fp_note, command=command)

    money_routes = [
        r for r in _routes(root, ctx)
        if r["method"] and "POST" in r["method"].split(",")
        and (_MONEY_POST_RE.search(r["route_path"] or "") or _MONEY_POST_RE.search(r["func"]))
    ]
    if money_routes:
        findings = [finding(r["rel"], r["line"], "money-moving POST without an idempotency key") for r in money_routes]
        return make_check(cid, title, group, tier, status="fail", findings=findings, fp_note=fp_note,
                           command="grep route decorators for POST + payment|charge|order|transfer|refund|capture, and confirm no Idempotency-Key handling")

    return make_check(cid, title, group, tier, status="pass", fp_note=fp_note, command=command)


def check_client_supplied_money(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "client-supplied-money", "Client-supplied money values", "correctness", "release"
    fp_note = "keyword co-occurrence in a function is a heuristic; a handler can recompute the total under a name this check does not recognize"
    command = "AST-scan Python handlers for amount/total/price/subtotal read + an outbound call without a recompute word, in the same function"

    findings: List[Dict[str, Any]] = []
    for r in _routes(root, ctx):
        src = _function_source(r)
        if src is None:
            continue
        if _MONEY_READ_RE.search(src) and _OUTBOUND_CALL_RE.search(src) and not _RECOMPUTE_RE.search(src):
            findings.append(finding(r["rel"], r["line"], "client total forwarded without server recomputation"))

    status = "review" if findings else "pass"
    return make_check(cid, title, group, tier, status=status, findings=findings, fp_note=fp_note, command=command)


def check_skipped_credentialed_tiers(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "skipped-credentialed-tiers", "Skipped credentialed test tiers", "ci-supply-chain", "release"
    fp_note = "the condition text is matched textually; a skip guarded by a differently named flag that still depends on credentials will not be caught"
    command = r"grep -nE 'skipif\(|pytest\.skip\(|mark\.skip' **/test_*.py | grep -E 'environ|getenv|TOKEN|KEY|SECRET|CREDENTIAL|PASSWORD'"

    findings: List[Dict[str, Any]] = []
    for path in iter_files(root):
        if path.suffix != ".py" or not TEST_FILE_RE.match(path.name):
            continue
        text = read_text(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if _SKIP_DECORATOR_RE.search(line) and _CRED_ENV_RE.search(line):
                findings.append(finding(rel(path, root), lineno, "credentialed skip condition"))

    if not findings:
        return make_check(cid, title, group, tier, status="pass", fp_note=fp_note, command=command)

    secrets_referenced = False
    wf_dir = root / ".github" / "workflows"
    if wf_dir.is_dir():
        for wf in wf_dir.glob("*.y*ml"):
            text = read_text(wf)
            if text and "secrets." in text:
                secrets_referenced = True
                break

    status = "review" if secrets_referenced else "fail"
    tail_note = "check the skip is fork-PR-only" if secrets_referenced else "credentialed tier can skip silently in CI"
    findings.append(finding("", 0, tail_note))
    return make_check(cid, title, group, tier, status=status, findings=findings, fp_note=fp_note, command=command)


def check_contract_artifact_drift(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "contract-artifact-drift", "Contract artifact drift", "ci-supply-chain", "release"
    fp_note = "a workflow can diff the wrong path or run before regeneration; read the step, not just its presence"
    command = "find . -path '*/pacts/*.json' -o -path '*/__snapshots__/*' -o -name '*.snap'; grep -rE 'git diff --exit-code|git diff --quiet|--check' .github/workflows"

    artifacts = []
    for path in iter_files(root):
        relp = rel(path, root)
        if fnmatch.fnmatch(relp, "pacts/*.json") or fnmatch.fnmatch(relp, "*/pacts/*.json") or "__snapshots__/" in relp or relp.endswith(".snap"):
            artifacts.append(path)

    if not artifacts:
        return make_check(cid, title, group, tier, status="pass", counts={"artifacts": 0}, fp_note=fp_note, command=command)

    diffed = False
    wf_dir = root / ".github" / "workflows"
    if wf_dir.is_dir():
        for wf in wf_dir.glob("*.y*ml"):
            text = read_text(wf)
            if text and re.search(r"git diff --exit-code|git diff --quiet|--check\b", text):
                diffed = True
                break

    status = "pass" if diffed else "fail"
    findings = [] if diffed else [finding(rel(a, root), 0, "regenerated contract artifact is not diffed in CI") for a in artifacts[:5]]
    return make_check(cid, title, group, tier, status=status, findings=findings,
                       counts={"artifacts": len(artifacts)}, fp_note=fp_note, command=command)


def _classify_uses(ref: str) -> str:
    if ref.startswith(("./", ".\\")):
        return "local"
    if "@" not in ref:
        return "unpinned"
    path_part, _sep, version = ref.rpartition("@")
    if "/.github/workflows/" in path_part:
        return "reusable"
    if _SHA_RE.match(version):
        return "sha"
    if re.match(r"^v?\d", version):
        return "tag"
    return "branch"


def check_action_pinning(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "action-pinning", "GitHub Actions pinning", "ci-supply-chain", "release"
    fp_note = "prefer the upstream CLI pinned by version and checksum where the action has relicensed"
    command = "grep -nE '^\\s*uses:' .github/workflows/*.yml"

    wf_dir = root / ".github" / "workflows"
    if not wf_dir.is_dir():
        return make_check(cid, title, group, tier, status="pass", fp_note=fp_note, command=command)

    findings: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for wf in sorted(wf_dir.glob("*.y*ml")):
        text = read_text(wf)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            m = _USES_RE.match(line)
            if not m:
                continue
            ref = m.group(1).strip().strip("'\"")
            kind = _classify_uses(ref)
            counts[kind] = counts.get(kind, 0) + 1
            if kind in ("local", "reusable"):
                continue
            owner = ref.split("/", 1)[0] if "/" in ref else ""
            if kind != "sha" and owner not in ("actions", "github"):
                findings.append(finding(rel(wf, root), lineno,
                                         f"{kind}-pinned third-party action {ref}: prefer the upstream CLI pinned by version and checksum where the action has relicensed"))
            elif kind == "tag" and owner in ("actions", "github"):
                findings.append(finding(rel(wf, root), lineno, f"tag-pinned first-party action {ref}"))

    fail_count = sum(1 for f in findings if "third-party" in f["note"])
    status = "fail" if fail_count else ("review" if findings else "pass")
    return make_check(cid, title, group, tier, status=status, findings=findings, counts=counts, fp_note=fp_note, command=command)


def _extract_min_version(spec: str) -> Optional[Tuple[int, int]]:
    m = re.search(r">=\s*(\d+)\.(\d+)", spec) or re.search(r"(\d+)\.(\d+)", spec)
    return (int(m.group(1)), int(m.group(2))) if m else None


def check_runtime_version_drift(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "runtime-version-drift", "Runtime version drift", "ci-supply-chain", "release"
    fp_note = "a workflow matrix intentionally spanning versions is not drift; read the intent before filing this as a bug"
    command = "grep requires-python pyproject.toml; cat .python-version; grep python-version .github/workflows/*.yml; grep '^FROM python' Dockerfile*"

    findings: List[Dict[str, Any]] = []
    floor = None

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        text = read_text(pyproject) or ""
        for lineno, line in enumerate(text.splitlines(), 1):
            m = re.search(r"requires-python\s*=\s*[\"']([^\"']+)[\"']", line)
            if m:
                v = _extract_min_version(m.group(1))
                if v:
                    findings.append(finding(rel(pyproject, root), lineno, f"requires-python floor {v[0]}.{v[1]}"))
                    floor = v

    pv_file = root / ".python-version"
    if pv_file.exists():
        text = (read_text(pv_file) or "").strip()
        if text:
            findings.append(finding(rel(pv_file, root), 1, f".python-version {text}"))

    wf_dir = root / ".github" / "workflows"
    if wf_dir.is_dir():
        for wf in sorted(wf_dir.glob("*.y*ml")):
            text = read_text(wf) or ""
            for lineno, line in enumerate(text.splitlines(), 1):
                m = re.search(r"python-version:\s*\[?[\"']?([0-9][0-9.]*)", line)
                if m:
                    findings.append(finding(rel(wf, root), lineno, f"python-version {m.group(1)}"))

    for dockerfile in _dockerfiles(root):
        text = read_text(dockerfile) or ""
        for lineno, line in enumerate(text.splitlines(), 1):
            m = re.search(r"^FROM\s+python:([0-9][0-9.]*)", line)
            if m:
                findings.append(finding(rel(dockerfile, root), lineno, f"FROM python:{m.group(1)}"))

    devc_dir = root / ".devcontainer"
    if devc_dir.is_dir():
        for path in sorted(devc_dir.rglob("*.json")):
            if not path.is_file():
                continue
            text = read_text(path) or ""
            for lineno, line in enumerate(text.splitlines(), 1):
                m = re.search(r"python:([0-9][0-9.]*)", line) or re.search(r'"VARIANT"\s*:\s*"([0-9][0-9.]*)"', line)
                if m:
                    findings.append(finding(rel(path, root), lineno, f"devcontainer python {m.group(1)}"))

    versions_seen = set()
    for f in findings:
        m = re.search(r"(\d+)\.(\d+)(?!\d)", f["note"])
        if m:
            versions_seen.add((int(m.group(1)), int(m.group(2))))

    status = "pass"
    if len(findings) >= 2 and (len(versions_seen) > 1 or (floor and versions_seen and any(v < floor for v in versions_seen))):
        status = "fail"

    return make_check(cid, title, group, tier, status=status, findings=findings, fp_note=fp_note, command=command)


def check_docs_endpoint_drift(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "docs-endpoint-drift", "Docs/endpoint drift", "docs", "release"
    fp_note = "path-prefix matching only; parameterized segments and versioned aliases can look like drift when they are not"
    command = "diff <(jq -r '.paths|keys[]' openapi.json) <(grep -rEho '/api/[A-Za-z0-9_./{}-]+|/v[0-9]+/[A-Za-z0-9_./{}-]+' docs)"

    spec_path = None
    for path in iter_files(root):
        if path.name in ("openapi.json", "openapi.yaml", "openapi.yml"):
            spec_path = path
            break
    if spec_path is None:
        return make_check(cid, title, group, tier, status="skip", reason="no OpenAPI document found", fp_note=fp_note, command=command)

    text = read_text(spec_path) or ""
    spec_paths: set = set()
    if spec_path.suffix == ".json":
        try:
            data = json.loads(text)
            spec_paths = set(data.get("paths", {}).keys())
        except json.JSONDecodeError:
            spec_paths = set()
    else:
        in_paths = False
        for line in text.splitlines():
            if re.match(r"^paths:\s*$", line):
                in_paths = True
                continue
            if in_paths:
                m = re.match(r"^\s{2}(/\S+):", line)
                if m:
                    spec_paths.add(m.group(1))
                elif line and not line.startswith(" "):
                    in_paths = False

    docs_dirs = config.get("docs_dirs") or ["docs", "README.md"]
    docs_paths: Dict[str, List[Tuple[Path, int]]] = {}
    for d in docs_dirs:
        base = root / d
        if base.is_file():
            candidates = [base]
        elif base.is_dir():
            candidates = sorted(base.rglob("*.md"))
        else:
            candidates = []
        for path in candidates:
            text2 = read_text(path)
            if text2 is None:
                continue
            for lineno, line in enumerate(text2.splitlines(), 1):
                for m in _ENDPOINT_MENTION_RE.finditer(line):
                    docs_paths.setdefault(m.group(0), []).append((path, lineno))

    findings: List[Dict[str, Any]] = []
    for p, locs in sorted(docs_paths.items()):
        if not any(p == sp or p.rstrip("/") == sp.rstrip("/") for sp in spec_paths):
            path, lineno = locs[0]
            findings.append(finding(rel(path, root), lineno, f"documented, not in spec: {p}"))

    in_spec_not_documented = sum(1 for sp in spec_paths if sp not in docs_paths)
    counts = {"documented_not_in_spec": len(findings), "in_spec_not_documented": in_spec_not_documented}
    return make_check(cid, title, group, tier, status="review", findings=findings, counts=counts, fp_note=fp_note, command=command)


def check_dos_surface(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "dos-surface", "DoS surface", "dos", "release"
    fp_note = ("keyword presence is a proxy for the control; a limit enforced at the load balancer, "
               "CDN or platform layer will not show up in the repository at all")
    command = ("grep for body-size/rate-limit/concurrency markers; check Dockerfile and compose files; "
               "grep for CORS wildcard+credentials, debug flags, and quantified-group regex patterns")

    findings: List[Dict[str, Any]] = []
    verdicts: List[str] = []

    py_files = [p for p in iter_files(root) if p.suffix == ".py"]
    texts = {p: (read_text(p) or "") for p in py_files}
    non_test_texts = {p: t for p, t in texts.items() if not TEST_FILE_RE.match(p.name)}
    server_sources = list(non_test_texts.values()) + [read_text(p) or "" for p in list(_dockerfiles(root)) + list(_compose_files(root))]
    if (root / "Procfile").exists():
        server_sources.append(read_text(root / "Procfile") or "")
    web_app = any(_WEB_FRAMEWORK_RE.search(t) for t in server_sources)

    def first_match(pat: re.Pattern[str], source=None):
        source = texts if source is None else source
        for p in sorted(source, key=lambda x: rel(x, root)):
            for lineno, line in enumerate(source[p].splitlines(), 1):
                if pat.search(line):
                    return p, lineno
        return None

    def unconditional(pat: re.Pattern[str], label: str) -> None:
        """A control with no per-site marker: judged only for web apps."""
        hit = first_match(pat)
        if hit:
            findings.append(finding(rel(hit[0], root), hit[1], f"{label} present at {rel(hit[0], root)}:{hit[1]}"))
            verdicts.append("present")
        elif web_app:
            findings.append(finding("", 0, f"{label} absent"))
            verdicts.append("absent")
        else:
            findings.append(finding("", 0, f"{label}: not applicable, no web server framework found"))
            verdicts.append("n/a")

    # 1. request body size limit
    unconditional(_BODY_LIMIT_RE, "request body size limit")

    # 2. outbound timeouts
    bad_calls = []
    any_calls = False
    for p, text in texts.items():
        file_has_timeout_ctor = bool(re.search(r"timeout\s*=|Timeout\(", text))
        for lineno, line in enumerate(text.splitlines(), 1):
            if _OUTBOUND_CALL_LINE_RE.search(line):
                any_calls = True
                if "timeout" not in line.lower() and not file_has_timeout_ctor:
                    bad_calls.append((p, lineno))
    if bad_calls:
        for p, lineno in bad_calls:
            findings.append(finding(rel(p, root), lineno, "outbound call without timeout"))
        verdicts.append("absent")
    elif any_calls:
        findings.append(finding("", 0, "outbound timeouts present"))
        verdicts.append("present")

    # 3. rate limiting
    unconditional(_RATE_LIMIT_RE, "rate limiting")

    # 4. server concurrency / keep-alive caps
    concurrency_sources = list(_dockerfiles(root)) + list(_compose_files(root))
    procfile = root / "Procfile"
    if procfile.exists():
        concurrency_sources.append(procfile)
    scripts_dir = root / "scripts"
    if scripts_dir.is_dir():
        concurrency_sources += [p for p in scripts_dir.rglob("*") if p.is_file()]
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        concurrency_sources.append(pyproject)
    hit = None
    for p in sorted(set(concurrency_sources), key=lambda x: rel(x, root)):
        text = read_text(p) or ""
        for lineno, line in enumerate(text.splitlines(), 1):
            if _CONCURRENCY_RE.search(line):
                hit = (p, lineno)
                break
        if hit:
            break
    if hit:
        findings.append(finding(rel(hit[0], root), hit[1], f"concurrency cap present at {rel(hit[0], root)}:{hit[1]}"))
        verdicts.append("present")
    elif web_app:
        findings.append(finding("", 0, "concurrency and keep-alive caps absent"))
        verdicts.append("absent")
    else:
        findings.append(finding("", 0, "concurrency and keep-alive caps: not applicable, no web server framework found"))
        verdicts.append("n/a")

    # 5. pagination caps on list endpoints
    list_handlers = [r for r in _routes(root, ctx) if _has_pagination_param(r)]
    if list_handlers:
        any_absent = False
        for r in list_handlers:
            src = _function_source(r) or ""
            has_cap = bool(re.search(r"\ble=|max\(|min\(|MAX_", src))
            findings.append(finding(r["rel"], r["line"], "pagination cap present" if has_cap else "pagination cap absent"))
            any_absent = any_absent or not has_cap
        verdicts.append("absent" if any_absent else "present")

    # 6. regex DoS candidates
    regex_dos_hits = []
    for p, text in texts.items():
        for lineno, line in enumerate(text.splitlines(), 1):
            if re.search(r"re\.(compile|match|search|fullmatch|findall|sub)\(", line) and _REGEX_DOS_RE.search(line):
                regex_dos_hits.append((p, lineno))
    if regex_dos_hits:
        for p, lineno in regex_dos_hits:
            findings.append(finding(rel(p, root), lineno, "regex DoS candidate"))
        verdicts.append("flag-review")

    # 7. upload limits
    upload_hit = first_match(_UPLOAD_RE)
    if upload_hit:
        p0, l0 = upload_hit
        lines = texts[p0].splitlines()
        window = "\n".join(lines[max(0, l0 - 10): l0 + 10])
        has_size = bool(_UPLOAD_SIZE_NEARBY_RE.search(window))
        findings.append(finding(rel(p0, root), l0, "upload size limit present" if has_size else "upload size limit absent"))
        verdicts.append("present" if has_size else "absent")

    # 8. container hardening
    dockerfiles = list(_dockerfiles(root))
    if dockerfiles:
        any_absent = False
        for d in sorted(dockerfiles, key=lambda x: rel(x, root)):
            text = read_text(d) or ""
            has_healthcheck = "HEALTHCHECK" in text
            has_nonroot_user = bool(re.search(r"^USER\s+(?!root\b)\S+", text, re.MULTILINE))
            has_digest_pin = bool(re.search(r"^FROM\s+\S+@sha256:", text, re.MULTILINE))
            relp = rel(d, root)
            findings.append(finding(relp, 0, "HEALTHCHECK present" if has_healthcheck else "HEALTHCHECK absent"))
            findings.append(finding(relp, 0, "non-root USER present" if has_nonroot_user else "non-root USER absent"))
            findings.append(finding(relp, 0, "FROM pinned by digest" if has_digest_pin else "FROM not pinned by digest"))
            if not (has_healthcheck and has_nonroot_user and has_digest_pin):
                any_absent = True
        verdicts.append("absent" if any_absent else "present")

    # 9. compose resource limits
    compose_files = list(_compose_files(root))
    if compose_files:
        any_absent = False
        for c in sorted(compose_files, key=lambda x: rel(x, root)):
            text = read_text(c) or ""
            has_limits = bool(re.search(r"resources:", text) and re.search(r"limits:", text))
            findings.append(finding(rel(c, root), 0, "compose resource limits present" if has_limits else "compose resource limits absent"))
            any_absent = any_absent or not has_limits
        verdicts.append("absent" if any_absent else "present")

    # 10. CORS wildcard origin with credentials
    cors_hits = []
    for p, text in sorted(non_test_texts.items(), key=lambda kv: rel(kv[0], root)):
        if _CORS_STAR_RE.search(text) and _ALLOW_CREDS_RE.search(text):
            for lineno, line in enumerate(text.splitlines(), 1):
                if _CORS_STAR_RE.search(line) or _ALLOW_CREDS_RE.search(line):
                    cors_hits.append((p, lineno))
    if cors_hits:
        for p, lineno in cors_hits:
            findings.append(finding(rel(p, root), lineno, "CORS wildcard origin with credentials allowed"))
        verdicts.append("flag-fail")

    # 11. debug flags in non-test code
    debug_hits = []
    for p, text in sorted(non_test_texts.items(), key=lambda kv: rel(kv[0], root)):
        for lineno, line in enumerate(text.splitlines(), 1):
            if _DEBUG_FLAG_RE.search(line):
                debug_hits.append((p, lineno))
    if debug_hits:
        for p, lineno in debug_hits:
            findings.append(finding(rel(p, root), lineno, "debug flag enabled in non-test code"))
        verdicts.append("flag-fail")

    # 12. graceful shutdown (absent is review, not fail)
    hit = first_match(_SHUTDOWN_RE)
    if hit:
        findings.append(finding(rel(hit[0], root), hit[1], f"present at {rel(hit[0], root)}:{hit[1]}"))
    else:
        findings.append(finding("", 0, "absent"))
        verdicts.append("absent-review")

    fail = any(v in ("absent", "flag-fail") for v in verdicts)
    review = any(v in ("unknown", "flag-review", "absent-review") for v in verdicts)
    status = "fail" if fail else ("review" if review else "pass")
    return make_check(cid, title, group, tier, status=status, findings=findings, fp_note=fp_note, command=command)


def check_tools(root: Path, config: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    cid, title, group, tier = "tools", "External tools", "tools", "release"
    fp_note = "an external scanner's own false-positive rate applies; treat every high/critical hit as a lead to verify, not a confirmed vulnerability"

    findings: List[Dict[str, Any]] = []
    counts: Dict[str, Any] = {}
    any_ran = False
    any_findings = False
    any_high = False
    skip_reasons: List[str] = []

    def not_installed(name: str, cmd: List[str], source: str) -> None:
        skip_reasons.append(f"{name}: not installed. Run `{' '.join(cmd)}`. Install from {source}.")

    # pip-audit
    cmd = ["pip-audit", "-f", "json"]
    if which("pip-audit"):
        _rc, out, _err, kind = run_tool(cmd, root, timeout=TOOL_TIMEOUT)
        if kind is None:
            any_ran = True
            try:
                data = json.loads(out) if out.strip() else {}
                deps = data.get("dependencies", data if isinstance(data, list) else [])
                vuln_pkgs = sum(1 for d in deps if d.get("vulns"))
                vuln_count = sum(len(d.get("vulns", [])) for d in deps)
                counts["pip_audit"] = {"vulnerable_packages": vuln_pkgs, "vulnerabilities": vuln_count}
                if vuln_count:
                    any_findings = True
                    findings.append(finding("", 0, f"pip-audit: {vuln_count} vulnerabilities in {vuln_pkgs} packages"))
            except (json.JSONDecodeError, AttributeError):
                pass
    else:
        not_installed("pip-audit", cmd, "https://pypi.org/project/pip-audit/ (PyPA)")

    # bandit
    cmd = ["bandit", "-r", str(root), "-f", "json", "-q"]
    if which("bandit"):
        _rc, out, _err, kind = run_tool(cmd, root, timeout=TOOL_TIMEOUT)
        if kind is None:
            any_ran = True
            try:
                data = json.loads(out) if out.strip() else {}
                by_sev: Dict[str, int] = {}
                for item in data.get("results", []):
                    sev = item.get("issue_severity", "UNKNOWN")
                    by_sev[sev] = by_sev.get(sev, 0) + 1
                counts["bandit"] = dict(sorted(by_sev.items()))
                total = sum(by_sev.values())
                if total:
                    any_findings = True
                    findings.append(finding("", 0, f"bandit: {total} findings"))
                if by_sev.get("HIGH") or by_sev.get("CRITICAL"):
                    any_high = True
            except json.JSONDecodeError:
                pass
    else:
        not_installed("bandit", cmd, "https://pypi.org/project/bandit/ (PyCQA)")

    # semgrep — only with a local config; the default registry configs need network
    semgrep_cfg = config.get("semgrep_config")
    if semgrep_cfg:
        cmd = ["semgrep", "--json", "--config", str(semgrep_cfg)]
        if which("semgrep"):
            _rc, out, _err, kind = run_tool(cmd, root, timeout=TOOL_TIMEOUT)
            if kind is None:
                any_ran = True
                try:
                    data = json.loads(out) if out.strip() else {}
                    results = data.get("results", [])
                    counts["semgrep"] = len(results)
                    if results:
                        any_findings = True
                        findings.append(finding("", 0, f"semgrep: {len(results)} findings"))
                except json.JSONDecodeError:
                    pass
        else:
            not_installed("semgrep", cmd, "https://github.com/semgrep/semgrep (local rules only)")
    else:
        skip_reasons.append("semgrep: skipped, no semgrep_config set (the default registry config needs network access)")

    # osv-scanner
    cmd = ["osv-scanner", "--format", "json", "-r", str(root)]
    if which("osv-scanner"):
        _rc, out, _err, kind = run_tool(cmd, root, timeout=TOOL_TIMEOUT)
        if kind is None:
            any_ran = True
            try:
                data = json.loads(out) if out.strip() else {}
                vuln_count = sum(len(pkg.get("vulnerabilities", [])) for r in data.get("results", []) for pkg in r.get("packages", []))
                counts["osv_scanner"] = {"vulnerabilities": vuln_count}
                if vuln_count:
                    any_findings = True
                    findings.append(finding("", 0, f"osv-scanner: {vuln_count} vulnerabilities"))
            except json.JSONDecodeError:
                pass
    else:
        not_installed("osv-scanner", cmd, "https://github.com/google/osv-scanner (Google)")

    # trivy
    cmd = ["trivy", "fs", "--format", "json", str(root)]
    if which("trivy"):
        _rc, out, _err, kind = run_tool(cmd, root, timeout=TOOL_TIMEOUT)
        if kind is None:
            any_ran = True
            try:
                data = json.loads(out) if out.strip() else {}
                by_sev = {}
                for result in data.get("Results", []) or []:
                    for v in result.get("Vulnerabilities", []) or []:
                        sev = v.get("Severity", "UNKNOWN")
                        by_sev[sev] = by_sev.get(sev, 0) + 1
                counts["trivy"] = dict(sorted(by_sev.items()))
                total = sum(by_sev.values())
                if total:
                    any_findings = True
                    findings.append(finding("", 0, f"trivy: {total} findings"))
                if by_sev.get("HIGH") or by_sev.get("CRITICAL"):
                    any_high = True
            except json.JSONDecodeError:
                pass
    else:
        not_installed("trivy", cmd, "https://github.com/aquasecurity/trivy (Aqua)")

    # npm audit — only when there is a lockfile
    if (root / "package-lock.json").exists():
        cmd = ["npm", "audit", "--json"]
        if which("npm"):
            _rc, out, _err, kind = run_tool(cmd, root, timeout=TOOL_TIMEOUT)
            if kind is None:
                any_ran = True
                try:
                    data = json.loads(out) if out.strip() else {}
                    by_sev = data.get("metadata", {}).get("vulnerabilities", {})
                    counts["npm_audit"] = by_sev
                    total = sum(v for k, v in by_sev.items() if k != "total" and isinstance(v, int))
                    if total:
                        any_findings = True
                        findings.append(finding("", 0, f"npm audit: {total} findings"))
                    if by_sev.get("high") or by_sev.get("critical"):
                        any_high = True
                except json.JSONDecodeError:
                    pass
        else:
            not_installed("npm", cmd, "https://docs.npmjs.com/cli/v10/commands/npm-audit")

    # lychee — network tool, run only if the operator already installed it
    docs_dirs = config.get("docs_dirs") or ["docs", "README.md"]
    cmd = ["lychee", "--format", "json"] + docs_dirs
    if which("lychee"):
        _rc, out, _err, kind = run_tool(cmd, root, timeout=TOOL_TIMEOUT)
        if kind is None:
            any_ran = True
            try:
                data = json.loads(out) if out.strip() else {}
                error_map = data.get("error_map", {})
                count = sum(len(v) for v in error_map.values()) if isinstance(error_map, dict) else 0
                counts["lychee"] = {"broken_links": count}
                if count:
                    any_findings = True
                    findings.append(finding("", 0, f"lychee: {count} broken links"))
            except json.JSONDecodeError:
                pass
    else:
        not_installed("lychee", cmd, "https://github.com/lycheeverse/lychee (this tool makes network requests; run it deliberately)")

    if any_high:
        status = "fail"
    elif any_findings:
        status = "review"
    elif any_ran:
        status = "pass"
    else:
        status = "skip"
    reason = "; ".join(skip_reasons) if status == "skip" else ""
    command = ("pip-audit -f json && bandit -r <root> -f json -q && osv-scanner --format json -r <root> && "
               "trivy fs --format json <root> && npm audit --json (if package-lock.json) && "
               "lychee --format json <docs> (network!)")
    return make_check(cid, title, group, tier, status=status, reason=reason, findings=findings,
                       counts=counts, fp_note=fp_note, command=command)


# --------------------------------------------------------------------------- registry, config, driver


def build_registry() -> List[Tuple[str, str, str, str, Any]]:
    """(id, title, group, tier, function) in the fixed, observed-impact order."""
    return [
        ("archive-hygiene", "Archive hygiene", "secrets", "precommit", check_archive_hygiene),
        ("history-secrets", "Secrets in git history", "secrets", "release", check_history_secrets),
        ("credential-patterns", "Credential pattern scan", "secrets", "precommit", check_credential_patterns),
        ("identifier-shapes", "Identifier shape scan", "secrets", "precommit", check_identifier_shapes),
        ("config-endpoint-secrets", "Config/settings endpoint secret exposure", "secrets", "release", check_config_endpoint_secrets),
        ("redaction-at-publish", "Redaction at stream publish", "debug-surfaces", "release", check_redaction_at_publish),
        ("debug-endpoint-exposure", "Debug endpoint exposure", "debug-surfaces", "release", check_debug_endpoint_exposure),
        ("html-sinks", "HTML sinks", "debug-surfaces", "precommit", check_html_sinks),
        ("async-terminal-states", "Async terminal states", "correctness", "release", check_async_terminal_states),
        ("vendor-mode-probes", "Vendor mode probes", "correctness", "release", check_vendor_mode_probes),
        ("idempotency-keys", "Idempotency keys", "correctness", "release", check_idempotency_keys),
        ("client-supplied-money", "Client-supplied money values", "correctness", "release", check_client_supplied_money),
        ("skipped-credentialed-tiers", "Skipped credentialed test tiers", "ci-supply-chain", "release", check_skipped_credentialed_tiers),
        ("contract-artifact-drift", "Contract artifact drift", "ci-supply-chain", "release", check_contract_artifact_drift),
        ("action-pinning", "GitHub Actions pinning", "ci-supply-chain", "release", check_action_pinning),
        ("runtime-version-drift", "Runtime version drift", "ci-supply-chain", "release", check_runtime_version_drift),
        ("docs-endpoint-drift", "Docs/endpoint drift", "docs", "release", check_docs_endpoint_drift),
        ("dos-surface", "DoS surface", "dos", "release", check_dos_surface),
        ("tools", "External tools", "tools", "release", check_tools),
    ]


def load_config(path: Optional[Path]) -> Dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def run_checks(
    root: Path, tier: str, config: Dict[str, Any],
    only: Optional[List[str]], archive: Optional[str], history: int,
) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {"archive": archive, "history": history, "is_git": is_git_repo(root)}
    disabled = set(config.get("disable") or [])
    checks_out: List[Dict[str, Any]] = []

    for cid, ctitle, group, ctier, fn in build_registry():
        if only is not None:
            if cid not in only:
                continue
        elif tier == "precommit" and ctier != "precommit":
            continue
        if cid in disabled:
            checks_out.append(make_check(cid, ctitle, group, ctier, status="skip", reason="disabled via config"))
            continue
        checks_out.append(fn(root, config, ctx))

    summary = {"pass": 0, "fail": 0, "review": 0, "skip": 0}
    for c in checks_out:
        summary[c["status"]] = summary.get(c["status"], 0) + 1

    return {"root": str(root), "tier": tier, "checks": checks_out, "summary": summary}


# --------------------------------------------------------------------------- rendering


def render_markdown(report: Dict[str, Any]) -> str:
    s = report["summary"]
    out = [
        f"# Production readiness — tier: {report['tier']} — pass {s['pass']} · fail {s['fail']} · review {s['review']} · skip {s['skip']}",
        "",
        "| id | status | findings | note |",
        "|---|---|---:|---|",
    ]
    for c in report["checks"]:
        note = c["reason"] or c["title"]
        if len(note) > 80:
            note = note[:79] + "…"
        out.append(f"| {c['id']} | {c['status']} | {len(c['findings'])} | {note} |")
    out.append("")

    for c in report["checks"]:
        if c["status"] not in ("fail", "review"):
            continue
        out.append(f"## {c['id']} ({c['status']})")
        shown = c["findings"][:5]
        for f in shown:
            loc = f"{f['path']}:{f['line']}" if f["path"] else "-"
            out.append(f"- {loc} — {f['note']}")
        remaining = len(c["findings"]) - len(shown)
        if remaining > 0:
            out.append(f"- {remaining} more")
        out.append("")

    skips = [c for c in report["checks"] if c["status"] == "skip"]
    if skips:
        out.append("## skipped")
        for c in skips:
            out.append(f"- {c['id']}: {c['reason']}")
        out.append("")

    text = "\n".join(out).rstrip("\n") + "\n"
    lines = text.splitlines()
    if len(lines) > 150:
        total = len(lines)
        lines = lines[:149] + [f"… output truncated ({total} lines total; see --json or the report file)"]
        text = "\n".join(lines) + "\n"
    return text


# --------------------------------------------------------------------------- CLI


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(prog="readiness.py", description="Deterministic production-readiness scanner.")
    parser.add_argument("root", nargs="?", default=".", help="Repository root to scan (default: .)")
    parser.add_argument("--tier", choices=["precommit", "release"], default="release",
                         help="precommit runs the fast subset (archive-hygiene, credential-patterns, identifier-shapes, html-sinks); release runs everything")
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON instead of markdown; nothing is written to disk")
    parser.add_argument("--config", default=None, help="Path to .readiness.json (default: ROOT/.readiness.json)")
    parser.add_argument("--out", default=None, help="Directory for report.json (default: ROOT/.readiness)")
    parser.add_argument("--archive", default=None, help="Inspect this tar or zip instead of running git archive")
    parser.add_argument("--history", type=int, default=0, help="Also grep the last N commits for credential_patterns")
    parser.add_argument("--only", default=None, help="Comma-separated check ids to run, ignoring --tier")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"readiness: {root} is not a directory", file=sys.stderr)
        return 2

    config_path = Path(args.config) if args.config else root / ".readiness.json"
    config = load_config(config_path)
    only = [c.strip() for c in args.only.split(",") if c.strip()] if args.only else None

    report = run_checks(root, args.tier, config, only, args.archive, args.history)

    if args.json:
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    else:
        out_dir = Path(args.out) if args.out else root / ".readiness"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        sys.stdout.write(render_markdown(report))

    return 1 if report["summary"]["fail"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
