# claude-plugins

Claude Code plugins for sessions that run on an expensive model under a
tight token budget. The pattern in every plugin is the same: the expensive
model decides, cheap workers implement under a strict contract, and every
step that can be a script is a script.

| plugin | what it is | install name |
|---|---|---|
| **governor** | Guardrails in code for Fable-tier sessions: spawns are pinned to cheap workers, forks are denied, an expensive spawn needs a written brief, a priced per-session budget is enforced from the transcript, and workers cannot stop without evidence. Agents with pinned model and effort, skills for triage, delegation, decomposition and consultation, a ledger, a status line, and headless worker runs under a hard dollar cap. | `governor@konyklabs-plugins` |
| **py-testing** | Python test engineering: pytest project layout, Playwright API and browser tests, SQLAlchemy test fixtures, and the workflow for untangling a large unmerged test suite, with a deterministic inventory script and a Sonnet worker that has the stack skills preloaded. | `py-testing@konyklabs-plugins` |
| **prod-readiness** | Production-readiness and security scanning for API sample apps and developer portals: one deterministic scan emits a categorized report, external scanners are summarized to counts and never installed, an Opus auditor judges only the rows that need judgment. Twenty-five check classes from a real hardening pass, nineteen settled by the scanner. | `prod-readiness@konyklabs-plugins` |

Requirements: Claude Code 2.1.255 or later, `python3` 3.9 or later on PATH.
Nothing else: no pip, no npm, no network calls, no installs at run time
(`scripts/audit-deps.sh` proves it in CI).

## Install

Pick one of three, depending on what the machine can reach.

**A. From GitHub** (the repository is private; the machine needs git access
to it, over SSH or `gh auth` with `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1`):

```
claude plugin marketplace add konyklabs/claude-plugins
claude plugin install governor@konyklabs-plugins
claude plugin install py-testing@konyklabs-plugins
claude plugin install prod-readiness@konyklabs-plugins
```

Until the first pull request has merged, add the branch instead:
`claude plugin marketplace add konyklabs/claude-plugins@feat/60-claude-plugins`.

**B. From zips, no git access** (a work machine): build once anywhere that
has the checkout, copy the three files, load them directly.

```
bash scripts/dist.sh                       # dist/<plugin>.zip, from the committed tree, each scanned
claude --plugin-dir governor.zip --plugin-dir py-testing.zip --plugin-dir prod-readiness.zip
```

The zips are `git archive` output (nothing ignored can be inside) and each
one is checked by the readiness scanner's archive-hygiene rule before the
script reports it. They contain no tests.

**C. From a checkout, for development**: `claude --plugin-dir ./plugins/governor`.
An installed plugin is a versioned copy; edits in the checkout are not seen
until `version` in its `plugin.json` changes, so use `--plugin-dir` while
developing.

Verify: `claude plugin list` shows the three as enabled; `claude plugin
details governor` shows 7 skills, 5 agents, 5 hooks and about 1,350
always-on tokens per session.

## First session

Start `claude` in any project. The governor's SessionStart hook prints its
policy and a spend line; every turn starts with one line of spend. Then:

```
/governor:budget          spend so far per model and subagent, and the budget
/governor:triage          sort the task in front of you into tiers
/governor:explore <q>     a question that is not yet a task: explore mode, then ship, spike or drop
/prod-readiness:readiness-review   scan a repository before a release
```

To track cost without interfering with anything (recommended for the first
day on a new machine), put this in `~/.claude/governor.json`:

```json
{"mode": "observe", "readout": "off"}
```

and add the status line printed by
`python3 <plugin root>/bin/governor.py statusline-snippet` to
`~/.claude/settings.json`. Nothing is denied or injected in observe mode;
the ledger still prices every turn. `docs/COST-TRACKING.md` is the runbook.
For the full run, from the brief to the budget gate, see `docs/PLAYBOOK.md`.

## For an agent picking this up

The human-facing sequence is `docs/PLAYBOOK.md`; this section is the reference.
Everything an agent needs to operate the plugins without reading the code.

**Skills** (slash commands, namespaced by plugin):

