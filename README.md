# claude-plugins

Claude Code plugins for sessions that run on an expensive model under a tight
token budget. Two plugins, one marketplace:

| plugin | what it is |
|---|---|
| **governor** | Guardrails in code for Fable-tier sessions: spawns are pinned to cheap workers, forks are denied, an expensive spawn needs a written brief, a priced per-session budget is enforced from the transcript, and workers cannot stop without evidence. Plus the agents (scout, implementer, senior-implementer, reviewer, architect) and the skills (triage, delegate, decompose, consult, budget) that make the cheap path the easy path. |
| **py-testing** | Python test engineering: pytest project layout, Playwright API and browser tests, SQLAlchemy test fixtures, and the workflow for untangling a large unmerged test suite, with a deterministic inventory script and a Sonnet worker that has the stack skills preloaded. |
| **prod-readiness** | Production-readiness and security scanning for API sample apps and developer portals: one deterministic scan emits a categorized report (secret and identifier leakage, debug and observability surfaces including HTML-string sinks, denial-of-service surface, correctness traps, CI and supply chain, documentation drift), external scanners are summarized to counts and never installed, and an Opus auditor judges only the rows that need judgment. Twenty-five check classes from a real hardening pass, ordered by observed impact: nineteen settled by the scanner, the rest by a test or the auditor. |

The design and its reasons are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Install

```
claude plugin marketplace add konyklabs/claude-plugins
claude plugin install governor@konyklabs-plugins
claude plugin install py-testing@konyklabs-plugins
claude plugin install prod-readiness@konyklabs-plugins
```

From a checkout, for development: `claude --plugin-dir ./plugins/governor`.
The plugins need `python3` (3.9+) on PATH; nothing else.

**On a machine with no git access to this repository** (a work machine):
build the zips once anywhere that has the checkout, copy them over, and
load them directly:

```
bash scripts/dist.sh                      # dist/<plugin>.zip from the committed tree, each scanned
claude --plugin-dir governor.zip --plugin-dir py-testing.zip --plugin-dir prod-readiness.zip
```

The zips are made with `git archive`, so nothing ignored can be inside
them, and they are checked by the readiness scanner's archive-hygiene rule
before they are reported. Nothing in the plugins phones home, installs
anything, or reads outside the session's own transcript and the project.

## Try it in five minutes

Both plugins installed at user scope, any project, a fresh session:

```
claude                      # the SessionStart hook prints the policy and a spend line
/governor:budget            # spend so far, per model and subagent; the budget
```

Then ask for something that needs a look-up, for example "find every place
we build a SQLAlchemy session". The conductor is meant to spawn
`governor:scout`; the readout on the next turn shows the spawn and its
model. Ask for a fork or for a `general-purpose` agent with no model and
watch the hook deny the first and pin the second.

To see what a session costs with no governor at all, point the ledger at
any past transcript:

```
python3 plugins/governor/bin/governor.py status \
  --session <id> --transcript ~/.claude/projects/<project>/<id>.jsonl
```

The session that built this repository, priced that way: 18.66 USD on
Fable and 25.01 in total, every subagent flagged as having inherited
`xhigh` effort, and 210 KB of `Bash` output read into the Fable context.
The default budget would have closed the gate at 15.

## governor in one minute

Start a session on Fable. The SessionStart hook injects the policy and the
current spend. From then on:

- A spawn that names no model is pinned to `sonnet` (configurable) instead
  of inheriting Fable. A `fork` is denied. A spawn *onto* Fable is allowed
  only with a brief carrying `## Question`, `## Context`, `## Definition of
  done`, and at most three per session.
- Every turn starts with one line: expensive-tier spend against the budget,
  total spend, session model, spawns so far.
- When expensive-tier spend reaches the budget (default 15 USD at API list
  price), tool calls are denied with a reason. `/model opus` keeps the
  context and lifts the gate; `/governor:budget set 25` raises it.
- `governor:implementer`, `governor:senior-implementer`,
  `py-testing:test-implementer` and `governor:scout` cannot
  finish without a report in their contract: `## Result` with
  DONE/PARTIAL/BLOCKED, `## Changed files`, `## Evidence` with the command on
  a `$ ` line and its output. The hook sends them back otherwise.

