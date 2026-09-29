"""scripts/digests.py: the 14-day digest rule as a check."""
import importlib.util
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "digests.py"
spec = importlib.util.spec_from_file_location("digests", SCRIPT)
digests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(digests)

TODAY = date(2026, 9, 29)


def _skill(root: Path, plugin: str, skill: str, skill_md: str, refs=None):
    d = root / "plugins" / plugin / "skills" / skill
    (d / "references").mkdir(parents=True)
    (d / "SKILL.md").write_text(skill_md)
    for name, text in (refs or {}).items():
        (d / "references" / name).write_text(text)
    return d


def _run(root: Path, *args):
    return subprocess.run([sys.executable, str(SCRIPT), str(root), "--today", TODAY.isoformat(), *args], capture_output=True, text=True)


def test_fresh_stale_boundary_and_oldest_marker_wins(tmp_path):
    fresh = (TODAY - timedelta(days=3)).isoformat()
    edge = (TODAY - timedelta(days=14)).isoformat()
    over = (TODAY - timedelta(days=15)).isoformat()
    _skill(tmp_path, "p", "fresh", "# s\nSources: docs; fetched %s." % fresh, {"a.md": f"Fetched {fresh} from x."})
    _skill(tmp_path, "p", "edge", "# s\n", {"a.md": f"All fetched {edge}."})
    _skill(tmp_path, "p", "over", "# s\n", {"a.md": f"fetched {over}"})
    _skill(tmp_path, "p", "mixed", "# s\n", {"a.md": f"section one fetched {fresh}; section two Fetched {over}."})
    _skill(tmp_path, "p", "wrapped", "# s\n", {"a.md": f"AWS ECR VPC endpoints page. Fetched\n{fresh}.\n"})
    _skill(tmp_path, "p", "forms", f"# s\nSources: docs, fetched on {fresh}.\n", {"a.md": f"Fetched: {fresh} from x."})
    _skill(tmp_path, "p", "nearmiss", f"# s\nSources: docs, fetched {fresh.replace('-', '/')}.\n", {"a.md": f"fetched {fresh}"})
    rows = {r["skill"] + "/" + Path(r["file"]).name: r for r in digests.scan(tmp_path, TODAY, 14)}
    assert rows["fresh/SKILL.md"]["status"] == "fresh" and rows["fresh/a.md"]["status"] == "fresh"
    assert rows["edge/a.md"]["status"] == "fresh" and rows["edge/a.md"]["age"] == 14
    assert rows["over/a.md"]["status"] == "stale" and rows["over/a.md"]["age"] == 15
    # a file is as old as its oldest source
    assert rows["mixed/a.md"]["status"] == "stale" and rows["mixed/a.md"]["fetched"] == over
    assert rows["wrapped/a.md"]["status"] == "fresh"  # a marker wrapped across a line break counts
    assert rows["forms/SKILL.md"]["status"] == "fresh" and rows["forms/a.md"]["status"] == "fresh"  # "fetched on", "Fetched:"
    assert rows["nearmiss/SKILL.md"]["status"] == "malformed"  # a slash date next to the word is never a silent none
    # a SKILL.md that cites nothing is not a digest
    assert rows["edge/SKILL.md"]["status"] == "none"
    p = _run(tmp_path)
    assert p.returncode == 1 and "1 stale" not in p.stdout and "2 stale" in p.stdout and "refresh:" in p.stdout


def test_references_need_a_marker_unless_they_say_they_have_no_sources(tmp_path):
    _skill(tmp_path, "p", "s", "# s\n", {"template.md": "# Brief\n\n<!-- no external sources: a template of this repository's own making -->\n", "digest.md": "# notes\nno date here\n",
                                       "prose.md": "# notes\nThe scanner runs offline and contacts no external sources.\n"})
    (tmp_path / "plugins/p/skills/s/references/nested").mkdir()
    (tmp_path / "plugins/p/skills/s/references/nested/deep.md").write_text("fetched 2020-01-01\n")
    rows = {Path(r["file"]).name: r for r in digests.scan(tmp_path, TODAY, 14)}
    assert rows["template.md"]["status"] == "none" and rows["template.md"]["note"] == "no external sources"
    assert rows["digest.md"]["status"] == "missing"
    assert rows["prose.md"]["status"] == "missing"  # the phrase in prose is not the opt-out comment
    assert rows["deep.md"]["status"] == "stale"      # a nested references file is not a way out
    assert _run(tmp_path).returncode == 1


def test_a_skill_with_a_sources_section_must_carry_its_date(tmp_path):
    _skill(tmp_path, "p", "cites", "# s\n\n## Sources\n\nThe provider docs and the release notes.\n")
    _skill(tmp_path, "p", "plain", "# s\n\nRun the script.\n")
    _skill(tmp_path, "p", "own", "# s\n\n## Sources\n\n<!-- no external sources: this plugin's own rubrics -->\n")
    rows = {r["skill"]: r for r in digests.scan(tmp_path, TODAY, 14)}
    assert rows["cites"]["status"] == "missing"   # a dropped date on a digest is not a way out
    assert rows["plain"]["status"] == "none"
    assert rows["own"]["status"] == "none"


def test_malformed_and_future_dates_fail(tmp_path):
    _skill(tmp_path, "p", "bad", "# s\n", {"a.md": "fetched 2026-13-40"})
    _skill(tmp_path, "p", "future", "# s\n", {"a.md": "fetched %s" % (TODAY + timedelta(days=2)).isoformat()})
    _skill(tmp_path, "p", "tomorrow", "# s\n", {"a.md": "fetched %s" % (TODAY + timedelta(days=1)).isoformat()})
    rows = {r["skill"]: r for r in digests.scan(tmp_path, TODAY, 14) if r["file"].endswith("a.md")}
    assert rows["bad"]["status"] == "malformed" and "2026-13-40" in rows["bad"]["note"]
    assert rows["future"]["status"] == "malformed"
    assert rows["tomorrow"]["status"] == "fresh"  # a refresh dated after local midnight east of UTC
    assert _run(tmp_path).returncode == 1
    assert _run(tmp_path, "--today", "yesterday").returncode == 2


def test_json_output_and_max_age_flag(tmp_path):
    old = (TODAY - timedelta(days=20)).isoformat()
    _skill(tmp_path, "p", "s", "# s\n", {"a.md": f"fetched {old}"})
    p = _run(tmp_path, "--json")
    out = json.loads(p.stdout)
    assert p.returncode == 1 and out["ok"] is False and out["rows"][1]["status"] == "stale" and out["max_age_days"] == 14
    p = _run(tmp_path, "--json", "--max-age", "30")
    assert p.returncode == 0 and json.loads(p.stdout)["ok"] is True


def test_the_repository_tree_parses_and_carries_a_marker_in_every_reference():
    """Freshness is CI's business (calendar); this asserts the tree is well-formed:
    no malformed date and no references file without a marker or an opt-out."""
    rows = digests.scan(REPO, date.today(), 10_000)  # the real calendar: a pinned day would call every later refresh "future"
    assert rows, "no skills found"
    bad = [r for r in rows if r["status"] in ("missing", "malformed")]
    assert bad == [], bad