| skill | use it when |
|---|---|
| `/governor:brief` | a task is about to start and the prompt is a sentence; five questions at most, then `.governor/brief.md` |
| `/governor:explore` | a question is not yet a task; switches to explore mode, frames it in three lines, and ends in ship, spike or drop |
| `/governor:triage` | a non-trivial task starts; it writes the tier table before any work |
| `/governor:delegate` | implementation, test writing or a look-up is about to happen; spec first, worker second, evidence third |
| `/governor:decompose` | a change touches more than about five files or a branch is too big to review |
| `/governor:consult` | a session on a cheaper model needs one decision from Fable, with a brief |
| `/governor:budget` | spend, budget, modes, status line |
| `/py-testing:testing-pytest-projects`, `testing-playwright-api`, `testing-playwright-browser`, `testing-sqlalchemy` | writing or fixing tests in that part of the stack |
| `/py-testing:untangling-test-suites` | a large or unmerged test suite nobody can review |
| `/prod-readiness:readiness-review` | before a release or a publish; runs the scan, judges the review rows, writes the report |
| `/prod-readiness:security-scanning`, `hardening-checks` | the detail, fix and false-positive note for one row of the scan |

**Agents** (spawn by `subagent_type`; never pass `model`, the definition pins it):

| agent | model, effort | job | report contract |
|---|---|---|---|
| `governor:scout` | haiku, low | look-ups; returns `path:line`, never whole files | `## Findings` with path:line |
| `governor:implementer` | sonnet, medium | implement a written spec, run its tests | worker |
| `governor:senior-implementer` | opus, high | the same, for hard slices | worker |
| `governor:reviewer` | opus, medium | review a diff against its spec | JSON findings with failure scenarios |
| `governor:architect` | fable, high | one structured decision; spawn only with a brief | decision record |
| `py-testing:test-implementer` | sonnet, medium | test slices, four stack skills preloaded | worker |
| `prod-readiness:scanner` | sonnet, medium | run the scan and the installed tools | worker |
| `prod-readiness:auditor` | opus, medium | judge the scan's review rows | JSON findings with failure scenarios |

The **worker contract**, enforced by a hook at `SubagentStop`: `## Result`
with DONE, PARTIAL or BLOCKED; `## Changed files`; `## Evidence` with each
command on a `$ ` line and its output, and every such command must appear
as a real Bash call in the worker's transcript. A report missing any of it
is sent back (twice at most).

**Scripts** (the deterministic steps; the model invokes, never re-derives):

| command | does |
|---|---|
| `governor.py status` / `budget show|set|history` | the ledger and the budget |
| `governor.py check-report FILE --contract worker` | the contract check as a command |
| `governor.py brief check FILE` / `brief template` | the task-brief lint (headings, one-line task, checkable done items, evidence command, procedure) and the format it fills; the brief skill runs both |
| `governor.py mode [show\|explore\|enforce\|observe] [--user\|--project]` | the mode, per project in the user's file; a project file may only set enforce |
| `governor.py plan build slices.json` / `plan check plan.json` | slices to levels; refuses cycles and same-level file conflicts |
| `governor.py run-worker --spec FILE --agent governor:implementer --budget 2` | one slice, headless, under `--max-budget-usd`, report checked; prints one `VERDICT:` line |
| `governor.py run-level PLAN.json --level N [--parallel K] [--retries N]` / `runs [PLAN]` | every slice of a level as a supervised headless worker: worktree each, retry on overload, resumable index, one `VERDICT:` line per slice |
| `governor.py statusline-snippet` | the settings fragment for the status line |
| `inventory.py tests [--json] [--diff before.json]` | test-suite facts; the diff is the integration evidence |
| `readiness.py [ROOT] --tier precommit|release [--only id,id] [--json]` | the categorized scan; full report in `.readiness/report.json` |
| `scripts/dist.sh`, `scripts/validate.sh`, `scripts/audit-deps.sh` | zips, strict validation, dependency audit |

`governor.py` lives at `plugins/governor/bin/`, `inventory.py` under
`plugins/py-testing/skills/untangling-test-suites/scripts/`, `readiness.py`
under `plugins/prod-readiness/skills/readiness-review/scripts/`. Inside a
skill, `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_SKILL_DIR}` resolve them.

**Configuration**: `~/.claude/governor.json` for the user (per-project
entries under `"projects": {"/abs/path": {...}}`), `.claude/governor.json`
in a project, which may only tighten. Keys and defaults, each with the
reason for its value, are `DEFAULTS` in `governor.py`. The scanner reads an
optional `.readiness.json` at the scanned root; its own `disable` list is
honoured only when passed with `--config`.

**State**: `~/.claude/plugins/data/<plugin>/` (or `~/.cache/governor`):
per-session ledgers, `history.jsonl`, `errors.log`; the scanner writes
`.readiness/` into the scanned repository. Add `.governor/` and
`.readiness/` to `.gitignore` in projects that use them.

## governor in one minute

- A task starts with `/governor:brief`: at most five questions, then
  `.governor/brief.md` in one fixed format, linted by a script before it is
  handed off; the triage that follows works from it.
