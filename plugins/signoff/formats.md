# signoff: the artifact formats

One home for every file the plugin reads or writes. Scripts and skills point
here; none restates a format. JSON for the working files (stdlib reads them);
Markdown for the durable test cases (people read them; the lint parses the
fixed skeleton). Decision: konyklabs/roadmap D-009.

## Identifiers

- `area`: kebab-case, one word where possible (`auth`, `org`, `credentials`,
  `docs`); exactly `^[a-z][a-z0-9-]*$`, because an area becomes a directory
  and an export file name.
- screen id: `<area>.<screen>` (`auth.login`, `org.home`).
- flow id: `<area>.<flow>` (`auth.sign-in`).
- rule id: `<area>.<flow>.<rule-slug>` (`auth.sign-in.valid-password`).
- tile id: a rule id (kind `rule`), `<screen>.render.<role>` (kind `render`),
  or `<screen>.error.<slug>` (kind `error`).
- test id: `<file>::<title>` for Playwright TS (`e2e/auth.spec.ts::signs in
  with a valid password`, the title without its `@` tokens), `<file>::<name>`
  for pytest (`tests/e2e/test_auth.py::test_sign_in_valid`).
- case id: `TC-<area>-<nnn>` (`TC-auth-001`).

Every id above matches `^[a-z0-9][a-z0-9._:-]*$`. tile.py and mapcheck.py
check it rather than trust it: an id is printed into a Markdown report and
joined into file names, so one carrying a slash, a backtick or a newline is
refused (mapcheck.py: a problem line; tile.py: the tile is dropped with a
warning). Every string that comes out of a scanned repository - ids, areas,
kinds, risks, titles, recorded file paths - has its control characters
replaced by `?` and its length capped (ids and areas at 120 characters,
titles and paths at 200) with a trailing `…` when it was cut, before it
reaches stdout, `tiles.json` or `coverage.md`.

A test claims a tile with the tag `@tile:<tile id>` in its title or `tag:`
list, or the static annotation `{type: "tile", description: "<tile id>"}`
passed to `test()`, or `@pytest.mark.tile("<tile id>")`. One test may claim
several tiles.

Measured 2026-09-04 (Playwright 1.5x, `tests/fixtures/playwright-list.json`
and `playwright-run.json`, whose absolute paths were rewritten to
`/work/pw-list/...` and are otherwise byte-for-byte the capture): `npx playwright test --list --reporter=json`
emits the run report's shape with every test `skipped` and `results: []`;
`tags` carry no `@` (`tile:auth.sign-in.valid-password`); a tag given in the
title stays in `title`, one given through `tag:` does not; `file` is relative
to `config.rootDir`; an annotation pushed at run time
(`test.info().annotations.push`) appears only in a run report, never in the
list, so a claim that must be visible at list time is a tag or a static
annotation.

## `.qa/map.json` (explore writes; mapcheck.py validates)

```json
{
  "base_url": "http://localhost:8000",
  "explored_at": "2026-09-04T18:00:00Z",
  "roles": ["anonymous", "member", "admin"],
  "screens": [
    {"id": "auth.login", "area": "auth", "path": "/login", "title": "Sign in",
     "roles": ["anonymous"],
     "actions": [{"id": "submit", "kind": "submit", "label": "Sign in", "to": "org.home"}],
     "forms": [{"id": "login", "fields": ["email", "password"]}],
     "states": ["error:invalid-credentials"],
     "links": ["docs.home"]}
  ],
  "flows": [
    {"id": "auth.sign-in", "area": "auth", "name": "Sign in", "role": "anonymous",
     "steps": ["auth.login", "org.home"]}
  ]
}
```

`kind` of an action: `navigate | submit | toggle | mutate`. `mutate` is a
destructive or state-changing action the explorer records but never performs
unless the allowlist names it. `states`: free strings; `error:<slug>` is the
prefix tile.py turns into an error tile. `roles`: which roles can reach the
screen; `anonymous` is a role.

## `.qa/routes.json` (routes.py writes)

```json
{"framework": "fastapi", "detected_from": "src/app/main.py:1",
 "routes": [{"method": "POST", "path": "/login", "handler": "login", "source": "src/app/auth.py:41",
             "guards": [{"kind": "auth", "source": "src/app/auth.py:39"}]}],
 "skipped": [{"path": "src/legacy", "reason": "framework not recognised"}]}
```

## `.qa/rules.json` (mine writes)

```json
{"mined_at": "2026-09-04T18:10:00Z",
 "rules": [
  {"id": "auth.sign-in.valid-password", "area": "auth", "flow": "auth.sign-in",
   "kind": "transition", "risk": "high",
   "statement": "A valid email and password sign the user in and land on the organisation page",
   "source": "src/app/auth.py:41", "screens": ["auth.login", "org.home"]}
 ]}
```

`kind`: `guard | validation | transition | flag | error | calculation`.
`risk`: `high | medium | low`; when absent, tile.py derives it from kind
(guard high; validation, error, transition medium; flag, calculation low).

## `.qa/tests.json` (tests.py writes)

```json
{"stack": "playwright-ts", "listed_at": "2026-09-04T18:20:00Z", "command": "npx playwright test --list --reporter=json",
 "tests": [
  {"id": "e2e/auth.spec.ts::signs in with a valid password", "title": "signs in with a valid password",
   "file": "e2e/auth.spec.ts", "line": 4, "tags": ["@tile:auth.sign-in.valid-password"],
   "annotations": [{"type": "tile", "description": "auth.login.render.anonymous"}],
   "tiles": ["auth.sign-in.valid-password", "auth.login.render.anonymous"],
   "skipped": false}
 ]}
```

