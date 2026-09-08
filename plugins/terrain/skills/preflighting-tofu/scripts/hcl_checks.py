#!/usr/bin/env python3
"""Structural checks on OpenTofu/Terraform source for the AWS traps that
validate, tflint, trivy and checkov do not catch: Fargate task shape, log
groups nobody creates, Lambda code that never redeploys, secrets that are
not marked sensitive, state without locking, hardcoded account ids, star
IAM actions written as JSON, SQS visibility below the consumer's timeout.

Usage:
    python3 hcl_checks.py DIR [--json]

Exit codes: 0 no findings, 2 findings, 1 not a module directory.

The scanner is deliberately small: a mask that blanks strings, comments
and heredocs so braces can be counted, top-level blocks from that count,
and attribute lookups at a block's top level only. It never evaluates
HCL, so a value it cannot read as a literal is left alone rather than
guessed. Every `*.tf` under DIR is read (`.terraform` and other dot
directories skipped), so a root-plus-modules layout is covered. Output
is `path:line rule` only, no source text; paths are sanitized because
they come from the scanned repository.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Fargate task-level CPU (units) to allowed memory (MiB), from the AWS ECS
# developer guide "Task CPU and memory" table for Linux tasks. Memory values
# between the bounds step by 1024 up to 4 vCPU, 4096 at 8 vCPU, 8192 at 16.
_FARGATE = {
    256: {512, 1024, 2048},
    512: set(range(1024, 4097, 1024)),
    1024: set(range(2048, 8193, 1024)),
    2048: set(range(4096, 16385, 1024)),
    4096: set(range(8192, 30721, 1024)),
    8192: set(range(16384, 61441, 4096)),
    16384: set(range(32768, 122881, 8192)),
    32768: {61440, 122880, 249856},  # 60, 120 and 244 GB, platform 1.4.0+
}
# SQS default when visibility_timeout_seconds is unset (SQS documentation).
_SQS_DEFAULT_VISIBILITY = 30
# Lambda default when timeout is unset (Lambda documentation).
_LAMBDA_DEFAULT_TIMEOUT = 3
# Names that, on a variable or output, mean the value is a credential.
_SECRET_NAME = re.compile(r"(password|passwd|secret|token|api_?key|private_?key|credential)", re.I)
_ACCOUNT_ID = re.compile(r"(arn:aws[a-z-]*:[a-z0-9-]*:[a-z0-9-]*:\d{12}:|\b\d{12}\.dkr\.ecr\.)")
# An Action list or string in either the jsonencode form or the policy
# document form, spanning lines; a `"*"` element anywhere in it is the hit.
_ACTION_VALUE = re.compile(r"""(?:\bAction\b|\bactions?\b)\s*[=:]\s*(\[[^\]]*\]|"[^"]*")""", re.S)
_STAR = re.compile(r"""["']\*["']""")
# File names come from the scanned repository and reach a report a model
# reads: anything outside this set is replaced, and a name is capped so the
# table stays a table. Colons are excluded so `path:line` stays parseable.
_PATH_OK = re.compile(r"[^A-Za-z0-9_.\-/]")
_PATH_MAX = 80

Block = dict  # keys: kind, labels, body, masked, line, path


def sanitize_path(name: str) -> str:
    out = _PATH_OK.sub("?", str(name))
    return out if len(out) <= _PATH_MAX else out[: _PATH_MAX - 1] + "…"


def mask(text: str) -> str:
    """The same text with the inside of strings, comments and heredocs
    replaced by spaces, newlines kept, so indexes line up with the
    original and braces can be counted without being fooled."""
    out = list(text)
    i, n = 0, len(text)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        ch = text[i]
        if ch == "#" or text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
        elif ch == '"':
            j, depth = i + 1, 0
            while j < n:
                c = text[j]
                if c == "\\":
                    j += 2
                    continue
                if depth == 0 and c == '"':
                    break
                if text.startswith("${", j) or text.startswith("%{", j):
                    depth += 1
                    j += 2
                    continue
                if depth and c == "}":
                    depth -= 1
                j += 1
            blank(i + 1, j)  # keep the quotes so `= "..."` still reads as a string
            i = j + 1
        elif text.startswith("<<", i):
            m = re.match(r"<<-?\s*([A-Za-z_][A-Za-z0-9_]*)", text[i:])
            if not m:
                i += 2
                continue
            end = re.search(r"^\s*" + re.escape(m.group(1)) + r"\s*$", text[i + m.end():], re.M)
            j = n if not end else i + m.end() + end.end()
            blank(i, j)
            i = j
        else:
            i += 1
    return "".join(out)


