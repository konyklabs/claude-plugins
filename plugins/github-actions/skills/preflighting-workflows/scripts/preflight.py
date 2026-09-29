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
    2  at least one blocking finding (or an external tool that ran and failed,
       or a workflow line the scanner could not place: the `parse` row)
    1  REPO_ROOT has no .github/workflows directory

Rules this script keeps:
- Installs nothing, opens no sockets (a `git remote get-url origin` to guess
  --org is a local read, not a network call). A tool that is missing, errors
  or times out is a `skip` row with the reason, never a pass.
- Prints no workflow text beyond `path:line rule`. Paths and rule ids are
  sanitized and length-capped because they come from the scanned repository.
- No PyYAML: a line-oriented scanner tracks indentation, blanks quoted
  strings and comments before matching, treats `run: |` / `run: >` block
  scalars as one block, reads indentless sequences, wrapped plain scalars
  and small flow collections, and strips quotes before a rule reads a
  value. It fails closed: a line no node consumed is a `parse-incomplete`
  row, a file it cannot parse (too deep, or any exception) a `parse-error`
  row, both blocking. Expressions inside `${{ ... }}` are pattern matched,
  never evaluated.
- --json carries the same 20-per-rule cap as the table (plus a `truncated`
  count) and `root` as a basename only.
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

# Per external tool: actionlint and zizmor finish a large repository in
# seconds; 120 s means a hung tool, and a hung tool is a skip, not a wait.
DEFAULT_TIMEOUT_S = int(os.environ.get("PREFLIGHT_TOOL_TIMEOUT", "120"))
# Rows shown per rule, in text and in --json: enough to act on, small
# enough that a hostile repository cannot flood the reading model's context.
MAX_FINDINGS = 20
# The org's own reusable workflows are called at @main on purpose (one
# owner, reviewed on its own default branch); nothing else is exempt.
DEFAULT_ALLOW_UNPINNED = ["konyklabs/.github/"]

# Characters a workflow path may keep in a report; anything else becomes `?`.
_PATH_OK = re.compile(r"[^A-Za-z0-9._/\-]")
# Longer than any real `.github/workflows/<name>.yml`, short enough to cap a
# hostile file name.
_PATH_MAX = 200
# Characters a rule or tool-kind id may keep in a report.
_IDENT_OK = re.compile(r"[^A-Za-z0-9_.\-/]")
# Longer than any actionlint kind or zizmor audit id seen (about 30).
_IDENT_MAX = 60
# A full git commit SHA-1 is 40 hex digits; anything shorter is a prefix a
# pusher can collide, anything else a movable tag or branch.
_SHA40 = re.compile(r"^[0-9a-fA-F]{40}$")

# Job id or name words that mark a job whose runs must not overlap.
_DEPLOY_WORDS = ("deploy", "release", "apply", "publish")

# Leaf names under github.event.* whose value an outside contributor
# writes: issue/PR/comment/review titles and bodies, commit messages, wiki
# page names, branch names (a fork can name, or rename, a branch anything)
# and author e-mails. `.ref`, `.label` and `.name` are NOT here: most of
# those are repository-controlled; the attacker-controlled ones are named
# explicitly below.
_INJECTION_LEAVES = ("title", "body", "message", "page_name", "head_branch", "default_branch", "email")
# Explicit attacker-controlled paths that do not end in one of the leaves.
_INJECTION_EXACT = ("github.head_ref", "github.event.pull_request.head.ref", "github.event.pull_request.head.label")
# Under head_commit only these are safe: a commit id is 40 hex.
_HEAD_COMMIT_SAFE = ("id", "sha")
# Whole event objects that carry a title, body, message or branch name:
# passed to toJSON() or interpolated whole, they inject as surely as the
# leaf does.
_INJECTION_OBJECTS = (
    "github.event", "github.event.issue", "github.event.pull_request", "github.event.comment",
    "github.event.review", "github.event.review_comment", "github.event.discussion",
    "github.event.discussion_comment", "github.event.head_commit", "github.event.commits",
)


