---
name: recording-test-cases
description: Lints and exports the human-readable test cases under testcases/<area>/TC-<area>-<nnn>.md — the fixed skeleton, tile references and automated-test links — with cases.py; use when writing a case to close a coverage gap, before every commit that touches testcases/, or when exporting cases to Azure CSV, Gherkin or a Markdown index.
---

# Recording test cases

The case skeleton, its lint rules and its export formats are in
`plugins/signoff/formats.md`, the only home of them; this skill does not
restate them.

## Writing a case from a tile

Start from the gap list `tiling-coverage` produced. For a `rule` tile, the
case's `## Steps` come from the rule's flow in `.qa/map.json`: walk the
flow's `steps` (a list of screen ids) and, at each screen, the `actions`
that move to the next one; the `Expected` cell of the last step is the
rule's own `statement` from `.qa/rules.json`. For a `render` or `error`
tile, the steps are just reaching the screen (and, for `error`, triggering
the state), and the expected cell is what the screen shows.

Copy the skeleton from an existing case in the same area, or from
`formats.md`, rather than typing it from memory: the title line, the
metadata list in its fixed order, `## Preconditions`, and the `## Steps`
table.

## Check before every commit

```
python3 "${CLAUDE_SKILL_DIR}/scripts/cases.py" check testcases
```

Add `--tiles .qa/tiles.json` and `--tests .qa/tests.json` to also check that
every named tile and every `automated` id actually exist. Run this before
every commit that touches `testcases/`; findings print as `path:line rule`,
one line each.

## status: manual, planned, automated

`manual` is a case a person runs; it closes its tile on its own, with no
automated test. `planned` is neither yet — written down so the case exists,
but not run and not automated, and it does not close its tile. `automated`
is set once a test claims the tile and the case's `automated` field names
it; a case only ever reaches `automated` that way, never by editing the
status field alone.

## The both-way link

A case names its tiles; a tile is `covered` only when a test claims it, and
`manual` only when a case with status `manual` names it. Renaming or
removing a tile in `.qa/rules.json` without updating the cases that name it
is exactly what `cases.py check --tiles` catches — run it after any
mine-step change, not just after editing a case.

## Exporting

```
python3 "${CLAUDE_SKILL_DIR}/scripts/cases.py" export --format azure-csv --out testcases/export.csv testcases
python3 "${CLAUDE_SKILL_DIR}/scripts/cases.py" export --format gherkin --out testcases/features testcases
python3 "${CLAUDE_SKILL_DIR}/scripts/cases.py" export --format markdown --out testcases/README.md testcases
```

`azure-csv` is for importing into Azure Test Plans; `gherkin` writes one
`<area>.feature` file per area, for a BDD runner or a reviewer who wants
Given/When/Then; `markdown` regenerates `testcases/README.md`, the index a
person browses in the repository. Export refuses to run while `check` still
finds issues in the same directory.
