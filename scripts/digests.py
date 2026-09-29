#!/usr/bin/env python3
"""Check that every skill digest is fresh.

A skill that depends on external documentation keeps a dated digest: its
`references/*.md`, and its SKILL.md when that cites docs itself, carry the
words `fetched YYYY-MM-DD` next to their sources. The workspace rule says a
digest older than 14 days is re-fetched, never trusted from memory. Nothing
enforced that until 2026-09-29, when every digest in the repository but one
was between 21 and 27 days old; this script is the enforcement, the way the
arch drift check enforces the model.

Usage:
    python3 scripts/digests.py [ROOT] [--max-age DAYS] [--today YYYY-MM-DD] [--json]

Exit codes:
    0  every digest is fresh
    1  a digest is stale, a references file carries no marker, or a date is
       malformed or in the future
    2  a bad argument (`--today` not a date)

Rules this script keeps:
- Scanned: plugins/*/skills/*/SKILL.md and every .md under a skill's
  references/ (the convention is one level deep; a nested file is not a way out).
- A marker is `fetched YYYY-MM-DD` in any case (`Fetched`, `All fetched`).
  A file with several markers is as old as its oldest: every source in it
  must be fresh.
- Every references file carries a marker, or the comment
  `<!-- no external sources: why -->` (a template or checklist of this
  repository's own making); the phrase in prose does not count.
- A SKILL.md without a marker is `none` (not every skill digests docs), unless
  it has a `## Sources` section, which is a digest whose date was dropped.
- A date up to one day ahead of the runner's clock is fresh (a refresh dated
  after local midnight east of UTC); further ahead is malformed.
- Refresh means re-reading the cited pages (Context7 when its server is
  present, the raw source otherwise), applying what changed, and only then
  writing today's date. A bumped date with no re-read is the failure this
  check exists to make visible, and a lens round is where it is caught.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_MAX_AGE_DAYS = 14
# "Fetched 2026-09-29", "fetched on 2026-09-29", "Fetched: 2026-09-29", and the prose
# wrap "Fetched\n2026-09-08" all count; a date in any other shape next to the
# word is a malformed marker, never a silent `none`
MARKER_RE = re.compile(r"\bfetched\b\W{0,3}(?:on\s+)?(\d{4}-\d{2}-\d{2})\b", re.I)
NEAR_MISS_RE = re.compile(r"\bfetched\b[^\n]{0,24}?\d{4}", re.I)
OPT_OUT = "no external sources"
OPT_OUT_RE = re.compile(r"<!--\s*" + OPT_OUT + r"\b", re.I)  # the comment form only: prose cannot exempt a digest
SOURCES_RE = re.compile(r"^#{2,3}\s*Sources\b", re.M)
FUTURE_TOLERANCE_DAYS = 1
FAILING = ("stale", "missing", "malformed")


def judge(path: Path, root: Path, today: date, max_age: int) -> Dict[str, Any]:
    rel = path.relative_to(root)
    parts = rel.parts  # plugins/<plugin>/skills/<skill>/...
    row: Dict[str, Any] = {
        "plugin": parts[1] if len(parts) > 1 else "",
        "skill": parts[3] if len(parts) > 3 else "",
        "file": rel.as_posix(),
        "fetched": None,
        "age": None,
        "status": "none",
        "note": "",
    }
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        row.update(status="malformed", note=f"unreadable: {e.__class__.__name__}")
        return row
    found = MARKER_RE.findall(text)
    is_reference = path.parent.name == "references"
    if not found:
        opted_out = OPT_OUT_RE.search(text) is not None
        if NEAR_MISS_RE.search(text):
            row.update(status="malformed", note="a date next to `fetched` is not `fetched YYYY-MM-DD`")
        elif opted_out:
            row.update(note=OPT_OUT)
        elif is_reference:
            row.update(status="missing", note=f"no `fetched YYYY-MM-DD` marker and no `<!-- {OPT_OUT}: ... -->` comment")
        elif SOURCES_RE.search(text):
            row.update(status="missing", note="a Sources section with no `fetched YYYY-MM-DD` marker")
        else:
            row.update(note="no marker; the skill cites no docs")
        return row
    parsed: List[date] = []
    for d in found:
        try:
            parsed.append(date.fromisoformat(d))
        except ValueError:
            row.update(status="malformed", note=f"bad date {d}")
            return row
    oldest = min(parsed)
    age = (today - oldest).days
    row.update(fetched=oldest.isoformat(), age=age)
    if age < -FUTURE_TOLERANCE_DAYS:
        row.update(status="malformed", note="fetched date is in the future")
    elif age > max_age:
        row.update(status="stale", note=f"older than {max_age} days: re-fetch the cited pages, then date it")
    else:
        row.update(status="fresh")
    return row


def scan(root: Path, today: date, max_age: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for skill_md in sorted(root.glob("plugins/*/skills/*/SKILL.md")):
        files = [skill_md] + sorted((skill_md.parent / "references").rglob("*.md"))
        rows.extend(judge(f, root, today, max_age) for f in files)
    return rows


def render(rows: List[Dict[str, Any]], max_age: int) -> str:
    lines = [f"{'status':<10}{'fetched':<12}{'age':>4}  file"]
    for r in rows:
        age = "" if r["age"] is None else str(r["age"])
        lines.append(f"{r['status']:<10}{r['fetched'] or '-':<12}{age:>4}  {r['file']}" + (f"  ({r['note']})" if r["note"] and r["status"] != "fresh" else ""))
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("fresh", "stale", "missing", "malformed", "none")}
    lines.append("digests: " + ", ".join(f"{n} {s}" for s, n in counts.items() if n) + f" (max age {max_age} days)")
    if any(r["status"] in FAILING for r in rows):
        lines.append("refresh: re-read each stale file's cited pages (Context7 when present, else the raw source), apply what changed, then write `fetched <today>` — see README, Development.")
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--max-age", type=int, default=DEFAULT_MAX_AGE_DAYS, metavar="DAYS")
    ap.add_argument("--today", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    try:
        today = date.fromisoformat(a.today) if a.today else date.today()
    except ValueError:
        print(f"digests: --today must be YYYY-MM-DD, got {a.today!r}", file=sys.stderr)
        return 2
    rows = scan(root, today, a.max_age)
    failed = any(r["status"] in FAILING for r in rows)
    if a.json:
        print(json.dumps({"today": today.isoformat(), "max_age_days": a.max_age, "ok": not failed, "rows": rows}, indent=1))
    else:
        print(render(rows, a.max_age))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