def expressions(text: str):
    """The body of every `${{ ... }}` in text, in one linear pass: find the
    opener, then the next `}}`; with no closer there is no further
    expression on that text. (A regex with a lazy group is quadratic on a
    line of many unclosed openers.)"""
    i = text.find("${{")
    while i >= 0:
        end = text.find("}}", i + 3)
        if end < 0:
            return
        yield text[i + 3:end]
        i = text.find("${{", end + 2)


def strip_expressions(text: str, placeholder: str) -> str:
    """text with every `${{ ... }}` replaced by placeholder, linearly."""
    out = []
    pos = 0
    i = text.find("${{")
    while i >= 0:
        end = text.find("}}", i + 3)
        if end < 0:
            break
        out.append(text[pos:i])
        out.append(placeholder)
        pos = end + 2
        i = text.find("${{", pos)
    out.append(text[pos:])
    return "".join(out)


def sanitize_path(name: object) -> str:
    out = _PATH_OK.sub("?", str(name))
    return out if len(out) <= _PATH_MAX else out[: _PATH_MAX - 1] + "…"


def sanitize_ident(name: object) -> str:
    out = _IDENT_OK.sub("?", str(name))
    return out if len(out) <= _IDENT_MAX else out[: _IDENT_MAX - 1] + "…"


# --------------------------------------------------------------------------
# Line-oriented YAML-ish scanner. Not a YAML parser: enough structure to
# find keys, list items, block scalars and small flow collections in a
# GitHub Actions workflow by indentation. It fails closed: every
# significant line must be consumed by some node, and a line that is not is
# reported as `parse-incomplete` (exit 2), never skipped in silence.
# --------------------------------------------------------------------------

# Nesting cap. A real workflow nests about ten levels (jobs > job > steps >
# item > with > key, or on > event > filter > list); 64 leaves a wide margin
# and stays far below Python's default recursion limit of 1000 even at three
# frames per level, so a hostile file raises ParseError instead of
# RecursionError.
MAX_DEPTH = 64
# Lines one flow collection may span. Real ones fit on one to a few lines;
# each extra line re-reads the joined text, so an uncapped join is quadratic.
MAX_FLOW_LINES = 50


class ParseError(Exception):
    """The file cannot be parsed safely (too deep, or structurally broken)."""


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


# A mapping key: plain or quoted, then `:` followed by whitespace or the end
# of the line (so `docker://img` or `http://x` is a scalar, not a key).
_KEY_RE = re.compile(r"""^(['"]?)([^\s'":\[\]{}#,]+)\1\s*:(?:\s+(.*))?$""")
_BLOCK_IND = re.compile(r"^[|>][+\-]?\d*$")


def is_list_item(ln: Line) -> bool:
    return ln.text == "-" or ln.text.startswith("- ")


def split_key(text: str):
    """(key, value) for `key: value` text, or None when it is not a key."""
    m = _KEY_RE.match(text)
    if not m:
        return None
    return m.group(2), (m.group(3) or "").strip()


def key_of(ln: Line):
    """(key, value) for a `key: value` line, or None (a list item is never a
    key line; its content is split by the list parser)."""
    if is_list_item(ln):
        return None
    return split_key(ln.text)


def unquote(value: str) -> str:
    """Strip one pair of matching outer quotes from a single-line scalar, so a
    rule compares `actions/checkout@<sha>`, never `"actions/checkout@<sha>"`."""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        inner = v[1:-1]
        return inner.replace("''", "'") if v[0] == "'" else inner
    return v


def block_scalar_lines(all_lines: list, start_no: int, base_indent: int):
    """Raw (lineno, text) pairs making up a `|`/`>` block scalar whose key
    line is start_no (1-based) and whose parent mapping sits at base_indent.
    Scans the *original* source: comments and quotes inside a shell block
    are not YAML syntax and are never stripped. Trailing blank lines are not
    part of the block."""
    out = []
    i = start_no  # 0-based index of the line after the key line
    n = len(all_lines)
    while i < n:
        raw = all_lines[i]
        if raw.strip() == "":
            out.append((i + 1, raw))
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent <= base_indent:
            break
        out.append((i + 1, raw))
        i += 1
    while out and out[-1][1].strip() == "":
        out.pop()
    return out


