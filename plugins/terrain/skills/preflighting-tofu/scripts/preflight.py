#!/usr/bin/env python3
"""Run every OpenTofu/Terraform checker that is on PATH against one root
module and print a bounded table: one row per tool with pass / fail / skip,
counts by severity, and findings as `path:line rule` only.

Usage:
    python3 preflight.py DIR [--json]

Exit codes:
    0  every tool that ran passed
    2  at least one tool failed
    1  DIR is not a module directory

Rules this script keeps:
- Installs nothing, opens no sockets. A tool that is missing, errors or times
  out is a `skip` row with the reason, never a pass.
- Prints no message text from a tool and no text from the module: a finding
  is `path:line rule`. Rule ids and paths are sanitized and length-capped.
- `validate` needs an initialized directory. The script does not run `init`
  because that reaches the network; it says so and skips.
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

# Enough for a large root module on a laptop; a checker that takes longer is
# stuck, and a stuck checker must not hang the review.
TIMEOUT_S = 300
# Rows per tool in the table. Beyond this the reader is skimming, and the
# counts already say how bad it is.
MAX_ROWS = 40
_SAFE = re.compile(r"[^A-Za-z0-9_.\-/\[\]\": ]")
# Rule names and check ids are identifiers; anything else in one is an
# attempt to smuggle text into the table.
_IDENT = re.compile(r"[^A-Za-z0-9_.\-]")


def _san(s: object, n: int = 80) -> str:
    out = _SAFE.sub("?", str(s))
    return out if len(out) <= n else out[: n - 1] + "…"


def _ident(s: object, n: int = 60) -> str:
    out = _IDENT.sub("?", str(s))
    return out if len(out) <= n else out[: n - 1] + "…"


def _run(cmd: list[str], cwd: Path) -> tuple[int | None, str, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None, "", "timeout"
    except OSError as exc:
        return None, "", exc.__class__.__name__
    return p.returncode, p.stdout, p.stderr


def _row(tool: str, status: str, reason: str = "", counts: dict | None = None, findings: list[str] | None = None) -> dict:
    return {"tool": tool, "status": status, "reason": reason, "counts": counts or {}, "findings": (findings or [])[:MAX_ROWS], "truncated": max(0, len(findings or []) - MAX_ROWS)}


def _binary() -> str | None:
    for name in ("tofu", "terraform"):
        if shutil.which(name):
            return name
    return None


def check_fmt(d: Path) -> dict:
    b = _binary()
    if not b:
        return _row("fmt", "skip", "no tofu or terraform on PATH")
    rc, out, err = _run([b, "fmt", "-check", "-recursive"], d)
    if rc is None:
        return _row("fmt", "skip", err)
    if rc == 0:
        return _row("fmt", "pass")
    files = [_san(line.strip()) for line in out.splitlines() if line.strip()]
    if rc == 3:
        return _row("fmt", "fail", "", {"unformatted": len(files)}, files)
    return _row("fmt", "skip", f"exit {rc}")


def check_validate(d: Path) -> dict:
    b = _binary()
    if not b:
        return _row("validate", "skip", "no tofu or terraform on PATH")
    if not (d / ".terraform").is_dir():
        return _row("validate", "skip", f"not initialized: run `{b} init -backend=false` first")
    rc, out, err = _run([b, "validate", "-json"], d)
    if rc is None:
        return _row("validate", "skip", err)
    try:
        doc = json.loads(out)
    except ValueError:
        return _row("validate", "skip", f"exit {rc}, no JSON")
    if "valid" not in doc or "diagnostics" not in doc:
        return _row("validate", "skip", f"exit {rc}, unexpected JSON shape")
    counts: dict[str, int] = {}
    findings = []
    for diag in doc.get("diagnostics") or []:
        sev = _ident(diag.get("severity", "unknown"), 12)
        counts[sev] = counts.get(sev, 0) + 1
        rng = diag.get("range") or {}
        loc = f"{_san(rng.get('filename', '?'))}:{int((rng.get('start') or {}).get('line', 0))}"
        # No rule id exists for a validate diagnostic and its summary is
        # free text, so the row names only the severity and the address;
        # the operator reads the text by running `tofu validate` directly.
        findings.append(f"{loc} validate:{sev} {_san(diag.get('address', ''), 60)}".rstrip())
    status = "pass" if doc.get("valid") and not doc.get("error_count") else "fail"
    return _row("validate", status, "", counts, findings)


def check_tflint(d: Path) -> dict:
    if not shutil.which("tflint"):
        return _row("tflint", "skip", "not on PATH")
    rc, out, err = _run(["tflint", "--format", "json"], d)
    if rc is None:
        return _row("tflint", "skip", err)
    try:
        doc = json.loads(out)
    except ValueError:
        return _row("tflint", "skip", f"exit {rc}, no JSON (plugins not installed? run `tflint --init`)")
    if rc not in (0, 2) or "issues" not in doc:  # 1 is a tool error, whatever it printed
        return _row("tflint", "skip", f"exit {rc}, tool error")
    if doc.get("errors"):
        return _row("tflint", "skip", f"{len(doc['errors'])} tool error(s)")
    counts: dict[str, int] = {}
    findings = []
    for issue in doc.get("issues") or []:
        rule = issue.get("rule") or {}
        sev = _ident(rule.get("severity", "unknown"), 12)
        counts[sev] = counts.get(sev, 0) + 1
        rng = issue.get("range") or {}
        findings.append(f"{_san(rng.get('filename', '?'))}:{int((rng.get('start') or {}).get('line', 0))} {_ident(rule.get('name', ''))}")
    return _row("tflint", "fail" if findings else "pass", "", counts, findings)


def check_trivy(d: Path) -> dict:
    if not shutil.which("trivy"):
        return _row("trivy", "skip", "not on PATH")
    rc, out, err = _run(["trivy", "config", "--format", "json", "--quiet", "--severity", "CRITICAL,HIGH,MEDIUM,LOW", "."], d)
    if rc is None:
        return _row("trivy", "skip", err)
    try:
        doc = json.loads(out)
    except ValueError:
        return _row("trivy", "skip", f"exit {rc}, no JSON")
    if rc != 0 or not isinstance(doc, dict) or "Results" not in doc:  # without --exit-code, non-zero is a scan failure
        return _row("trivy", "skip", f"exit {rc}, no Results")
    counts: dict[str, int] = {}
    findings = []
    for res in doc.get("Results") or []:
        for m in res.get("Misconfigurations") or []:
            sev = _ident(m.get("Severity", "UNKNOWN"), 12)
            counts[sev] = counts.get(sev, 0) + 1
            line = int((m.get("CauseMetadata") or {}).get("StartLine") or 0)
            findings.append(f"{_san(res.get('Target', '?'))}:{line} {_ident(m.get('ID', ''))} {sev}")
    return _row("trivy", "fail" if findings else "pass", "", counts, findings)


def check_checkov(d: Path) -> dict:
    if not shutil.which("checkov"):
        return _row("checkov", "skip", "not on PATH")
    rc, out, err = _run(["checkov", "-d", ".", "--framework", "terraform", "-o", "json", "--quiet"], d)
    if rc is None:
        return _row("checkov", "skip", err)
    try:
        doc = json.loads(out)
    except ValueError:
        return _row("checkov", "skip", f"exit {rc}, no JSON")
    reports = doc if isinstance(doc, list) else [doc]
    if rc not in (0, 1) or not all(isinstance(r, dict) and "summary" in r and "results" in r for r in reports):
        return _row("checkov", "skip", f"exit {rc}, no report")
    counts: dict[str, int] = {"failed": 0, "passed": 0}
    findings = []
    for rep in reports:
        summ = rep.get("summary") or {}
        counts["failed"] += int(summ.get("failed", 0))
        counts["passed"] += int(summ.get("passed", 0))
        for f in (rep.get("results") or {}).get("failed_checks") or []:
            line = int((f.get("file_line_range") or [0])[0])
            findings.append(f"{_san(f.get('file_path', '?').lstrip('/'))}:{line} {_ident(f.get('check_id', ''))}")
    return _row("checkov", "fail" if findings else "pass", "", counts, findings)


CHECKS = [check_fmt, check_validate, check_tflint, check_trivy, check_checkov]


def render(rows: list[dict]) -> str:
    lines = [f"{'tool':10} {'status':6} counts / reason"]
    for r in rows:
        detail = r["reason"] if r["status"] == "skip" else " ".join(f"{k}={v}" for k, v in r["counts"].items()) or "-"
        lines.append(f"{r['tool']:10} {r['status']:6} {detail}")
    for r in rows:
        if r["findings"]:
            lines.append("")
            lines.append(f"[{r['tool']}]")
            lines.extend("  " + f for f in r["findings"])
            if r["truncated"]:
                lines.append(f"  … {r['truncated']} more")
    skipped = [r["tool"] for r in rows if r["status"] == "skip"]
    lines.append("")
    if skipped:
        lines.append("skipped (not evidence of a pass): " + ", ".join(skipped))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    d = Path(args.dir)
    if not d.is_dir() or not any(d.glob("*.tf")):
        print("preflight: not a module directory (no *.tf files)", file=sys.stderr)
        return 1
    rows = [check(d) for check in CHECKS]
    print(json.dumps(rows, indent=2, ensure_ascii=False) if args.json else render(rows))
    return 2 if any(r["status"] == "fail" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
