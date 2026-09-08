#!/usr/bin/env python3
"""Summarize an OpenTofu/Terraform plan JSON into a table and fail closed on
destructive actions.

Usage:
    tofu plan -out=tfplan && tofu show -json tfplan > tfplan.json
    python3 plan_summary.py tfplan.json [--allow ADDRESS ...] [--json]

Exit codes:
    0  no destroy, replace or forget, or every one is allow-listed
    2  a destroy, replace or forget is present and not allow-listed
       (forget = removed from state without destroy; the object stops
       being managed, which is as irreversible as a destroy for review)
    1  the input is missing, unreadable or not a plan

The script never prints attribute values from the plan: a plan carries
secrets. It prints resource addresses and types only, sanitized and
length-capped, because the review that reads this table needs them.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter

# Addresses are `type.name`, `module.m["k"].type.name[0]` and the like. Anything
# outside this set is replaced and the length is capped, so an address cannot
# break the table's structure (newlines, control characters, markup). A
# resource *name* is still repository text and can be a sentence; the reader
# treats the address column as data, never as an instruction.
_ADDRESS_OK = re.compile(r'[^A-Za-z0-9_.\[\]"\-/:]')
# Longest reasonable address: a few nested modules plus a keyed instance. A
# longer one is truncated so the table stays a table.
_ADDRESS_MAX = 160

# The action arrays the JSON plan format documents, mapped to one word.
_ACTION_CLASS = {
    ("no-op",): "no-op",
    ("create",): "create",
    ("read",): "read",
    ("update",): "update",
    ("delete",): "destroy",
    ("delete", "create"): "replace",
    ("create", "delete"): "replace",
    ("forget",): "forget",
}
DESTRUCTIVE = {"destroy", "replace", "forget"}


def sanitize(address: str) -> str:
    cleaned = _ADDRESS_OK.sub("?", str(address))
    if len(cleaned) > _ADDRESS_MAX:
        return cleaned[: _ADDRESS_MAX - 1] + "…"
    return cleaned


def classify(actions: list[str]) -> str:
    return _ACTION_CLASS.get(tuple(actions), "unknown:" + "+".join(map(str, actions)))


def summarize(plan: dict) -> dict:
    rows = []
    counts: Counter[str] = Counter()
    for rc in plan.get("resource_changes") or []:
        change = rc.get("change") or {}
        cls = classify(change.get("actions") or [])
        counts[cls] += 1
        if cls == "no-op":
            continue
        rows.append(
            {
                "address": sanitize(rc.get("address", "")),
                "_raw": str(rc.get("address", "")),  # for the allow-list match; never printed
                "type": sanitize(rc.get("type", "")),
                "action": cls,
                "reason": sanitize(rc.get("action_reason") or ""),
            }
        )
    drift = [sanitize(d.get("address", "")) for d in plan.get("resource_drift") or []]
    outputs = plan.get("output_changes") or {}
    output_changes = sorted(
        sanitize(name)
        for name, ch in outputs.items()
        if (ch.get("actions") or ["no-op"]) != ["no-op"]
    )
    return {
        "format_version": str(plan.get("format_version", "")),
        "counts": dict(counts),
        "changes": rows,
        "drift": drift,
        "output_changes": output_changes,
    }


def render(summary: dict, blocked: list[dict], allowed: list[dict]) -> str:
    c = summary["counts"]
    order = ["create", "update", "replace", "destroy", "forget", "read", "no-op"]
    head = "  ".join(f"{k}={c.get(k, 0)}" for k in order if k in c or k in ("create", "update", "replace", "destroy"))
    lines = [f"plan: {head}"]
    unknown = [k for k in c if k.startswith("unknown:")]
    if unknown:
        lines.append(f"unknown action arrays (treated as destructive): {', '.join(unknown)}")
    if summary["changes"]:
        lines.append("")
        lines.append(f"{'action':8} {'type':40} address")
        for row in sorted(summary["changes"], key=lambda r: (r["action"], r["address"])):
            reason = f"  ({row['reason']})" if row["reason"] else ""
            lines.append(f"{row['action']:8} {row['type']:40} {row['address']}{reason}")
    if summary["drift"]:
        lines.append("")
        lines.append(f"drift: {len(summary['drift'])} resource(s) changed outside the plan: " + ", ".join(summary["drift"][:20]))
    if summary["output_changes"]:
        lines.append("outputs changed: " + ", ".join(summary["output_changes"]))
    lines.append("")
    if blocked:
        lines.append("BLOCKED: destructive change(s) (destroy, replace, forget) not on the allow list:")
        lines.extend(f"  {r['action']:8} {r['address']}" for r in blocked)
    elif allowed:
        lines.append("OK: every destructive change is on the allow list:")
        lines.extend(f"  {r['action']:8} {r['address']}" for r in allowed)
    else:
        lines.append("OK: no destroy, replace or forget in this plan")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan", help="path to the output of `tofu show -json PLANFILE`")
    ap.add_argument("--allow", action="append", default=[], metavar="ADDRESS", help="a resource address whose destroy/replace is intended; repeatable")
    ap.add_argument("--json", action="store_true", help="emit the summary as JSON instead of a table")
    args = ap.parse_args(argv)

    try:
        with open(args.plan, "rb") as fh:
            plan = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"plan_summary: cannot read plan: {exc.__class__.__name__}", file=sys.stderr)
        return 1
    if not isinstance(plan, dict) or "format_version" not in plan or "resource_changes" not in plan and "planned_values" not in plan:
        print("plan_summary: not a plan JSON (expected format_version and resource_changes from `tofu show -json`)", file=sys.stderr)
        return 1

    summary = summarize(plan)
    # Exact match on the raw address: sanitizing first would let one
    # allow entry admit a different address that sanitizes the same way.
    allow = set(args.allow)
    destructive = [r for r in summary["changes"] if r["action"] in DESTRUCTIVE or r["action"].startswith("unknown:")]
    blocked = [r for r in destructive if r["_raw"] not in allow]
    allowed = [r for r in destructive if r["_raw"] in allow]
    for r in summary["changes"]:
        r.pop("_raw", None)
    summary["blocked"] = blocked
    summary["allowed"] = allowed

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(render(summary, blocked, allowed))
    return 2 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
