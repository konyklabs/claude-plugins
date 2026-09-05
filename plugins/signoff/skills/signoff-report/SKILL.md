---
name: signoff-report
description: Writes testcases/coverage.md — summary counts, a table per area, the ranked gaps, a tile diff against a previous sign-off and, when given a run report, the failing tests mapped to their tiles — with report.py; use to sign off a change, to see what moved since the last sign-off, or to write the coverage section of a pull request.
---

# Signoff report

The report's sections and its diff and run-report options are in
`plugins/signoff/formats.md`, the only home of them; this skill does not
restate them.

## Running it

```
python3 "${CLAUDE_SKILL_DIR}/scripts/report.py" --tiles .qa/tiles.json \
  --since-ref <previous sign-off's commit or tag> --run <playwright-run.json> \
  --out testcases/coverage.md
```

`--since-ref` is a git ref — the commit or tag of the previous sign-off —
whose `.qa/tiles.json` `report.py` reads with `git show` and diffs against
the current tiles file; when the ref or the file at it does not exist, the
diff prints as a `skip` line, never a failure. `--run` is the latest
Playwright JSON run report (`npx playwright test --reporter=json`, saved to
a file first); give it to map the run's failing tests onto the tiles they
claim, so a failing test shows up next to the tile it was supposed to
cover. Both are optional; `--tiles` and `--out` are not.

## What goes in the pull request

Quote three things from this cycle's report, not the whole file: the diff
of `testcases/` itself (new, removed and reworded cases), the summary line
and per-area table from `coverage.md`, and the ranked gap list. A reviewer
reading the PR description should see what changed without opening
`.qa/tiles.json`.

## Uncovered high-risk tiles

A sign-off is not a pass/fail gate; `report.py` always writes the report,
gap list included. When a high-risk tile is still uncovered, the PR
description says so in a sentence — which tile, what it is — rather than a
coverage percentage that quietly buries it. Silence on a high-risk gap is
the failure mode this step exists to prevent.