- A spawn that names no model is pinned to `sonnet` (configurable) instead
  of inheriting Fable; the session's own permission rules still apply. A
  `fork` is denied. A spawn *onto* Fable is allowed only with a brief
  carrying `## Question`, `## Context`, `## Definition of done`, and at most
  three per session.
- When expensive-tier spend reaches the budget (default 15 USD at API list
  price, subagents included), tool calls are denied with a reason; spawning
  a cheap worker stays allowed. `/model opus` keeps the context and lifts
  the gate; `/governor:budget set 25` raises it. A budget of 0 is a closed
  gate, never an open one.
- Three modes. `enforce` (default) as above. `observe` prices everything
  and refuses nothing. `explore`, for a question that is not yet a task:
  workers still pinned and forks denied, report contracts off, and the
  budget a one-time checkpoint (ship, spike or drop) instead of a wall;
  `/governor:explore` switches to it, `/governor:brief` switches back.
- Two ways to run it. **Fable conducts**: the session is Fable and the hooks
  keep it from doing the cheap work. **Fable consults**: the session is Opus
  or Sonnet and `governor:architect` is Fable, called with a brief for the
  one decision that needs it; cheaper by construction.

Prices are in `plugins/governor/bin/pricing.json` with the date checked; a
model missing from the table is charged at the top rate and flagged.

## py-testing in one minute

Five sourced skills (each ends with its sources and fetch date, and says
where the docs were silent), `scripts/inventory.py` for the facts a
decomposition needs (shadowed fixtures, duplicated tests, unregistered
markers, shared fixtures, a first cut of slices with one command each), and
`py-testing:test-implementer`, a Sonnet worker with the four stack skills
preloaded.

## prod-readiness in one minute

```
readiness.py --tier precommit   # archive hygiene, custom credential and identifier shapes, HTML sinks
readiness.py --tier release     # all 19 checks plus whichever of gitleaks, pip-audit, bandit, semgrep, osv-scanner, trivy, npm audit, lychee is on PATH
```

Stdout is a bounded table (`pass` / `fail` / `review` / `skip` per check);
findings are `path:line` and a rule name, never the matched text, and every
string that comes from the scanned repository is sanitized and marked
(`cfg:`, `<tool>:`). The workflow: `prod-readiness:scanner` runs it,
`prod-readiness:auditor` judges the `review` rows, the conductor reads two
tables. Checks that need running code (a create-then-poll flow that fails
silently, a vendor key mode) are written as tests in the references and
delegated as slices. External tools are documented with install-from-
maintainer, pin-and-checksum guidance and are never installed by the plugin.

## Development

```
bash scripts/validate.sh                              # strict manifest, skill and agent validation
bash scripts/audit-deps.sh                            # stdlib-only imports, no network code, what CI pulls
uv run --with pytest python -m pytest -q plugins      # hook engine, inventory and scanner tests
uv run --python 3.9 --with pytest python -m pytest -q plugins   # the floor the scripts promise
bash scripts/dist.sh                                  # zips for machines with no git access
claude --plugin-dir ./plugins/governor                # try a plugin without installing
GOVERNOR_DEBUG=1 claude ...                           # record hook input shapes (redacted) to the state dir
```

CI runs the first three plus the org's review gate, proprietary scan and
title lint. `evals/` under each plugin holds `claude plugin eval` cases in
the early-access layout; run them with `claude plugin eval plugins/<name>
--runs 1 --model sonnet --judge-model haiku --max-cost-usd 5 --no-publish`
once the feature is enabled for the account.

`CLAUDE.md` at the root carries the rules for working in this repository.
`docs/ARCHITECTURE.md` is the design and its verification record;
`docs/COST-TRACKING.md` is the cost runbook and the supply-chain statement.

## Repository map

```
.claude-plugin/marketplace.json        the marketplace (konyklabs-plugins)
plugins/governor/                      bin/governor.py, hooks/hooks.json, agents/, skills/, tests/, evals/
plugins/py-testing/                    skills/ (5, with references and scripts/inventory.py), agents/, tests/, evals/
plugins/prod-readiness/                skills/ (3, with references and scripts/readiness.py), agents/, tests/, evals/
scripts/                               validate.sh, audit-deps.sh, dist.sh
docs/                                  ARCHITECTURE.md, COST-TRACKING.md, PLAYBOOK.md
.github/workflows/                     validate (manifests, audit, hooks on 3.9 and 3.13) plus the org callers
```

## Status

0.1.0, the first set. Driving tasks: konyklabs/roadmap#60 (governor,
py-testing) and #61 (prod-readiness, deterministic helpers).
