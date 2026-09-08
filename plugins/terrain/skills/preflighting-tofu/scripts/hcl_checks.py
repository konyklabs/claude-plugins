#!/usr/bin/env python3
"""Structural checks on OpenTofu/Terraform source for the AWS traps that
validate, tflint, trivy and checkov do not catch: Fargate task shape, log
groups nobody creates, Lambda code that never redeploys, secrets that are
not marked sensitive, state without locking, hardcoded account ids, star
IAM actions written as JSON, SQS visibility below the consumer's timeout.

Usage:
    python3 hcl_checks.py DIR [--json]

Exit codes: 0 no findings, 2 findings, 1 not a module directory.

The scanner is deliberately small: top-level blocks by brace matching, and
attribute lookups by regex inside a block's text. It never evaluates HCL,
so a value it cannot read as a literal is left alone rather than guessed.
Output is `path:line rule` only, no source text.
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
_STAR_ACTION = re.compile(r"""(Action|actions?)\s*[=:]\s*\[?\s*["']\*["']""")

Block = dict  # keys: kind, labels, body, line, path


def scan_blocks(text: str, path: str) -> list[Block]:
    """Top-level HCL blocks. Skips strings, comments and heredocs so a brace
    in a string does not unbalance the count."""
    blocks: list[Block] = []
    i, n, depth, line = 0, len(text), 0, 1
    start = header_start = -1
    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch == "#" or text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            seg = text[i : n if j < 0 else j + 2]
            line += seg.count("\n")
            i += len(seg)
            continue
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == "\\":
                    j += 1
                elif text[j] == "\n":
                    line += 1
                elif text.startswith("${", j):  # template: skip to matching }
                    k, d = j + 2, 1
                    while k < n and d:
                        d += text[k] == "{"
                        d -= text[k] == "}"
                        line += text[k] == "\n"
                        k += 1
                    j = k
                    continue
                j += 1
            i = j + 1
            continue
        if text.startswith("<<", i):
            m = re.match(r"<<-?\s*([A-Za-z_][A-Za-z0-9_]*)", text[i:])
            if m:
                tag = m.group(1)
                j = re.search(r"^\s*" + re.escape(tag) + r"\s*$", text[i:], re.M)
                seg = text[i : n if not j else i + j.end()]
                line += seg.count("\n")
                i += len(seg)
                continue
        if ch == "{":
            if depth == 0:
                header_start = text.rfind("\n", 0, i) + 1
                start = i
                start_line = line
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                header = text[header_start:start].strip()
                m = re.match(r'([a-z_]+)((?:\s+"[^"]*")*)\s*$', header)
                if m:
                    labels = re.findall(r'"([^"]*)"', m.group(2))
                    blocks.append({"kind": m.group(1), "labels": labels, "body": text[start + 1 : i], "line": start_line, "path": path})
                start = -1
        i += 1
    return blocks


def attr(body: str, name: str) -> str | None:
    """The raw right-hand side of the first top-level-looking `name = ...`
    assignment in body, or None."""
    m = re.search(r"(?m)^\s*" + re.escape(name) + r"\s*=\s*(.+?)\s*$", body)
    return m.group(1) if m else None


def literal_int(v: str | None) -> int | None:
    if v is None:
        return None
    m = re.fullmatch(r'"?(\d+)"?', v.strip())
    return int(m.group(1)) if m else None


def literal_str(v: str | None) -> str | None:
    if v is None:
        return None
    m = re.fullmatch(r'"(.*)"', v.strip())
    return m.group(1) if m else None


def line_of(block: Block, pattern: str) -> int:
    m = re.search(pattern, block["body"])
    return block["line"] + block["body"][: m.start()].count("\n") if m else block["line"]


def run_checks(blocks: list[Block], texts: dict[str, str]) -> list[str]:
    out: list[str] = []
    res = [b for b in blocks if b["kind"] == "resource" and len(b["labels"]) == 2]
    by_type: dict[str, list[Block]] = {}
    for b in res:
        by_type.setdefault(b["labels"][0], []).append(b)

    def f(b: Block, rule: str, line: int | None = None) -> None:
        out.append(f"{b['path']}:{line or b['line']} {rule}")

    # Fargate task definitions
    fargate_tasks = [b for b in by_type.get("aws_ecs_task_definition", []) if "FARGATE" in (attr(b["body"], "requires_compatibilities") or "")]
    for b in fargate_tasks:
        if literal_str(attr(b["body"], "network_mode")) != "awsvpc":
            f(b, "fargate_network_mode_not_awsvpc")
        cpu, mem = literal_int(attr(b["body"], "cpu")), literal_int(attr(b["body"], "memory"))
        if cpu is not None and mem is not None and mem not in _FARGATE.get(cpu, set()):
            f(b, "fargate_cpu_memory_invalid", line_of(b, r"(?m)^\s*memory\s*="))
        if attr(b["body"], "execution_role_arn") and attr(b["body"], "execution_role_arn") == attr(b["body"], "task_role_arn"):
            f(b, "task_role_same_as_execution_role", line_of(b, r"(?m)^\s*task_role_arn\s*="))
        for m in re.finditer(r'"awslogs-group"\s*=\s*"([^"]*)"', b["body"]):
            wanted = m.group(1)
            managed = {literal_str(attr(lg["body"], "name")) for lg in by_type.get("aws_cloudwatch_log_group", [])}
            if wanted not in managed and '"awslogs-create-group"' not in b["body"]:
                f(b, "awslogs_group_not_managed", b["line"] + b["body"][: m.start()].count("\n"))
    if fargate_tasks:
        for tg in by_type.get("aws_lb_target_group", []):
            if literal_str(attr(tg["body"], "target_type")) != "ip":
                f(tg, "target_group_not_ip_for_fargate")

    # Lambda
    lambdas = {b["labels"][1]: b for b in by_type.get("aws_lambda_function", [])}
    for b in lambdas.values():
        if attr(b["body"], "filename") and not (attr(b["body"], "source_code_hash") or attr(b["body"], "code_sha256")):
            f(b, "lambda_no_source_code_hash", line_of(b, r"(?m)^\s*filename\s*="))
    queues = {b["labels"][1]: b for b in by_type.get("aws_sqs_queue", [])}
    for esm in by_type.get("aws_lambda_event_source_mapping", []):
        q = re.search(r"aws_sqs_queue\.([A-Za-z0-9_-]+)\.arn", attr(esm["body"], "event_source_arn") or "")
        fn = re.search(r"aws_lambda_function\.([A-Za-z0-9_-]+)\.", attr(esm["body"], "function_name") or "")
        if q and fn and q.group(1) in queues and fn.group(1) in lambdas:
            vis = literal_int(attr(queues[q.group(1)]["body"], "visibility_timeout_seconds"))
            to = literal_int(attr(lambdas[fn.group(1)]["body"], "timeout"))
            vis = _SQS_DEFAULT_VISIBILITY if vis is None else vis
            to = _LAMBDA_DEFAULT_TIMEOUT if to is None else to
            if vis < to:
                f(esm, "sqs_visibility_below_lambda_timeout")

    # Secrets on variables and outputs
    for b in blocks:
        if b["kind"] in ("variable", "output") and b["labels"] and _SECRET_NAME.search(b["labels"][0]):
            if literal_str(attr(b["body"], "sensitive")) is None and (attr(b["body"], "sensitive") or "").strip() != "true":
                f(b, f"secret_{b['kind']}_not_sensitive")
            if b["kind"] == "variable" and attr(b["body"], "default") not in (None, "null"):
                f(b, "secret_variable_has_default", line_of(b, r"(?m)^\s*default\s*="))

    # Backend locking
    for b in blocks:
        if b["kind"] == "terraform":
            for m in re.finditer(r'backend\s+"s3"\s*\{', b["body"]):
                seg = b["body"][m.end() : b["body"].find("}", m.end())]
                if "use_lockfile" not in seg and "dynamodb_table" not in seg:
                    f(b, "s3_backend_no_locking", b["line"] + b["body"][: m.start()].count("\n"))

    # Text-level patterns, per file
    for path, text in texts.items():
        for lineno, line in enumerate(text.splitlines(), 1):
            if _ACCOUNT_ID.search(line):
                out.append(f"{path}:{lineno} hardcoded_account_id")
            if _STAR_ACTION.search(line):
                out.append(f"{path}:{lineno} iam_star_action")
    return sorted(set(out), key=lambda s: (s.split(":")[0], int(s.split(":")[1].split()[0]), s))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    d = Path(args.dir)
    files = sorted(p for p in d.glob("*.tf") if p.is_file())
    if not files:
        print("hcl_checks: not a module directory (no *.tf files)", file=sys.stderr)
        return 1
    texts = {p.name: p.read_text(errors="replace") for p in files}
    blocks = [b for name, text in texts.items() for b in scan_blocks(text, name)]
    findings = run_checks(blocks, texts)
    if args.json:
        print(json.dumps({"findings": findings, "count": len(findings)}))
    else:
        print("\n".join(findings) if findings else "hcl_checks: no findings")
    return 2 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