def scan_blocks(text: str, path: str) -> list[Block]:
    """Top-level HCL blocks with their raw and masked bodies."""
    masked = mask(text)
    blocks: list[Block] = []
    depth, start, header_start, start_line = 0, -1, 0, 0
    for i, ch in enumerate(masked):
        if ch == "{":
            if depth == 0:
                header_start = masked.rfind("\n", 0, i) + 1
                start = i
                start_line = masked.count("\n", 0, i) + 1
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                header = text[header_start:start].strip()
                m = re.match(r'([a-z_]+)((?:\s+"[^"]*")*)\s*$', header)
                if m:
                    labels = re.findall(r'"([^"]*)"', m.group(2))
                    blocks.append({"kind": m.group(1), "labels": labels, "body": text[start + 1 : i], "masked": masked[start + 1 : i], "line": start_line, "path": path})
                start = -1
            elif depth < 0:
                depth = 0
    return blocks


def attr(block: Block, name: str) -> str | None:
    """The raw right-hand side of `name = ...` at the block's top level,
    spanning lines until its brackets balance, or None."""
    masked, body = block["masked"], block["body"]
    depth = 0
    for m in re.finditer(r"(?m)^[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(?!=)", masked):
        depth_here = masked.count("{", 0, m.start()) + masked.count("[", 0, m.start()) + masked.count("(", 0, m.start()) \
            - masked.count("}", 0, m.start()) - masked.count("]", 0, m.start()) - masked.count(")", 0, m.start())
        if depth_here != 0 or m.group(1) != name:
            continue
        j, depth = m.end(), 0
        while j < len(masked):
            c = masked[j]
            if c in "{[(":
                depth += 1
            elif c in "}])":
                depth -= 1
            elif c == "\n" and depth <= 0:
                break
            j += 1
        # The mask has blanked any trailing comment, so its last non-blank
        # character is where the real value ends.
        end = m.end() + len(masked[m.end() : j].rstrip())
        return body[m.end() : end].strip()
    return None


def literal_int(v: str | None) -> int | None:
    if v is None:
        return None
    m = re.fullmatch(r'"?(\d+)"?', v.strip())
    return int(m.group(1)) if m else None


def literal_str(v: str | None) -> str | None:
    if v is None:
        return None
    m = re.fullmatch(r'"(.*)"', v.strip(), re.S)
    return m.group(1) if m else None


def line_of(block: Block, pattern: str) -> int:
    m = re.search(pattern, block["body"])
    return block["line"] + block["body"][: m.start()].count("\n") if m else block["line"]