class Node:
    __slots__ = ("line", "key", "kind", "scalar", "comment", "children", "col", "lines")

    def __init__(self, line: int, key, kind: str, scalar: str = "", comment: str = "", col: int = 0):
        self.line = line
        self.key = key
        self.kind = kind  # 'map' | 'list' | 'scalar' | 'block'
        self.scalar = scalar  # unquoted value for a scalar
        self.comment = comment
        self.children = []  # list[Node] for 'map'/'list'
        self.col = col
        self.lines = []  # [(lineno, raw text)] a scalar or block spans

    def get(self, key):
        if self.kind != "map":
            return None
        for c in self.children:
            if c.key == key:
                return c
        return None

    def find_all(self, key):
        out = []
        if self.key == key:
            out.append(self)
        if self.kind in ("map", "list"):
            for c in self.children:
                out.extend(c.find_all(key))
        return out


# ---- flow collections: `[a, b]`, `{k: v, k2: {x: y}}` --------------------


class _FlowError(Exception):
    pass


class _FlowUnterminated(_FlowError):
    pass


def _parse_flow(s: str):
    """A small recursive-descent reader for flow collections. Returns nested
    dict/list/str. `${{ ... }}` is kept whole inside a plain scalar. Raises
    _FlowUnterminated when the text ends inside a collection (the caller may
    join the next line) and _FlowError for anything it does not understand."""
    pos = [0]
    n = len(s)

    def ws():
        while pos[0] < n and s[pos[0]] in " \t":
            pos[0] += 1

    def need_more():
        if pos[0] >= n:
            raise _FlowUnterminated()

    def value(depth, in_key=False):
        if depth > MAX_DEPTH:
            raise ParseError("flow nesting too deep")
        ws()
        need_more()
        ch = s[pos[0]]
        if ch == "[":
            return seq(depth + 1)
        if ch == "{":
            return mapping(depth + 1)
        if ch in "'\"":
            return quoted(ch)
        return plain(in_key)

    def quoted(q):
        start = pos[0]
        pos[0] += 1
        while True:
            need_more()
            c = s[pos[0]]
            if q == '"' and c == "\\":
                pos[0] += 2
                continue
            if c == q:
                if q == "'" and pos[0] + 1 < n and s[pos[0] + 1] == "'":
                    pos[0] += 2
                    continue
                pos[0] += 1
                return unquote(s[start:pos[0]])
            pos[0] += 1

    def plain(in_key):
        start = pos[0]
        while pos[0] < n:
            if s.startswith("${{", pos[0]):
                end = s.find("}}", pos[0] + 3)
                if end < 0:
                    raise _FlowUnterminated()
                pos[0] = end + 2
                continue
            c = s[pos[0]]
            if c in ",[]{}":
                break
            if c == ":" and (pos[0] + 1 >= n or s[pos[0] + 1] in " \t,[]{}"):
                if in_key:
                    break
                raise _FlowError("mapping inside a flow sequence")
            pos[0] += 1
        text = s[start:pos[0]].strip()
        if text == "" and pos[0] < n and s[pos[0]] in "[{":
            raise _FlowError("unexpected collection")
        return text

    def seq(depth):
        pos[0] += 1
        out = []
        while True:
            ws()
            need_more()
            if s[pos[0]] == "]":
                pos[0] += 1
                return out
            out.append(value(depth))
            ws()
            need_more()
            if s[pos[0]] == ",":
                pos[0] += 1
            elif s[pos[0]] != "]":
                raise _FlowError("expected , or ]")

    def mapping(depth):
        pos[0] += 1
        out = {}
        while True:
            ws()
            need_more()
            if s[pos[0]] == "}":
                pos[0] += 1
                return out
            k = value(depth, in_key=True)
            if not isinstance(k, str) or k == "":
                raise _FlowError("non-scalar key")
            ws()
            need_more()
            v = ""
            if s[pos[0]] == ":":
                pos[0] += 1
                ws()
                need_more()
                if s[pos[0]] not in ",}":
                    v = value(depth)
            out[k] = v
            ws()
            need_more()
            if s[pos[0]] == ",":
                pos[0] += 1
            elif s[pos[0]] != "}":
                raise _FlowError("expected , or }")

    result = value(0)
    ws()
    if pos[0] != n:
        raise _FlowError("trailing text after a flow collection")
    return result


