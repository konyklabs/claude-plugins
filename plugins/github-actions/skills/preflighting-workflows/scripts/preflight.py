#!/usr/bin/env python3
"""Deterministic preflight for GitHub Actions workflows: actionlint and
zizmor on PATH reduced to counts, plus structural checks for the pinning,
permissions, pull_request_target and injection traps neither tool always
catches. Prints a bounded table: one row per external tool, one row per
structural rule, `path:line rule` findings only.

Usage:
    python3 preflight.py [REPO_ROOT] [--json] [--allow-unpinned PREFIX ...] [--org OWNER]

Exit codes:
    0  no blocking finding
    2  at least one blocking finding (or an external tool that ran and failed)
    1  REPO_ROOT has no .github/workflows directory

Rules this script keeps:
- Installs nothing, opens no sockets (a `git remote get-url origin` to guess
  --org is a local read, not a network call). A tool that is missing, errors
  or times out is a `skip` row with the reason, never a pass.
- Prints no workflow text beyond `path:line rule`. Paths and rule ids are
  sanitized and length-capped because they come from the scanned repository.
- No PyYAML: a line-oriented scanner tracks indentation, blanks quoted
  strings and comments before matching, and treats `run: |` / `run: >`
  block scalars as one block. Expressions inside `${{ ... }}` are pattern
  matched, never evaluated.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_TIMEOUT_S = int(os.environ.get("PREFLIGHT_TOOL_TIMEOUT", "120"))
MAX_FINDINGS = 20
DEFAULT_ALLOW_UNPINNED = ["konyklabs/.github/"]

_PATH_OK = re.compile(r"[^A-Za-z0-9._/\-]")
_PATH_MAX = 200
_IDENT_OK = re.compile(r"[^A-Za-z0-9_.\-/]")
_IDENT_MAX = 60
_SHA40 = re.compile(r"^[0-9a-fA-F]{40}$")

_DEPLOY_WORDS = ("deploy", "release", "apply", "publish")

_INJECTION_SUFFIXES = (".title", ".body", ".message", ".ref", ".label", ".name", ".email", ".page_name", ".head_branch", ".default_branch")

_EXPR = re.compile(r"\$\{\{(.*?)\}\}")


def sanitize_path(name: object) -> str:
    out = _PATH_OK.sub("?", str(name))
    return out if len(out) <= _PATH_MAX else out[: _PATH_MAX - 1] + "…"


def sanitize_ident(name: object) -> str:
    out = _IDENT_OK.sub("?", str(name))
    return out if len(out) <= _IDENT_MAX else out[: _IDENT_MAX - 1] + "…"


# --------------------------------------------------------------------------
# Line-oriented YAML-ish scanner. Not a YAML parser: enough structure to
# find keys, list items and block scalars in a GitHub Actions workflow by
# indentation, with quoted strings and comments blanked before matching.
# --------------------------------------------------------------------------


class Line:
    __slots__ = ("no", "indent", "text", "comment")

    def __init__(self, no: int, indent: int, text: str, comment: str):
        self.no = no
        self.indent = indent
        self.text = text
        self.comment = comment


def _mask_quotes(s: str) -> str:
    """Same length, quoted spans replaced with `q`, so a `#` or `:` found in
    the mask at a given index is real syntax, not text inside a string."""
    out = list(s)
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch in "'\"":
            j = i + 1
            while j < n and s[j] != ch:
                if ch == '"' and s[j] == "\\":
                    j += 1
                j += 1
            for k in range(i, min(j + 1, n)):
                out[k] = "q"
            i = j + 1
        else:
            i += 1
    return "".join(out)


def tokenize(text: str) -> list:
    """Significant (non-blank, non-comment-only) lines with indentation,
    trailing comments split off, and quoted content left intact in `.text`
    (only the mask is used to find where syntax lives)."""
    lines = []
    for no, raw in enumerate(text.splitlines(), 1):
        stripped = raw.lstrip(" ")
        indent = len(raw) - len(stripped)
        body = stripped.rstrip("\n")
        if body.strip() == "" or body.lstrip().startswith("#"):
            continue
        mask = _mask_quotes(body)
        c = None
        for m in re.finditer(r"#", mask):
            pos = m.start()
            if pos == 0 or mask[pos - 1] in " \t":
                c = pos
                break
        if c is None:
            content, comment = body.rstrip(), ""
        else:
            content, comment = body[:c].rstrip(), body[c:].rstrip()
        if content == "":
            continue
        lines.append(Line(no, indent, content, comment))
    return lines


_KEY_RE = re.compile(r"""^(?:-\s+)?(['"]?)([A-Za-z0-9_.\-]+)\1\s*:\s*(.*)$""")
_BLOCK_IND = re.compile(r"^[|>][+\-]?\d*$")


def is_list_item(ln: Line) -> bool:
    return ln.text == "-" or ln.text.startswith("- ")


def key_of(ln: Line):
    """(key, value_text, value_col) for a `key: value` line, a list item
    `- key: value` (value_col is where the item's own mapping starts), or
    None when the line is not a key line at all (a bare list scalar)."""
    text = ln.text
    prefix = 0
    if is_list_item(ln):
        prefix = 2 if text.startswith("- ") else 1
        text = text[prefix:]
        if text.strip() == "":
            return None
    m = _KEY_RE.match(text)
    if not m:
        return None
    return m.group(2), m.group(3).strip(), ln.indent + prefix


def item_col(ln: Line) -> int:
    """The column a list item's own mapping keys sit at."""
    return ln.indent + (2 if ln.text.startswith("- ") else 1)


def block_scalar_lines(all_lines: list, start_no: int, key_indent: int):
    """Raw (lineno, text) pairs making up a `|`/`>` block scalar that began
    on start_no, scanning the *original* source (comments/quotes inside a
    shell block are not YAML syntax and are never stripped)."""
    out = []
    i = start_no  # 0-based index of the line *after* the key line
    n = len(all_lines)
    while i < n:
        raw = all_lines[i]
        if raw.strip() == "":
            out.append((i + 1, raw))
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent <= key_indent:
            break
        out.append((i + 1, raw))
        i += 1
    return out


class Node:
    __slots__ = ("line", "key", "kind", "scalar", "comment", "children", "col")

    def __init__(self, line: int, key, kind: str, scalar: str = "", comment: str = "", col: int = 0):
        self.line = line
        self.key = key
        self.kind = kind  # 'map' | 'list' | 'scalar' | 'block'
        self.scalar = scalar
        self.comment = comment
        self.children = []  # list[Node] for 'map'/'list'
        self.col = col

    def get(self, key):
        for c in self.children:
            if self.kind == "map" and c.key == key:
                return c
        return None

    def find_all(self, key):
        out = []
        if self.key == key:
            out.append(self)
        for c in self.children:
            if isinstance(c, Node):
                out.extend(c.find_all(key))
        return out


def build_map(sig: list, i: int, col: int, all_lines: list):
    """Parse sibling mapping keys at exactly `col` starting at sig[i].
    Returns (list[Node], next_i)."""
    nodes = []
    n = len(sig)
    while i < n and sig[i].indent == col and not is_list_item(sig[i]):
        ln = sig[i]
        parsed = key_of(ln)
        if parsed is None:
            i += 1
            continue
        key, value, _ = parsed
        if value == "":
            # Nested block on following more-indented lines, or genuinely empty.
            j = i + 1
            if j < n and sig[j].indent > col:
                child_col = sig[j].indent
                if is_list_item(sig[j]):
                    children, j = build_list(sig, j, child_col, all_lines)
                    node = Node(ln.no, key, "list", col=col)
                    node.children = children
                else:
                    children, j = build_map(sig, j, child_col, all_lines)
                    node = Node(ln.no, key, "map", col=col)
                    node.children = children
                nodes.append(node)
                i = j
            else:
                nodes.append(Node(ln.no, key, "scalar", "", ln.comment, col))
                i += 1
        elif _BLOCK_IND.match(value):
            block = block_scalar_lines(all_lines, ln.no, ln.indent)
            node = Node(ln.no, key, "block", col=col)
            node.scalar = "\n".join(t for _, t in block)
            node.children = block  # list[(lineno, raw text)]
            nodes.append(node)
            # Advance i past sig-lines swallowed by the block.
            i += 1
            while i < n and sig[i].no <= (block[-1][0] if block else ln.no):
                i += 1
        else:
            nodes.append(Node(ln.no, key, "scalar", value, ln.comment, col))
            i += 1
    return nodes, i


def build_list(sig: list, i: int, col: int, all_lines: list):
    """Parse sibling list items at exactly `col`. Returns (list[Node], next_i)."""
    nodes = []
    n = len(sig)
    while i < n and sig[i].indent == col and is_list_item(sig[i]):
        ln = sig[i]
        inner_col = item_col(ln)
        parsed = key_of(ln)
        j = i + 1
        first_children = []
        if parsed is None:
            # A bare scalar list entry (`- push`, `- pull_request_target`),
            # not a `key: value` item: the item IS the scalar, no sub-map.
            bare = ln.text[2:] if ln.text.startswith("- ") else ln.text[1:]
            item = Node(ln.no, None, "scalar", bare.strip(), ln.comment, col)
            nodes.append(item)
            i = j
            continue
        item = Node(ln.no, None, "map", col=col)
        if parsed is not None:
            key, value, _ = parsed
            if value == "":
                k = j
                if k < n and sig[k].indent > inner_col:
                    gc_col = sig[k].indent
                    if is_list_item(sig[k]):
                        gchildren, k = build_list(sig, k, gc_col, all_lines)
                        sub = Node(ln.no, key, "list", col=inner_col)
                    else:
                        gchildren, k = build_map(sig, k, gc_col, all_lines)
                        sub = Node(ln.no, key, "map", col=inner_col)
                    sub.children = gchildren
                    first_children.append(sub)
                    j = k
                else:
                    first_children.append(Node(ln.no, key, "scalar", "", ln.comment, inner_col))
            elif _BLOCK_IND.match(value):
                block = block_scalar_lines(all_lines, ln.no, ln.indent)
                sub = Node(ln.no, key, "block", col=inner_col)
                sub.scalar = "\n".join(t for _, t in block)
                sub.children = block
                first_children.append(sub)
                while j < n and sig[j].no <= (block[-1][0] if block else ln.no):
                    j += 1
            else:
                first_children.append(Node(ln.no, key, "scalar", value, ln.comment, inner_col))
        rest, j = build_map(sig, j, inner_col, all_lines)
        item.children = first_children + rest
        nodes.append(item)
        i = j
    return nodes, i


def parse_workflow(text: str) -> Node:
    all_lines = text.splitlines()
    sig = tokenize(text)
    children, _ = build_map(sig, 0, 0, all_lines)
    root = Node(1, None, "map")
    root.children = children
    return root


# --------------------------------------------------------------------------
# Structural rules
# --------------------------------------------------------------------------


class Finding:
    __slots__ = ("path", "line", "rule", "severity")

    def __init__(self, path: str, line: int, rule: str, severity: str):
        self.path = sanitize_path(path)
        self.line = int(line)
        self.rule = sanitize_ident(rule)
        self.severity = severity

    def key(self):
        return (self.path, self.line, self.rule)


def top(root: Node, *names):
    for c in root.children:
        if c.key in names:
            return c
    return None


def on_has(root: Node, name: str):
    """Line of `name` inside the `on:` block -- as a mapping key, a block
    list item, or a flow list / bare scalar written inline -- or None."""
    node = top(root, "on", '"on"', "'on'")
    if node is None:
        return None
    if node.kind == "list":
        for item in node.children:
            if item.kind == "scalar" and item.scalar.strip("'\"") == name:
                return item.line
            if item.key == name:
                return item.line
    elif node.kind == "map":
        got = node.get(name)
        if got is not None:
            return got.line
    elif node.kind == "scalar":
        value = node.scalar.strip()
        if value.startswith("[") and value.endswith("]"):
            parts = [p.strip().strip("'\"") for p in value[1:-1].split(",")]
            if name in parts:
                return node.line
        elif value.strip("'\"") == name:
            return node.line
    return None


def all_uses(root: Node):
    """(Node,) for every `uses:` scalar anywhere in the tree."""
    return root.find_all("uses")


def jobs_node(root: Node):
    j = top(root, "jobs")
    return j


def job_nodes(root: Node):
    j = jobs_node(root)
    if j is None or j.kind != "map":
        return []
    return j.children  # each child is a Node(kind='map', key=job_id)


def steps_of(job: Node):
    st = job.get("steps")
    if st is None or st.kind != "list":
        return []
    return st.children


def with_script_or_run_blocks(step: Node):
    """List of (start_line, [(lineno, text)]) blocks to scan for injection:
    the `run:` value (block or inline) and a `with: script:` value."""
    out = []
    run = step.get("run")
    if run is not None:
        if run.kind == "block":
            out.append(run.children)
        elif run.kind == "scalar":
            out.append([(run.line, run.scalar)])
    withn = step.get("with")
    if withn is not None and withn.kind == "map":
        script = withn.get("script")
        if script is not None:
            if script.kind == "block":
                out.append(script.children)
            elif script.kind == "scalar":
                out.append([(script.line, script.scalar)])
    return out


def check_uses_pinning(root: Node, allow_unpinned: list, path: str) -> list:
    findings = []
    for node in all_uses(root):
        value = node.scalar
        if not value:
            continue
        if any(value.startswith(p) for p in allow_unpinned):
            continue
        if value.startswith("./") or value.startswith(".\\"):
            continue
        if value.startswith("docker://"):
            if "@sha256:" not in value:
                findings.append(Finding(path, node.line, "uses-unpinned", "blocking"))
            continue
        if "@" not in value:
            findings.append(Finding(path, node.line, "uses-unpinned", "blocking"))
            continue
        ref = value.rsplit("@", 1)[1]
        if not _SHA40.match(ref):
            findings.append(Finding(path, node.line, "uses-unpinned", "blocking"))
        else:
            if not node.comment.strip():
                findings.append(Finding(path, node.line, "uses-version-comment-missing", "minor"))
    return findings


def check_permissions_missing(root: Node, path: str) -> list:
    if top(root, "permissions") is not None:
        return []
    findings = []
    for job in job_nodes(root):
        if job.get("permissions") is None:
            findings.append(Finding(path, job.line, "permissions-missing", "minor"))
    return findings


def _is_write_all(node: Node) -> bool:
    return node.kind == "scalar" and node.scalar.strip("'\"") == "write-all"


def check_permissions_write_all(root: Node, path: str) -> list:
    findings = []
    wf = top(root, "permissions")
    if wf is not None and _is_write_all(wf):
        findings.append(Finding(path, wf.line, "permissions-write-all", "blocking"))
    for job in job_nodes(root):
        p = job.get("permissions")
        if p is not None and _is_write_all(p):
            findings.append(Finding(path, p.line, "permissions-write-all", "blocking"))
    return findings


def check_pull_request_target_checkout(root: Node, path: str) -> list:
    prt_line = on_has(root, "pull_request_target")
    if prt_line is None:
        return []
    findings = []
    for job in job_nodes(root):
        for step in steps_of(job):
            uses = step.get("uses")
            if uses is None or "actions/checkout" not in (uses.scalar or ""):
                continue
            withn = step.get("with")
            if withn is None:
                continue
            ref = withn.get("ref")
            if ref is None or ref.kind != "scalar":
                continue
            if "github.event.pull_request" in ref.scalar or "github.head_ref" in ref.scalar:
                findings.append(Finding(path, ref.line, "pull-request-target-checkout", "blocking"))
    return findings


def _expr_paths(text: str):
    for m in _EXPR.finditer(text):
        yield m.group(1).strip()


def check_expression_injection(root: Node, path: str) -> list:
    findings = []
    for job in job_nodes(root):
        for step in steps_of(job):
            for block in with_script_or_run_blocks(step):
                for lineno, text in block:
                    for expr in _expr_paths(text):
                        # tolerate `expr || 'default'` etc.: look at the first
                        # bare identifier path only, never evaluate it.
                        head = re.split(r"\s", expr, maxsplit=1)[0]
                        rule = None
                        sev = None
                        # The narrower inputs.* carve-out is checked first: a
                        # workflow_dispatch input named e.g. `message` would
                        # otherwise also match the `.message` suffix below
                        # and get classified as the more severe rule.
                        if head.startswith("github.event.inputs.") or head.startswith("inputs."):
                            rule, sev = "inputs-in-run", "minor"
                        elif head == "github.head_ref":
                            rule, sev = "expression-injection", "blocking"
                        elif head.startswith("github.event.") and head.endswith(_INJECTION_SUFFIXES):
                            rule, sev = "expression-injection", "blocking"
                        elif ".head_commit." in head or ".commits" in head:
                            rule, sev = "expression-injection", "blocking"
                        if rule:
                            findings.append(Finding(path, lineno, rule, sev))
    return findings


def check_timeout_missing(root: Node, path: str) -> list:
    findings = []
    for job in job_nodes(root):
        if job.get("uses") is not None:
            # a job that calls a reusable workflow has no steps and accepts no
            # `timeout-minutes`; the callee's jobs carry their own
            continue
        if job.get("timeout-minutes") is None:
            findings.append(Finding(path, job.line, "timeout-missing", "minor"))
    return findings


def _job_deploy_ish(job: Node) -> bool:
    words = [job.key or ""]
    name = job.get("name")
    if name is not None and name.kind == "scalar":
        words.append(name.scalar)
    text = " ".join(words).lower()
    return any(w in text for w in _DEPLOY_WORDS)


def check_concurrency_missing_on_deploy(root: Node, path: str) -> list:
    wf_conc = top(root, "concurrency") is not None
    findings = []
    for job in job_nodes(root):
        if not _job_deploy_ish(job):
            continue
        if wf_conc or job.get("concurrency") is not None:
            continue
        findings.append(Finding(path, job.line, "concurrency-missing-on-deploy", "minor"))
    return findings


def _owner_of(uses_value: str):
    if not uses_value or uses_value.startswith("./"):
        return None
    return uses_value.split("/", 1)[0]


def check_secrets_inherit_external(root: Node, org: object, path: str) -> list:
    findings = []
    skip = False
    for job in job_nodes(root):
        secrets = job.get("secrets")
        if secrets is None or secrets.kind != "scalar" or secrets.scalar.strip("'\"") != "inherit":
            continue
        uses = job.get("uses")
        owner = _owner_of(uses.scalar) if uses is not None else None
        if owner is None:
            continue
        if org is None:
            skip = True
            continue
        if owner != org:
            findings.append(Finding(path, secrets.line, "secrets-inherit-external", "blocking"))
    return findings, skip


def check_schedule_without_concurrency(root: Node, path: str) -> list:
    line = on_has(root, "schedule")
    if line is None:
        return []
    if top(root, "concurrency") is not None:
        return []
    return [Finding(path, line, "schedule-without-concurrency", "minor")]


STRUCTURAL_RULES = {
    "uses-pinning": ("blocking", None),
    "permissions-missing": ("minor", None),
    "permissions-write-all": ("blocking", None),
    "pull-request-target-checkout": ("blocking", None),
    "expression-injection": ("blocking", None),
    "inputs-in-run": ("minor", None),
    "timeout-missing": ("minor", None),
    "concurrency-missing-on-deploy": ("minor", None),
    "secrets-inherit-external": ("blocking", None),
    "schedule-without-concurrency": ("minor", None),
}


def run_structural(root: Node, path: str, allow_unpinned: list, org: object):
    findings = []
    findings += check_uses_pinning(root, allow_unpinned, path)
    findings += check_permissions_missing(root, path)
    findings += check_permissions_write_all(root, path)
    findings += check_pull_request_target_checkout(root, path)
    findings += check_expression_injection(root, path)
    findings += check_timeout_missing(root, path)
    findings += check_concurrency_missing_on_deploy(root, path)
    sie, skip = check_secrets_inherit_external(root, org, path)
    findings += sie
    findings += check_schedule_without_concurrency(root, path)
    return findings, skip


# --------------------------------------------------------------------------
# External tools
# --------------------------------------------------------------------------


def _run_tool(cmd: list, cwd: Path):
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=DEFAULT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None, "", "timeout"
    except OSError as exc:
        return None, "", exc.__class__.__name__
    return p.returncode, p.stdout, p.stderr


def tool_row(check: str, status: str, severity: str, count: int = 0, findings=None, note: str = ""):
    return {"check": check, "status": status, "severity": severity, "count": count, "findings": findings or [], "note": note}


def run_actionlint(root: Path):
    if not shutil.which("actionlint"):
        return tool_row("actionlint", "skip", "blocking", note="not on PATH")
    rc, out, err = _run_tool(["actionlint", "-format", "{{json .}}", "-no-color"], root)
    if rc is None:
        return tool_row("actionlint", "skip", "blocking", note=err or "tool error")
    try:
        doc = json.loads(out) if out.strip() else []
    except ValueError:
        return tool_row("actionlint", "skip", "blocking", note=f"exit {rc}, no JSON")
    if not isinstance(doc, list):
        return tool_row("actionlint", "skip", "blocking", note=f"exit {rc}, unexpected JSON shape")
    findings = []
    for item in doc:
        if not isinstance(item, dict):
            continue
        path = sanitize_path(item.get("filepath", "?"))
        line = int(item.get("line") or 0)
        kind = sanitize_ident(item.get("kind", "issue"))
        findings.append({"path": path, "line": line, "rule": f"actionlint/{kind}"})
    if rc == 0 and not findings:
        return tool_row("actionlint", "pass", "blocking", 0)
    if findings:
        return tool_row("actionlint", "fail", "blocking", len(findings), findings)
    return tool_row("actionlint", "skip", "blocking", note=f"exit {rc}, no parseable findings")


def run_zizmor(root: Path):
    if not shutil.which("zizmor"):
        return tool_row("zizmor", "skip", "blocking", note="not on PATH")
    rc, out, err = _run_tool(["zizmor", "--format", "json", ".github/workflows"], root)
    if rc is None:
        return tool_row("zizmor", "skip", "blocking", note=err or "tool error")
    try:
        doc = json.loads(out) if out.strip() else []
    except ValueError:
        return tool_row("zizmor", "skip", "blocking", note=f"exit {rc}, no JSON")
    if not isinstance(doc, list):
        return tool_row("zizmor", "skip", "blocking", note=f"exit {rc}, unexpected JSON shape (fallback to line count)")
    findings = []
    sev_counts = {}
    fallback = False
    for item in doc:
        if not isinstance(item, dict):
            fallback = True
            continue
        ident = sanitize_ident(item.get("ident", "finding"))
        dets = item.get("determinations") or {}
        sev = sanitize_ident(dets.get("severity", "unknown"), )
        sev_counts[sev] = sev_counts.get(sev, 0) + 1
        line = 0
        path = "?"
        for loc in item.get("locations") or []:
            sym = (loc or {}).get("symbolic") or {}
            conc = (loc or {}).get("concrete") or {}
            if "key_path" in sym:
                path = sym.get("key_path") or path
            start = ((conc.get("location") or {}).get("start_point") or {})
            if "row" in start:
                try:
                    line = int(start["row"]) + 1
                except (TypeError, ValueError):
                    pass
            if "path" in conc:
                path = conc.get("path") or path
            break
        findings.append({"path": sanitize_path(path), "line": line, "rule": f"zizmor/{ident}"})
    note = "counts by severity: " + ", ".join(f"{k}={v}" for k, v in sorted(sev_counts.items())) if sev_counts else ""
    if fallback:
        note = (note + "; " if note else "") + "output shape differed from the documented schema; counted lines only"
    if rc == 0 and not findings:
        return tool_row("zizmor", "pass", "blocking", 0, note=note)
    if findings or fallback:
        cnt = len(findings) if findings else len(out.splitlines())
        return tool_row("zizmor", "fail", "blocking", cnt, findings, note=note)
    return tool_row("zizmor", "skip", "blocking", note=f"exit {rc}, no parseable findings")


# --------------------------------------------------------------------------
# Org detection
# --------------------------------------------------------------------------


def detect_org(root: Path):
    try:
        p = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(root), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    m = re.search(r"github\.com[:/]+([^/]+)/", p.stdout.strip())
    return m.group(1) if m else None


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def render(rows: list) -> str:
    lines = [f"{'check':34} {'status':6} {'count':5} findings"]
    for r in rows:
        head = f"{r['check']:34} {r['status']:6} {r['count']:<5}"
        findings = r["findings"]
        shown = [f"{fx['path']}:{fx['line']} {fx['rule']}" for fx in findings[:MAX_FINDINGS]]
        extra = len(findings) - MAX_FINDINGS
        detail = ", ".join(shown)
        if extra > 0:
            detail = (detail + f", …+{extra}") if detail else f"…+{extra}"
        if not detail and r["status"] == "skip":
            detail = r.get("note", "")
        lines.append((head + detail).rstrip())
    blocking = sum(r["count"] for r in rows if r["severity"] == "blocking" and r["status"] == "fail")
    minor = sum(r["count"] for r in rows if r["severity"] == "minor" and r["status"] == "fail")
    skipped = sum(1 for r in rows if r["status"] == "skip")
    lines.append("")
    lines.append(f"preflight: {blocking} blocking, {minor} minor, {skipped} skipped")
    if blocking > 0:
        lines.append("exit 2: at least one blocking finding")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--allow-unpinned", nargs="*", default=None)
    ap.add_argument("--org", default=None)
    args = ap.parse_args(argv)

    root = Path(args.root)
    wf_dir = root / ".github" / "workflows"
    if not wf_dir.is_dir():
        print("preflight: no .github/workflows directory", file=sys.stderr)
        return 1
    files = sorted(list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml")))
    if not files:
        print("preflight: no .github/workflows directory", file=sys.stderr)
        return 1

    allow_unpinned = args.allow_unpinned if args.allow_unpinned is not None else list(DEFAULT_ALLOW_UNPINNED)
    org = args.org if args.org is not None else detect_org(root)

    all_findings = []
    any_skip_needs_org = False
    for f in files:
        rel = f.relative_to(root).as_posix()
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        try:
            tree = parse_workflow(text)
        except Exception:
            continue
        findings, skip = run_structural(tree, rel, allow_unpinned, org)
        all_findings.extend(findings)
        any_skip_needs_org = any_skip_needs_org or skip

    rows = [run_actionlint(root), run_zizmor(root)]

    for rule, (sev, _unused) in STRUCTURAL_RULES.items():
        if rule == "uses-pinning":
            fs = [x for x in all_findings if x.rule in ("uses-unpinned", "uses-version-comment-missing")]
            blocking_fs = [x for x in fs if x.rule == "uses-unpinned"]
            minor_fs = [x for x in fs if x.rule == "uses-version-comment-missing"]
            for sub_rule, sub_sev, sub_fs in (("uses-unpinned", "blocking", blocking_fs), ("uses-version-comment-missing", "minor", minor_fs)):
                status = "fail" if sub_fs else "pass"
                rows.append({"check": sub_rule, "status": status, "severity": sub_sev, "count": len(sub_fs),
                             "findings": [{"path": x.path, "line": x.line, "rule": x.rule} for x in sub_fs], "note": ""})
            continue
        if rule == "secrets-inherit-external" and org is None and any_skip_needs_org:
            rows.append({"check": rule, "status": "skip", "severity": sev, "count": 0, "findings": [], "note": "needs --org (git remote get-url origin did not resolve one)"})
            continue
        fs = [x for x in all_findings if x.rule == rule]
        status = "fail" if fs else "pass"
        rows.append({"check": rule, "status": status, "severity": sev, "count": len(fs),
                     "findings": [{"path": x.path, "line": x.line, "rule": x.rule} for x in fs], "note": ""})

    ok = not any(r["status"] == "fail" and r["severity"] == "blocking" for r in rows)
    if args.json:
        print(json.dumps({"ok": ok, "root": str(root), "rows": rows}, ensure_ascii=False))
    else:
        print(render(rows))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