The workflow the skills encode:

```
/governor:triage      sort the task into tiers; write the table
/governor:decompose   big body of work -> levels of slices with specs
/governor:delegate    one slice -> spec -> worker -> read the evidence -> reviewer
/governor:consult     one structured question -> architect (Fable), from a cheaper session
/governor:budget      spend per model and subagent; set the budget
```

Two ways to run it:

- **Fable conducts.** The session is on Fable; the hooks keep it from doing
  the cheap work itself. Best for design sessions with little tool output.
- **Fable consults.** The session is on Opus or Sonnet; `governor:architect`
  is Fable, called with a brief for the one decision that needs it. Best
  for long implementation runs, where the conductor's context is what costs.

Configuration: `~/.claude/governor.json` for the user (per-project entries
under `projects`), `.claude/governor.json` in a project, which may only
tighten. Keys and defaults are in `plugins/governor/bin/governor.py`
(`DEFAULTS`), each with the reason for its value. Prices are in
`plugins/governor/bin/pricing.json` with the date they were checked.

## Tracking cost without interfering

`{"mode": "observe", "readout": "off"}` in `~/.claude/governor.json` keeps
the ledger and never denies, rewrites, blocks or injects; read it with
`/governor:budget` or put it on the status line
(`governor.py statusline-snippet`). The runbook, including what the plugin
reads and writes and what CI pulls in, is [docs/COST-TRACKING.md](docs/COST-TRACKING.md).
The plugin code is standard-library Python with no network calls;
`scripts/audit-deps.sh` proves it in CI.

## py-testing in one minute

Five skills, one worker, one script.

- `testing-pytest-projects`: src layout, importlib mode, tiers as
  directories, the conftest hierarchy, registered markers, xdist, flakiness
  tools.
- `testing-playwright-api`: `APIRequestContext` fixture, base URL, roles, a
  client per resource, sharing cookies with the browser.
- `testing-playwright-browser`: fixtures, locator priority, web-first
  assertions, page objects, log in once with `storage_state`, tracing on
  failure, network mocking.
- `testing-sqlalchemy`: the savepoint-rollback session fixture (sync and
  async), Postgres via testcontainers, SQLite caveats, Alembic in fixtures,
  pytest-alembic, factories.
- `untangling-test-suites`: inventory with `scripts/inventory.py`, classify
  keep/merge/rewrite/delete, decide once, cut into slices, integrate per
  level.

`py-testing:test-implementer` is a Sonnet worker at medium effort with the
four stack skills preloaded and the governor's report contract.

Every skill ends with its sources and fetch date, and says where the docs
were silent.

## prod-readiness in one minute

```
python3 plugins/prod-readiness/skills/readiness-review/scripts/readiness.py --tier precommit   # archive hygiene, identifiers, HTML sinks
python3 plugins/prod-readiness/skills/readiness-review/scripts/readiness.py --tier release     # all 19 checks plus installed scanners
```

Stdout is a bounded table (`pass` / `fail` / `review` / `skip` per check);
the full report goes to `.readiness/report.json`. Findings are `path:line`
and a rule name, never the matched text. `/prod-readiness:readiness-review`
runs the workflow: `prod-readiness:scanner` (Sonnet) runs the scan,
`prod-readiness:auditor` (Opus) judges the `review` rows with failure
scenarios, the conductor reads two tables. `security-scanning` and
`hardening-checks` hold each check's detail, its fix and its false-positive
note; external tools (gitleaks, pip-audit, bandit, semgrep, osv-scanner,
trivy, npm audit, lychee) are documented with install-from-maintainer,
pin-and-checksum guidance and are run only if already on PATH.

## Development

```
bash scripts/validate.sh                              # strict manifest, skill and agent validation
bash scripts/audit-deps.sh                            # stdlib-only imports, no network code, what CI pulls
uv run --with pytest python -m pytest -q plugins      # hook engine and inventory script tests
claude --plugin-dir ./plugins/governor                # try it without installing
```

CI runs the same two commands plus the org's review gate, proprietary scan
and title lint. `evals/` under each plugin holds `claude plugin eval` cases;
the feature is early access and the cases are written, not yet run.

## Status

0.1.0, the first set. Driving task: konyklabs/roadmap#60.