def _flow_to_node(obj, key, line: int, comment: str, col: int) -> Node:
    if isinstance(obj, dict):
        node = Node(line, key, "map", col=col)
        node.children = [_flow_to_node(v, k, line, comment, col) for k, v in obj.items()]
    elif isinstance(obj, list):
        node = Node(line, key, "list", col=col)
        node.children = [_flow_to_node(v, None, line, comment, col) for v in obj]
    else:
        node = Node(line, key, "scalar", obj, comment, col)
        node.lines = [(line, obj)]
    return node


# ---- the block parser ------------------------------------------------------


class Parser:
    def __init__(self, text: str):
        self.all_lines = text.splitlines()
        self.sig = tokenize(text)
        self.n = len(self.sig)
        self.consumed = set()  # indexes into sig
        self.bad = []  # line numbers of values read but not understood

    # A line is placed once some node owns it.
    def take(self, i: int):
        self.consumed.add(i)

    def parse(self) -> Node:
        root = Node(1, None, "map")
        root.children, _ = self.build_map(0, 0, 0)
        return root

    def unplaced(self) -> list:
        """Line numbers of significant lines no node consumed, plus values
        that were read but not understood, sorted."""
        out = {self.sig[i].no for i in range(self.n) if i not in self.consumed}
        out.update(self.bad)
        return sorted(out)

    def _check_depth(self, depth: int):
        if depth > MAX_DEPTH:
            raise ParseError("nesting deeper than MAX_DEPTH")

    def build_map(self, i: int, col: int, depth: int):
        """Sibling mapping keys at exactly `col`. Lines more indented than
        `col` that no key claimed are left unconsumed (reported, then
        skipped) so one odd line never hides the rest of the file."""
        self._check_depth(depth)
        nodes = []
        sig = self.sig
        while i < self.n and sig[i].indent >= col:
            ln = sig[i]
            if ln.indent > col or is_list_item(ln):
                i += 1  # stray: stays unconsumed
                continue
            parsed = key_of(ln)
            if parsed is None:
                i += 1  # not a key line: stays unconsumed
                continue
            self.take(i)
            key, value = parsed
            node, i = self.value_node(key, value, ln, col, i + 1, depth)
            nodes.append(node)
        return nodes, i

    def build_list(self, i: int, col: int, depth: int):
        """Sibling list items at exactly `col`. A key line at `col` ends the
        list: that is the indentless-sequence case, where the next key
        belongs to the parent mapping."""
        self._check_depth(depth)
        nodes = []
        sig = self.sig
        while i < self.n and sig[i].indent >= col:
            ln = sig[i]
            if ln.indent > col:
                i += 1  # stray: stays unconsumed
                continue
            if not is_list_item(ln):
                break
            self.take(i)
            rest_raw = ln.text[1:]
            rest = rest_raw.lstrip(" ")
            inner_col = ln.indent + 1 + (len(rest_raw) - len(rest))
            j = i + 1
            if rest == "":
                # `-` alone: the item's content is on the following lines.
                if j < self.n and sig[j].indent > col:
                    item, j = self.block_at(j, depth + 1)
                else:
                    item = Node(ln.no, None, "scalar", "", ln.comment, col)
                nodes.append(item)
                i = j
                continue
            if rest[0] in "[{":
                item, j = self.flow_value(None, rest, ln, col, j, depth + 1)
                nodes.append(item)
                i = j
                continue
            if rest == "-" or rest.startswith("- "):
                # A nested sequence on the dash line is not supported.
                self.bad.append(ln.no)
                nodes.append(Node(ln.no, None, "scalar", "", ln.comment, col))
                i = j
                continue
            parsed = split_key(rest)
            if parsed is None:
                item, j = self.plain_scalar(None, rest, ln, col, j)
                nodes.append(item)
                i = j
                continue
            key, value = parsed
            first, j = self.value_node(key, value, ln, inner_col, j, depth + 1)
            rest_nodes, j = self.build_map(j, inner_col, depth + 1)
            item = Node(ln.no, None, "map", col=col)
            item.children = [first] + rest_nodes
            nodes.append(item)
            i = j
        return nodes, i

    def block_at(self, j: int, depth: int):
        """A nested block starting at sig[j]: a list, a mapping, or a plain
        scalar written on the next line (`uses:` then the value below)."""
        ln = self.sig[j]
        if is_list_item(ln):
            node = Node(ln.no, None, "list", col=ln.indent)
            node.children, j = self.build_list(j, ln.indent, depth)
            return node, j
        if key_of(ln) is not None:
            node = Node(ln.no, None, "map", col=ln.indent)
            node.children, j = self.build_map(j, ln.indent, depth)
            return node, j
        self.take(j)
        text = ln.text
        if text[:1] in "[{":
            return self.flow_value(None, text, ln, ln.indent - 1, j + 1, depth)
        return self.plain_scalar(None, text, ln, ln.indent - 1, j + 1)

    def value_node(self, key, value: str, ln: Line, key_col: int, j: int, depth: int):
        """The node for `key: value` on line ln, whose key sits at key_col;
        j is the next unread sig index. Returns (node, next j)."""
        sig = self.sig
        if value == "":
            if j < self.n and sig[j].indent > key_col:
                node, j = self.block_at(j, depth + 1)
                node.key, node.line, node.col = key, ln.no, key_col
                if node.kind == "scalar" and not node.comment:
                    node.comment = ln.comment
                return node, j
            if j < self.n and sig[j].indent == key_col and is_list_item(sig[j]):
                # Indentless sequence: `steps:` then `- uses:` at the same column.
                node = Node(ln.no, key, "list", col=key_col)
                node.children, j = self.build_list(j, key_col, depth + 1)
                return node, j
            node = Node(ln.no, key, "scalar", "", ln.comment, key_col)
            return node, j
        if _BLOCK_IND.match(value):
            block = block_scalar_lines(self.all_lines, ln.no, key_col)
            node = Node(ln.no, key, "block", col=key_col)
            node.scalar = "\n".join(t for _, t in block)
            node.lines = block
            last = block[-1][0] if block else ln.no
            while j < self.n and sig[j].no <= last:
                self.take(j)
                j += 1
            return node, j
        if value[0] in "[{":
            return self.flow_value(key, value, ln, key_col, j, depth + 1)
        return self.plain_scalar(key, value, ln, key_col, j)

    def plain_scalar(self, key, value: str, ln: Line, key_col: int, j: int):
        """A scalar, with multi-line continuation lines absorbed: lines more
        indented than the key that are neither a key line nor a list item."""
        sig = self.sig
        lines = [(ln.no, value)]
        while j < self.n and sig[j].indent > key_col and not is_list_item(sig[j]) and key_of(sig[j]) is None:
            self.take(j)
            lines.append((sig[j].no, sig[j].text))
            j += 1
        if len(lines) == 1:
            scalar = unquote(value)
        else:
            scalar = " ".join(t.strip() for _, t in lines)
        node = Node(ln.no, key, "scalar", scalar, ln.comment, key_col)
        node.lines = lines
        return node, j

    def flow_value(self, key, value: str, ln: Line, key_col: int, j: int, depth: int):
        """A flow collection, joined across following more-indented lines
        until it closes. Anything it cannot read is a `bad` line (reported
        as parse-incomplete), kept as an opaque scalar."""
        self._check_depth(depth)
        sig = self.sig
        text = value
        start_j = j
        while True:
            try:
                obj = _parse_flow(text)
                break
            except _FlowUnterminated:
                if j < self.n and sig[j].indent > key_col and j - start_j < MAX_FLOW_LINES:
                    self.take(j)
                    text = text + " " + sig[j].text
                    j += 1
                    continue
                obj = None
                break
            except _FlowError:
                obj = None
                break
        if obj is None:
            self.bad.append(ln.no)
            for k in range(start_j, j):
                self.consumed.discard(k)
            node = Node(ln.no, key, "scalar", value, ln.comment, key_col)
            node.lines = [(ln.no, value)]
            return node, start_j
        node = _flow_to_node(obj, key, ln.no, ln.comment, key_col)
        return node, j