def balanced_end(masked: str, open_idx: int) -> int:
    """Index of the `}` matching the `{` at open_idx in masked text."""
    depth = 0
    for j in range(open_idx, len(masked)):
        if masked[j] == "{":
            depth += 1
        elif masked[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return len(masked)


def run_checks(blocks: list[Block], texts: dict[str, str]) -> list[str]:
    """Findings as `path:line rule`, sorted by path then line. Paths are
    sanitized here, once, whatever the caller passed in."""
    out: list[tuple[str, int, str]] = []
    res = [b for b in blocks if b["kind"] == "resource" and len(b["labels"]) == 2]
    by_type: dict[str, list[Block]] = {}
    for b in res:
        by_type.setdefault(b["labels"][0], []).append(b)

    def f(b: Block, rule: str, line: int | None = None) -> None:
        out.append((sanitize_path(b["path"]), int(line or b["line"]), rule))

    # Fargate task definitions
    fargate_tasks = [b for b in by_type.get("aws_ecs_task_definition", []) if "FARGATE" in (attr(b, "requires_compatibilities") or "")]
    for b in fargate_tasks:
        if literal_str(attr(b, "network_mode")) != "awsvpc":
            f(b, "fargate_network_mode_not_awsvpc")
        cpu, mem = literal_int(attr(b, "cpu")), literal_int(attr(b, "memory"))
        if cpu is not None and mem is not None and mem not in _FARGATE.get(cpu, set()):
            f(b, "fargate_cpu_memory_invalid", line_of(b, r"(?m)^\s*memory\s*="))
        exec_role, task_role = attr(b, "execution_role_arn"), attr(b, "task_role_arn")
        if exec_role and exec_role == task_role:
            f(b, "task_role_same_as_execution_role", line_of(b, r"(?m)^\s*task_role_arn\s*="))
        managed = {literal_str(attr(lg, "name")) for lg in by_type.get("aws_cloudwatch_log_group", [])}
        for m in re.finditer(r'"awslogs-group"\s*=\s*"([^"]*)"', b["body"]):
            if m.group(1) not in managed and '"awslogs-create-group"' not in b["body"]:
                f(b, "awslogs_group_not_managed", b["line"] + b["body"][: m.start()].count("\n"))
    if fargate_tasks:
        for tg in by_type.get("aws_lb_target_group", []):
            if literal_str(attr(tg, "target_type")) != "ip":
                f(tg, "target_group_not_ip_for_fargate")

    # Lambda
    lambdas = {b["labels"][1]: b for b in by_type.get("aws_lambda_function", [])}
    for b in lambdas.values():
        if attr(b, "filename") and not (attr(b, "source_code_hash") or attr(b, "code_sha256")):
            f(b, "lambda_no_source_code_hash", line_of(b, r"(?m)^\s*filename\s*="))
    queues = {b["labels"][1]: b for b in by_type.get("aws_sqs_queue", [])}
    for esm in by_type.get("aws_lambda_event_source_mapping", []):
        q = re.search(r"aws_sqs_queue\.([A-Za-z0-9_-]+)\.arn", attr(esm, "event_source_arn") or "")
        fn = re.search(r"aws_lambda_function\.([A-Za-z0-9_-]+)\.", attr(esm, "function_name") or "")
        if q and fn and q.group(1) in queues and fn.group(1) in lambdas:
            vis_raw = attr(queues[q.group(1)], "visibility_timeout_seconds")
            to_raw = attr(lambdas[fn.group(1)], "timeout")
            # Unset means the AWS default; set to an expression means unknown,
            # and the check does not guess.
            vis = _SQS_DEFAULT_VISIBILITY if vis_raw is None else literal_int(vis_raw)
            to = _LAMBDA_DEFAULT_TIMEOUT if to_raw is None else literal_int(to_raw)
            if vis is not None and to is not None and vis < to:
                f(esm, "sqs_visibility_below_lambda_timeout")

    # Secrets on variables and outputs
    for b in blocks:
        if b["kind"] in ("variable", "output") and b["labels"] and _SECRET_NAME.search(b["labels"][0]):
            if (attr(b, "sensitive") or "").strip() != "true":
                f(b, f"secret_{b['kind']}_not_sensitive")
            if b["kind"] == "variable" and attr(b, "default") not in (None, "null"):
                f(b, "secret_variable_has_default", line_of(b, r"(?m)^\s*default\s*="))

    # Backend locking
    for b in blocks:
        if b["kind"] == "terraform":
            for m in re.finditer(r'backend\s+"s3"\s*\{', b["body"]):  # the label is blank in the mask; indexes line up
                seg = b["masked"][m.end() : balanced_end(b["masked"], m.end() - 1)]
                if not re.search(r"(?m)^\s*(use_lockfile|dynamodb_table)\s*=", seg):
                    f(b, "s3_backend_no_locking", b["line"] + b["body"][: m.start()].count("\n"))

    # Text-level patterns, per file
    for path, text in texts.items():
        for lineno, line in enumerate(text.splitlines(), 1):
            if _ACCOUNT_ID.search(line):
                out.append((sanitize_path(path), lineno, "hardcoded_account_id"))
        for m in _ACTION_VALUE.finditer(text):
            if _STAR.search(m.group(1)):
                out.append((sanitize_path(path), text.count("\n", 0, m.start()) + 1, "iam_star_action"))
    return [f"{path}:{line} {rule}" for path, line, rule in sorted(set(out))]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    d = Path(args.dir)
    files = sorted(p for p in d.rglob("*.tf") if p.is_file() and not any(part.startswith(".") for part in p.relative_to(d).parts))
    if not files:
        print("hcl_checks: not a module directory (no *.tf files)", file=sys.stderr)
        return 1
    texts = {p.relative_to(d).as_posix(): p.read_text(errors="replace") for p in files}
    blocks = [b for name, text in texts.items() for b in scan_blocks(text, name)]
    findings = run_checks(blocks, texts)
    if args.json:
        print(json.dumps({"findings": findings, "count": len(findings)}))
    else:
        print("\n".join(findings) if findings else "hcl_checks: no findings")
    return 2 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