`tests.py --run` writes this file only when the listing both exited zero and
reported an empty `errors` array; either one otherwise is exit 2 and a `skip:`
reason, because a suite that failed to load lists only what compiled, and
tiling against a partial listing reports the rest as gaps.

`stack`: `playwright-ts | pytest`. `id` uses the title with its `@` tokens
removed and `file` joined to `config.rootDir` relative to the working
directory. `tiles` is derived: every `tile:` tag (with or without `@`), every
`tile` annotation present in the input, and every `pytest.mark.tile`
argument.

`skipped` (boolean, default false): the test is disabled, so it claims a tile
without ever exercising it. True when pytest carries a `skip`, `skipif` or
`xfail` marker, whatever its arguments; or when Playwright reports the test's
`expectedStatus` as `skipped`, or an annotation of type `skip` or `fixme`.

## `.qa/tiles.json` (tile.py writes)

```json
{"tiled_at": "2026-09-04T18:30:00Z",
 "tiles": [
  {"id": "auth.sign-in.valid-password", "area": "auth", "kind": "rule", "flow": "auth.sign-in",
   "rule": "auth.sign-in.valid-password", "screen": null, "role": null, "risk": "high",
   "tests": ["e2e/auth.spec.ts::signs in with a valid password"], "skipped_tests": [],
   "cases": ["TC-auth-001"], "status": "covered"}
 ],
 "gaps": ["org.members.remove.requires-admin", "auth.login.error.invalid-credentials"],
 "unknown_claims": [{"test": "e2e/auth.spec.ts::renamed", "tile": "auth.sign-in.old-id"}]}
```

Tiles come from: every rule (kind `rule`, risk = the rule's); every screen
× role in its `roles` (kind `render`, id `<screen>.render.<role>`, risk
`low`: a page that renders is the cheapest fact about it); every
`error:<slug>` state (kind `error`, id `<screen>.error.<slug>`, risk
`medium`: an error path is where a user is stranded). `status`: `covered` when
a test claims it; `manual` when only a case with status `manual` names it;
`uncovered` otherwise. `gaps` = uncovered tile ids ordered by risk (high,
medium, low), then kind (rule, error, render), then id.

`skipped_tests`: the ids of the tests that claim this tile and are `skipped`
in `tests.json`. They never enter `tests`, so they never make a tile
`covered`; a tile claimed only by disabled tests is `uncovered` and ranks as
a gap, and tile.py's summary and the report's gap list mark it
` (claimed by a disabled test: <id>)`.

`gaps` is tile.py's ranking of its own tiles. report.py does not read it: it
ranks the tiles it was given by the same rule, and prints one `warning:` line
when a `gaps` key is present and disagrees. A stale or hand-edited key can
then never hide a gap the tiles beside it plainly show.

`unknown_claims`: one entry per claim on a tile id no rule or screen defines,
`{"test": <test id>, "tile": <tile id>}`, in the order the tests were read.
tile.py also prints `warning: <test id> claims unknown tile <id>` for each.

Percent covered, wherever it is printed: `round(100 * covered / tiles)` to
the nearest whole number, and 0 when there are no tiles.

## `testcases/<area>/TC-<area>-<nnn>.md` (people write; cases.py lints and exports)

```markdown
# TC-auth-001: Sign in with a valid password

- area: auth
- tiles: auth.sign-in.valid-password, auth.login.render.anonymous
- role: anonymous
- priority: high
- status: automated
- automated: e2e/auth.spec.ts::signs in with a valid password

## Preconditions

- a member account exists with a known password

## Steps

| # | Action | Expected |
|---|--------|----------|
| 1 | Open /login | The sign-in form shows email and password fields |
| 2 | Enter the email and password, press Sign in | The organisation page opens and shows the member's name |
```

Fixed skeleton: the title line; the metadata list in that order (`automated`
may be empty, and must be empty unless `status` is `automated`); a
`## Preconditions` list (`- none` when there are none); a `## Steps` table
with at least one row, both cells non-empty. `status`: `automated | manual |
planned`. `priority`: `high | medium | low`. Anything after the table is
free prose. The steps are the *first* table after `## Steps` only - its
header row, its separator row, then its consecutive `|` rows, ending at the
first line that is not one - so a table written in that prose is prose.

Lint (`cases.py check`): the skeleton; every tile named exists in
`.qa/tiles.json` when it is present; every `automated` id exists in
`.qa/tests.json` and claims at least one of the case's tiles; every tile
with status `covered` is named by at least one case. Findings are
`path:line rule` with a one-line reason.

Exports (`cases.py export --format ...`): `azure-csv` writes the nine
columns Azure Test Plans documents, one row per step (`ID` empty for a new
item, `Work Item Type` = `Test Case`, `Title` = `<id>: <title>`, `Test
Step` numbered from 1, `Step Action`, `Step Expected`, `Area Path` = the
area, `Assigned To` empty, `State` = `Design`); `gherkin` writes one
`<area>.feature` per area with `Scenario: <id> <title>`, `Given` per
precondition, `When`/`Then` per step (`And` after the first); `markdown`
writes `testcases/README.md`, a table per area (id, title, status,
priority, tiles, automated).

## `testcases/coverage.md` (report.py writes)

Summary counts; a table per area (tiles, covered, manual, uncovered,
percent covered); the ranked gaps; the tile diff against a previous
`tiles.json` (`--since <path>`, or `--since-ref <git ref>` reading
`.qa/tiles.json` from that commit): new, removed, and status-changed tiles;
when a Playwright JSON run report is given (`--run <path>`), the failing
tests mapped to their tiles.

`--run` takes a run report only. A `--list` capture has the same shape with
every test `skipped` and `results: []`, so it would read as a run in which
nothing failed; report.py refuses any input in which no test carries a
non-empty `results` list, with `<path>:0 not-a-run-report: <reason>` and
exit 2.