def parse_workflow_full(text: str):
    """(root Node, unplaced line numbers). Raises ParseError when too deep."""
    p = Parser(text)
    root = p.parse()
    return root, p.unplaced()


def parse_workflow(text: str) -> Node:
    return parse_workflow_full(text)[0]


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
            if item.kind == "scalar" and item.scalar == name:
                return item.line
            if item.kind == "map" and item.get(name) is not None:
                return item.line
    elif node.kind == "map":
        got = node.get(name)
        if got is not None:
            return got.line
    elif node.kind == "scalar":
        if node.scalar == name:
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
    if run is not None and run.kind in ("block", "scalar"):
        out.append(run.lines)
    withn = step.get("with")
    if withn is not None and withn.kind == "map":
        script = withn.get("script")
        if script is not None and script.kind in ("block", "scalar"):
            out.append(script.lines)
    return out


def check_uses_pinning(root: Node, allow_unpinned: list, path: str) -> list:
    findings = []
    for node in all_uses(root):
        if node.kind != "scalar":
            continue
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
    return node.kind == "scalar" and node.scalar == "write-all"


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
            if withn is None or withn.kind != "map":
                continue
            ref = withn.get("ref")
            if ref is not None and ref.kind == "scalar" and _ref_names_pr_head(ref.scalar):
                findings.append(Finding(path, ref.line, "pull-request-target-checkout", "blocking"))
            repo = withn.get("repository")
            if repo is not None and repo.kind == "scalar" and _repo_names_pr_head(repo.scalar):
                # The fork itself: its default branch, or any ref, is fork code.
                findings.append(Finding(path, repo.line, "pull-request-target-checkout", "blocking"))
    return findings


