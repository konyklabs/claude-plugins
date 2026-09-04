---
name: tiling-coverage
description: Builds .qa/tiles.json — one tile per mined rule, per screen×role render and per error state — lists the suite's own tests, and ranks the gaps by risk, with tests.py and tile.py; use once map.json and rules.json exist, and again on every later cycle, to see what the app does that nothing covers.
---

# Tiling coverage

The tile derivation rules, the id formats and the gap order are in
`plugins/signoff/formats.md`, the only home of them; this skill does not
restate them.

## 1. List the tests

Run the stack's own tests.py first: `tile.py` marks a tile covered only by
what `tests.py` found.

```
python3 "${CLAUDE_SKILL_DIR}/scripts/tests.py" --stack playwright-ts --run --out .qa/tests.json
python3 "${CLAUDE_SKILL_DIR}/scripts/tests.py" --stack pytest e2e --out .qa/tests.json
```

Tell the stack apart by what the project has: a `playwright.config.*` at the
root means `playwright-ts` (`--run`, which shells out to `npx playwright
test --list --reporter=json`); its absence means the pytest stack, and the
positional argument is the tests directory, not the whole repository.

## 2. Build the tiles

```
python3 "${CLAUDE_SKILL_DIR}/scripts/tile.py" --map .qa/map.json --rules .qa/rules.json \
  --tests .qa/tests.json --out .qa/tiles.json
```

Add `--cases testcases` when a `testcases/` directory already exists, so a
case marked `manual` closes a tile even with no automated test on it yet.
Run this after `map.json` and `rules.json` exist (the explore and mine
steps), and again on every later cycle once the suite or the map has moved.

## 3. Read the result

`tile.py` writes `.qa/tiles.json` and prints the same summary as Markdown to
stdout: a count line, a table per area (tiles, covered, manual, uncovered),
and a ranked gap list, high risk first. The gap list is the coverage
backlog to work from; read it top to bottom, not the tiles file itself.

## Pruning noise

A tile that keeps showing up as a false-positive gap — a render tile for a
role that cannot actually reach the screen, a rule mined from dead code — is
never fixed by hand-editing `.qa/tiles.json`; the next run overwrites it.
Fix the source instead: correct the role list in `map.json`, or drop or
reword the rule in `.qa/rules.json` (the mine step), then re-run `tile.py`.

## Into the next cycle

Keep this cycle's `.qa/tiles.json` (commit it, or tag the commit): the next
cycle's `signoff-report` diffs `--since-ref` against it to show what moved.
That diff, not the raw tile count, is the review surface — new gaps, closed
gaps, and tiles whose status changed are what a reviewer reads.