def _repo_names_pr_head(value: str) -> bool:
    for e in expressions(value.lower()):
        for p in _paths_in(e):
            if p.startswith("github.event.pull_request.head.repo"):
                return True
    return False


_PR_HEAD_REF = re.compile(r"refs/pull/[^/\s]+/(?:head|merge)\b")


def _ref_names_pr_head(value: str) -> bool:
    """A checkout `ref:` that names the pull request's own code: the head
    commit or branch, or the refs/pull/N/head or /merge ref (the merge ref
    contains the head too). The base (`...pull_request.base.*`) is trusted."""
    v = value.lower()
    for e in expressions(v):
        for p in _paths_in(e):
            if p.startswith("github.event.pull_request.head.") or p == "github.head_ref":
                return True
    # An expression inside the ref (`refs/pull/${{ github.event.number }}/head`)
    # is replaced by a placeholder segment before the pattern is matched.
    return bool(_PR_HEAD_REF.search(strip_expressions(v, "n")))


_BRACKET_NAME = re.compile(r"""\[\s*(['"])([^'"]*)\1\s*\]""")
_BRACKET_INDEX = re.compile(r"\[\s*(?:\d+|\*)\s*\]")
_STRING_LIT = re.compile(r"'(?:[^']|'')*'")
_IDENT_PATH = re.compile(r"(?<![\w.\-])([a-z_][\w\-]*(?:\.(?:[a-z_][\w\-]*|\*))*)")


def _paths_in(expr: str):
    """Every context path in one `${{ ... }}` body, lower-cased (contexts are
    case-insensitive): through function calls, operators and bracket
    access. `a['b']` reads as `a.b`; `a[0]` and `a.*` keep `a`. String
    literals are dropped first so `format('{0}', x)` yields only `x`."""
    e = expr.lower()
    e = _BRACKET_NAME.sub(lambda m: "." + m.group(2), e)
    e = _BRACKET_INDEX.sub("", e)
    e = _STRING_LIT.sub(" ", e)
    for m in _IDENT_PATH.finditer(e):
        yield m.group(1)


def _classify(p: str):
    """(rule, severity) for one context path, or None."""
    if p.startswith("github.event.inputs.") or p.startswith("inputs."):
        # Checked first: a dispatch input named `message` or `title` is the
        # same class one trust level up, not the event-text rule.
        return "inputs-in-run", "minor"
    if p in _INJECTION_EXACT or p in _INJECTION_OBJECTS:
        return "expression-injection", "blocking"
    if not p.startswith("github.event."):
        return None
    parts = p.split(".")[2:]
    if "commits" in parts:
        return "expression-injection", "blocking"
    if "head_commit" in parts:
        rest = parts[parts.index("head_commit") + 1:]
        if not (len(rest) == 1 and rest[0] in _HEAD_COMMIT_SAFE):
            return "expression-injection", "blocking"
        return None
    if parts and parts[-1] in _INJECTION_LEAVES:
        return "expression-injection", "blocking"
    return None


def check_expression_injection(root: Node, path: str) -> list:
    findings = []
    for job in job_nodes(root):
        for step in steps_of(job):
            for block in with_script_or_run_blocks(step):
                seen = set()
                for lineno, text in block:
                    for e in expressions(text):
                        for p in _paths_in(e):
                            got = _classify(p)
                            if got and (lineno, got[0]) not in seen:
                                seen.add((lineno, got[0]))
                                findings.append(Finding(path, lineno, got[0], got[1]))
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
    if not uses_value or uses_value.startswith("./") or uses_value.startswith("docker://"):
        return None
    return uses_value.split("/", 1)[0]


def check_secrets_inherit_external(root: Node, org: object, path: str) -> list:
    findings = []
    skip = False
    for job in job_nodes(root):
        secrets = job.get("secrets")
        if secrets is None or secrets.kind != "scalar" or secrets.scalar != "inherit":
            continue
        uses = job.get("uses")
        owner = _owner_of(uses.scalar) if uses is not None and uses.kind == "scalar" else None
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


UNPARSEABLE = "unparseable output"


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _parse_json_list(out: str):
    """The tool's stdout as a JSON list, [] for empty output, or None."""
    if not out.strip():
        return []
    try:
        doc = json.loads(out)
    except ValueError:
        return None
    return doc if isinstance(doc, list) else None


def run_actionlint(root: Path):
    if not shutil.which("actionlint"):
        return tool_row("actionlint", "skip", "blocking", note="not on PATH")
    rc, out, err = _run_tool(["actionlint", "-format", "{{json .}}", "-no-color"], root)
    if rc is None:
        return tool_row("actionlint", "skip", "blocking", note=err or "tool error")
    doc = _parse_json_list(out)
    if doc is None:
        return tool_row("actionlint", "skip", "blocking", note=UNPARSEABLE)
    findings = []
    for item in doc:
        # Every field is checked before use: a malformed item makes the
        # whole tool a skip, and no value from the tool is ever echoed.
        if not isinstance(item, dict):
            return tool_row("actionlint", "skip", "blocking", note=UNPARSEABLE)
        path, line, kind = item.get("filepath"), item.get("line"), item.get("kind")
        if not isinstance(path, str) or not _is_int(line) or not isinstance(kind, str):
            return tool_row("actionlint", "skip", "blocking", note=UNPARSEABLE)
        findings.append({"path": sanitize_path(path), "line": line, "rule": f"actionlint/{sanitize_ident(kind)}"})
    if rc == 0 and not findings:
        return tool_row("actionlint", "pass", "blocking", 0)
    if findings:
        return tool_row("actionlint", "fail", "blocking", len(findings), findings)
    return tool_row("actionlint", "skip", "blocking", note=f"exit {int(rc)}, no parseable findings")


def _dig(obj, *keys):
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def _zizmor_location(item: dict):
    """(path, line, used_fallback). zizmor's JSON shape is read defensively
    from every place a version has been seen to put it; a value that is not
    found falls back to `?` / 0 and is flagged, never guessed."""
    locs = item.get("locations")
    loc = locs[0] if isinstance(locs, list) and locs else {}
    path = None
    for cand in (
        _dig(loc, "symbolic", "key", "Local", "given_path"),
        _dig(loc, "concrete", "location", "path"),
        _dig(loc, "concrete", "path"),
        _dig(loc, "symbolic", "key_path"),
    ):
        if isinstance(cand, str) and cand:
            path = cand
            break
    row = _dig(loc, "concrete", "location", "start_point", "row")
    line = row + 1 if _is_int(row) and row >= 0 else None
    fallback = path is None or line is None
    return (path if path is not None else "?"), (line if line is not None else 0), fallback


def run_zizmor(root: Path):
    if not shutil.which("zizmor"):
        return tool_row("zizmor", "skip", "blocking", note="not on PATH")
    rc, out, err = _run_tool(["zizmor", "--format", "json", ".github/workflows"], root)
    if rc is None:
        return tool_row("zizmor", "skip", "blocking", note=err or "tool error")
    doc = _parse_json_list(out)
    if doc is None:
        return tool_row("zizmor", "skip", "blocking", note=UNPARSEABLE)
    findings = []
    sev_counts = {}
    fallback = False
    for item in doc:
        if not isinstance(item, dict) or not isinstance(item.get("ident"), str):
            return tool_row("zizmor", "skip", "blocking", note=UNPARSEABLE)
        ident = sanitize_ident(item["ident"])
        sev = _dig(item, "determinations", "severity")
        sev = sanitize_ident(sev) if isinstance(sev, str) else "unknown"
        sev_counts[sev] = sev_counts.get(sev, 0) + 1
        path, line, fb = _zizmor_location(item)
        fallback = fallback or fb
        findings.append({"path": sanitize_path(path), "line": line, "rule": f"zizmor/{ident}"})
    note = "counts by severity: " + ", ".join(f"{k}={v}" for k, v in sorted(sev_counts.items())) if sev_counts else ""
    if fallback:
        note = (note + "; " if note else "") + "location not found in the output, path ? or line 0 used"
    if rc == 0 and not findings:
        return tool_row("zizmor", "pass", "blocking", 0, note=note)
    if findings:
        return tool_row("zizmor", "fail", "blocking", len(findings), findings, note=note)
    return tool_row("zizmor", "skip", "blocking", note=f"exit {int(rc)}, no parseable findings")


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
    parse_findings = []
    any_skip_needs_org = False
    for f in files:
        rel = f.relative_to(root).as_posix()
        try:
            text = f.read_text(errors="replace")
            tree, unplaced = parse_workflow_full(text)
            findings, skip = run_structural(tree, rel, allow_unpinned, org)
        except Exception:  # noqa: BLE001 - fail closed on any parser or rule bug
            # No exception text and no file text: only the path and a rule.
            parse_findings.append(Finding(rel, 1, "parse-error", "blocking"))
            continue
        if unplaced:
            # Rules still ran on what was placed; the first line nothing
            # consumed marks the file as not fully checked.
            parse_findings.append(Finding(rel, unplaced[0], "parse-incomplete", "blocking"))
        all_findings.extend(findings)
        any_skip_needs_org = any_skip_needs_org or skip

    rows = [run_actionlint(root), run_zizmor(root)]
    rows.append({"check": "parse", "status": "fail" if parse_findings else "pass", "severity": "blocking",
                 "count": len(parse_findings), "findings": [{"path": x.path, "line": x.line, "rule": x.rule} for x in parse_findings],
                 "note": ""})

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
        capped = []
        for r in rows:
            r = dict(r)
            r["truncated"] = max(0, len(r["findings"]) - MAX_FINDINGS)
            r["findings"] = r["findings"][:MAX_FINDINGS]
            capped.append(r)
        # The basename only: an absolute path would leak the local layout.
        name = sanitize_path(root.resolve().name or ".")
        print(json.dumps({"ok": ok, "root": name, "rows": capped}, ensure_ascii=False))
    else:
        print(render(rows))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
