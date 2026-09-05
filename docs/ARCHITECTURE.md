# Architecture

What this repository is, why it is shaped this way, what is enforced in code
versus advised in prose, and what was verified against what. Driving task:
konyklabs/roadmap#60. Spec facts below were fetched from code.claude.com on
2026-09-02 against Claude Code 2.1.258; anything observed rather than
documented says so.

## The problem

A Claude Code session on Fable pays 10 USD per million input tokens and 50
per million output, twice Opus and five times Sonnet. The expensive tokens
are rarely the hard decisions. They are:

1. **Tool output read by the conductor.** Every `Read`, every `Bash` result,
   every subagent report flows through the session's context at the
   expensive rate, and a long implementation loop is mostly that.
2. **Spawns that inherit.** A subagent with no model pinned runs on the
   session's model. A subagent with no effort pinned runs at the session's
   effort. Observed: a Sonnet worker spawned from a Haiku session ran at
   `xhigh`, the user's saved default. A `fork` copies the entire context onto
   the parent model.
3. **Workers that report prose.** A worker that says "tests pass" costs the
   conductor a re-run to find out, at the expensive rate.

The upstream request for a `PreAgentSpawn` hook that would route spawns by
cost (anthropics/claude-code#55144) is closed as not planned, and no plugin
found in the official or community marketplaces enforces a model choice or a
budget; the closest are observability hooks. So the guardrails are built
here, from the hook events that do exist.

## Two plugins, not one

`supervisor` is generic: it knows about models, spend and contracts, nothing
about Python. `py-testing` is a domain: it knows about pytest, Playwright and
SQLAlchemy, and reuses the supervisor's report contract for its worker. The
next domain (TypeScript, a framework, a vendor API) becomes a third plugin
beside `py-testing` without touching the supervisor. Each installs on its own.

## supervisor: what is enforced, and how

Everything below is a hook in `hooks/hooks.json` calling one stdlib Python
file, `bin/supervisor.py`, with the hook's JSON on stdin. Python because the
decisions need JSON, regexes and a small state file, and because a bash
implementation of the same logic would not be testable. Stdlib only so the
plugin has no install step.

| guardrail | event | mechanism | verified |
|---|---|---|---|
| A model-less spawn is pinned to the worker model | `PreToolUse` on `Agent` | `hookSpecificOutput.updatedInput` rewrites `model`, with no permission decision so the session's own rules still apply | real session: `general-purpose` spawn rewritten, subagent transcript shows `claude-sonnet-5`, with and without a `permissionDecision` |
| `fork` is denied | `PreToolUse` on `Agent` | `permissionDecision: deny` | real session: denied with reason |
| A spawn onto the expensive tier needs a brief | `PreToolUse` on `Agent` | deny unless the prompt has `## Question`, `## Context`, `## Definition of done`, is under 8000 chars, and the session has consults left (3) | unit tests |
| Budget gate | `PreToolUse` on every tool | ledger from the transcript; deny when expensive-tier spend ≥ budget and the caller's own model is expensive; a spawn onto a cheap worker stays allowed; a budget of 0 is a closed gate | unit tests; ledger priced a real session |
| Worker report contract | `SubagentStop` | `decision: block` with the missing sections, at most twice per agent; every `$` command in the evidence must appear as a Bash call in the worker's transcript | unit tests; real session: a compliant report passed without a block |
| Policy and spend in context | `SessionStart`, `UserPromptSubmit` | `additionalContext` | real session: state written, readout generated |
| Session history | `SessionEnd` | append to `history.jsonl` | unit tests |

Agent definitions carry `model:` and `effort:`; both fields are documented
and effort was verified in a real session (`supervisor:implementer` ran on
`claude-sonnet-5` at `medium` while the session default was `xhigh`).

### Configuration is tighten-only from a project

Three files: the user's `~/.claude/supervisor.json` (with per-project entries
under `projects`), the project's `.claude/supervisor.json`, and
`$SUPERVISOR_CONFIG`. A project file may only tighten the guardrail keys
(lower the budget, forbid forks, add expensive models, keep enforcement
on); a loosening is ignored and reported in the readout and in
`budget show`. The reason is the obvious attack: a cloned repository ships
a config that turns the supervisor off for whoever opens it. Every value is
type-checked, and a bad value falls back to the previous one rather than
crashing the hook into silence. `mode: observe` and `readout: off` exist for
tracking without interference and are user-level decisions for the same
reason.

### Concurrency

Every tool call from every worker is its own hook process, and they run at
the same time. The ledger takes an exclusive `flock` on a per-session lock
file from load to save (bounded wait, then skip the write rather than stall
the session), and writes through a temp file named by pid, so counters such
as the consult cap are not lost and the state file cannot be torn.

### The ledger

Claude Code writes one JSONL line per content block, repeating the message's
`usage` on each line, so the ledger deduplicates on `message.id`. Subagent
transcripts live in `<session>/subagents/agent-<id>.jsonl` and are read the
same way, attributed to the agent id. Cache writes are split into the two
TTL tiers the transcript reports (`cache_creation.ephemeral_5m_input_tokens`
and `_1h_`), because they are priced differently. Parsing is incremental: a
byte offset per file, whole lines only, so the `PreToolUse` hook, which runs
before every tool call, parses only what was appended since the last call.

The transcript schema is not documented. It was observed on 2.1.258 and the
tests synthesise exactly that shape. If a future version changes it, the
ledger degrades to zero spend and the gate stays open; the hook never fails
closed, because a broken guardrail must not lock the user out of their own
session. Errors go to `errors.log` in the state dir.

State lives in `${CLAUDE_PLUGIN_DATA}` (survives plugin updates; documented),
passed as `--state-dir` from `hooks.json`, with `~/.cache/supervisor` as the
fallback. Verified: a `--plugin-dir` session wrote to
`~/.claude/plugins/data/supervisor-inline/`.

### Prices

`bin/pricing.json`, USD per million tokens at Anthropic first-party list
rates, with the date checked. On a subscription the dollars are notional;
the ratios between models are what the budget expresses. The gate counts
only expensive-tier spend, so a Fable session that delegates well can run
long: the workers' Sonnet spend does not consume the budget.

### What is advised, not enforced

The hooks cannot make the conductor delegate. They make the cheap path
cheaper than the expensive path (a worker spawn costs one line; a fork is
refused; a brief-less consult is refused) and they make the spend visible
every turn. The skills carry the rest: `triage` says which tier a task
deserves, `delegate` says how a spec is written and evidence read,
`decompose` says how a large change becomes levels of slices. A plugin
cannot ship standing rules (no `rules/`, no CLAUDE.md; documented), so the
policy arrives through the SessionStart hook as context, and it is short.

### The brief: interview, then lint

The flow above is only as good as the brief it starts from, and a
one-sentence prompt is not a brief. `/supervisor:brief` closes the gap in one
turn: at most five structured questions (`AskUserQuestion`, one round at a
time, the recommended option first), then `.supervisor/brief.md` in the one
format the rest of the flow reads (`skills/brief/references/brief-template.md`:
task, definition of done, evidence command, out of scope, decisions already
made, assumptions, procedure), then `supervisor.py brief check` on the file.

Design choices, each with its reason:

- **A skill, not a hook.** `updatedInput` exists only for tool events; a
  `UserPromptSubmit` hook can add context but cannot rewrite the prompt into
  a brief, and it cannot ask a question.
- **In the main session, not a fork.** `AskUserQuestion` is not available
  inside subagents; the interview has to run where the user is.
- **`model: sonnet` for the turn.** The rubric is fixed and the user supplies
  the judgment, so the interview is execution, not decision. The override
  lasts for the invoking turn; the triage on the next prompt runs on the
  session model.
- **Five questions, one round at a time.** spec-kit's `clarify` caps at
  five; the Ambig-SWE finding is that engagement drops after about three
  clarification turns. Gaps that were not asked about are written as
  assumptions, one line each: a brief that hides its guesses is the failure
  mode, and the user strikes a line faster than they answer a question.
- **The lint is a script with a reason per rule.** Required headings; a
  one-line task under 240 characters; at least two done items, each
  checkable (a backtick, a digit, a path, or a word such as "exits 0",
  "zero", "listed"); no vague words ("better", "properly", "as needed") in
  the task or the done items; at least one `$` command under `## Evidence`,
  the same contract the worker report uses; a procedure that runs
  `/supervisor:triage` and never names `general-purpose` (pinned to Sonnet but
  inheriting the session's effort, observed 2026-09-02); and the same
  8000-character cap as the consult brief. The template does not pass its
  own check, so an unfilled brief cannot be handed off.

What the lint cannot do: judge whether the evidence command is the right
evidence, whether the out-of-scope list is complete, or whether an
assumption is true. It refuses the brief shapes that predictably waste a
worker; the user reads the printed brief for the rest.

### Three operating modes

- **Fable conducts.** The session is Fable; the hooks stop it from doing the
  cheap work. Right for design sessions where the value is in the thinking
  and the tool output is small.
- **Fable consults.** The session is Opus or Sonnet; `supervisor:architect` is
  Fable with `Read`, `Grep`, `Glob` and a brief. Right for long
  implementation runs. This is the cheaper mode by construction: the
  expensive model sees a brief instead of a transcript. The `consult` skill
  and the brief enforcement exist for it.

For unattended runs, Claude Code's own `--max-budget-usd` (print mode only,
documented) is the hard cap; the supervisor's gate is the interactive
equivalent and the ledger works in both. In the consult mode the gate still
binds: the architect's own spend is expensive-tier spend, and its tool calls
are gated on its own model, so a runaway consult closes on itself.

The third mode, `explore`, exists because rigor attaches to the first
push, not to the start of work. For a loosely defined question the session
keeps the protections that cost nothing (workers pinned, forks denied, spend
tracked and read out), drops the report contracts (prose is what exploration
produces), and turns the budget gate into a checkpoint: at the number the
hook denies exactly one tool call, with "ship, spike or drop" in the reason,
sets `explore_checkpoint` in the ledger, and does not deny again. A wall had
blocked its own escape hatch in the field; a checkpoint hands the decision to
the human and gets out of the way. `supervisor.py mode` writes the mode the
way `budget set` writes a budget: per project in the user's file, or at the
user's top level; a project file may only set `enforce`. `/supervisor:explore`
frames the question and routes the exit; `/supervisor:brief` switches back to
`enforce` as its first step, because a brief is the first durable act.

### Supervised headless workers

Checked against the docs on 2026-09-03: a hand-spawned subagent that hits
an API error is marked failed and must be resumed by hand; a headless
`claude -p` run retries retryable errors itself and emits `api_retry`
events; fan-out is documented as a loop over `claude -p` with
`--allowedTools` and worktrees; and Anthropic's own multi-agent system
passes lightweight references back to the orchestrator and resumes from
checkpoints rather than restarting. `run-level` is that shape in one
command: one `run-worker` per slice of a plan level, in its own worktree
(`.supervisor/wt/<plan>/<id>`, branch `<plan>/<id>`; namespaced by plan so two
plans that reuse a slice id never share a checkout, and an existing directory
is reused only when it is on that branch), at most `LEVEL_PARALLEL` at
once, up to `LEVEL_RETRIES` outer retries with doubling backoff when the
process died on something `TRANSIENT_RE` recognises (overload, rate limit,
5xx, connection reset), and never a retry on anything else, because that
would spend the budget on the same failure; a retry runs under the budget
remaining for the slice within that invocation, never a fresh cap, so one
invocation can cost a slice at most its cap; an attempt whose cost the CLI
did not report (a timeout, unreadable output) is charged the whole cap it ran
under and marked `cost_assumed`, except a transient death, which happens
before the work and is charged nothing so it can be retried; the allowance
and the cap are per invocation,
the index keeps the cumulative count. Every attempt updates the index under
`.supervisor/runs/<plan>/level-N.json` and writes its own report file; a rerun
skips a DONE slice only while the digest of its spec is unchanged, and `runs`
prints the table, naming an unreadable index rather than hiding it. An
exception inside one slice fails that slice and the level goes on. The first
worktree add appends `.supervisor/` to the repository's `.git/info/exclude`,
so the checkouts never appear as untracked content in the parent. The worker runs with `--output-format json`, so
cost and session id are recorded per slice and a PARTIAL worker can be
continued with `run-worker --resume <session>`. Not `--bare`: bare mode
needs an API key, and this plugin runs on subscription login.

What the conductor sees is one line per slice and a summary; the reports
are files. That is the "lightweight references" rule applied.

The docs also recommend dynamic workflows over hand-spawned subagents for
many-agent work ("dozens to hundreds of agents per run"); the local review
gate as a Workflow is a separate task. `run-level` is for the implementation
levels, where each worker needs a checkout, a budget cap and a report file.

### The spec gate, and where worker dollars show

Field feedback from a real run (2026-09-03): one spec bundled investigation,
an upgrade, a regression check, new tests and quality gates, and the worker
ran out of turns; another asked the worker to find a cache directory the
conductor could have resolved in a second; and two headless workers' spend
never appeared on the budget line. The split rule and the resolve-first rule
were prose. `spec check` makes them a script: an empty spec or one over the
size cap stops the dispatch, and a spec that mixes three or more kinds of
work (a keyword heuristic over the goal, files and definition of done: it
catches the common wordings, not every phrasing), has more than six done
items or five files, or asks the worker to find a value is warned about, by `spec check` itself and by `run-worker` and
`run-level` before they spawn. Headless workers are their own sessions, so
the ledger never sees them; `worker_spend` sums the run indexes under
`.supervisor/runs`; the readout and the status line show it as `workers $`,
and `status` as a `Headless workers` line. `run-level --setup` runs one command in each new worktree,
because a worker without the project's environment fights the editor's
type checker instead of doing the slice.

## Dead workers (2026-09-04)

Measured across every subagent transcript on one machine (22 project
directories, 2026-08-24 to 2026-09-04): 335 subagents, 13 died on an API
error (202 USD of the 3,542 USD all 335 cost at list price), and 6 of those
13 died on a usage limit (178 USD, one agent alone 164 USD). Separately, 61 Sonnet workers ran at `xhigh` effort (162 USD)
because a bare `general-purpose` spawn is pinned to Sonnet by the model-pin
guardrail but still inherits the session's effort — there is no matching pin
for `effort`. The `SubagentStop` report-contract send-back, by contrast, is
not where the spend goes: 3 in total.

Two changes follow from those numbers. A bare `general-purpose` spawn now
routes to a new agent, `supervisor:worker` (Sonnet, medium effort, every tool,
no report contract), closing the effort gap the 61 workers above show for a
spawn that carries no spec to hold a report contract against. And a worker's
death is now read by the hook and reported to the conductor in the same
turn: a transient API error is a retry-once signal (wait, then re-spawn with
the same spec), a usage-limit death is a switch-tier signal (the hook denies
further spawns onto that model for the rest of the session). No hook runs
on a background task notification, so a death that arrives that way is
covered by the policy text instead: the conductor treats the same phrase,
"Agent terminated early due to an API error", the same way. `supervisor.py
status` lists the dead workers.

Field-verified 2026-09-04 on Claude Code 2.1.260: a `PreToolUse`
`updatedInput.subagent_type` rewrite is honoured — a `general-purpose` spawn
ran as `supervisor:scout` on Haiku, shown by both `subagent_stats.by_type` and
the subagent's own transcript.

The advice goes out on both channels a PostToolUse hook has: `hookSpecificOutput.additionalContext`, which the hooks reference describes as "added to Claude's context alongside the tool result" (checked 2026-09-04), and `systemMessage`, which is shown to the person. The first review round of this change had it on `systemMessage` alone, which the model never reads.

Not verified: the `tool_response` shape of a failed foreground `Agent` call.
The hook matches the exact phrase above, only when it opens the response and
the response is one message long, and is a no-op on anything else: a worker
that finished and merely quotes the phrase in its report is not a death.

Known limit: a plan-wide session limit is recorded against the model that was
running, so only that family is denied. When the plan itself is out, the
conductor's own calls fail the same way and the wall is visible without the
guard; recording it against every family would deny the cheap tiers on a
per-model limit, which is the common case.

## py-testing: what it is and is not

Five skills that follow the authoring rules the org already uses (third
person descriptions with the words a person would type, under 150 lines,
references one level deep with a contents list, one home per fact, sources
and fetch dates at the end, silence of the docs stated rather than papered
over). The content was researched from official docs on 2026-09-02; two
things the docs do not say are labelled as this repo's conventions (tiers as
directories) or as maintainer-endorsed rather than documented (the async
savepoint recipe).

The `untangling-test-suites` workflow is the reason the plugin exists: a
large unmerged suite is decomposed from facts a script produced, not from a
model reading three hundred files. `scripts/inventory.py` is stdlib `ast`,
deterministic, tested, and reports the things a decomposition needs
(shadowed fixtures, duplicated tests, unregistered markers, shared fixtures,
a first cut of slices with one command each).

`test-implementer` preloads the four stack skills so a Sonnet worker knows
the recipes without the conductor explaining them, and it is held to the
supervisor's report contract by the same hook.

## prod-readiness: the same shape, applied to security

The third plugin follows the supervisor's economics. The expensive part of a
security review is reading code, so `readiness.py` reads it once and emits
a table: nineteen checks, each `pass`, `fail`, `review` or `skip`, with
`path:line` and a rule name and never the matched text. A Sonnet `scanner`
runs it and the installed external tools and returns the bounded summary;
an Opus `auditor` judges only the `review` rows and answers with failure
scenarios or clears them; the conductor reads two tables. Checks that need
running code (a create-then-poll flow that fails silently, a vendor key
mode) are written as tests in the references and delegated as slices.

The catalogue is twenty-five check classes from a real hardening pass of a
partner-facing API sample app, delivered generalized: no company,
product, vertical, hostname or identifier appears in it or in anything
built from it, and the two config-driven checks (credential and identifier
shapes) ship disabled with placeholders because their real patterns belong
to the organization that owns them. Order is observed impact, so a partial
run covers what mattered most. External scanners run only if already on
PATH and are summarized to counts; the plugin installs nothing and the
dependency audit still passes.

## signoff: reconciling coverage against what the app does

The seam is a Playwright JSON report on one side — `--list --reporter=json`
for what the suite claims to test, a run's `--reporter=json` for what
passed — and a `@tile:` claim on the other: a tag in a test's title, a
`tag:` list entry, or a static `{type: "tile", description: "<id>"}`
annotation passed to `test()`. Each claim is matched to a tile: a mined
rule, a screen rendered for a role, or an error state. `plugins/signoff/formats.md`
is the one home for every id and file format the plugin reads or writes,
decided in konyklabs/roadmap D-009; nothing else restates it.

Two artifact homes, not one. `.qa/*.json` (`map.json`, `routes.json`,
`rules.json`, `tests.json`, `tiles.json`) is working state a script writes
and reads and a person never hand-edits, gitignored the way `.readiness/`
is. `testcases/` is durable, human-authored Markdown, committed the way
source is. The tile is the unit the whole plugin reconciles on: the map,
the mined rules and the listed tests each reduce to tile ids upstream, and
a case, a coverage row and a gap each name one downstream.

Field-tested against Playwright 1.5.x (`plugins/signoff/tests/fixtures/playwright-list.json`
and `playwright-run.json`, captured 2026-09-04): `--list --reporter=json`
emits the run report's shape with every test `skipped` and `results: []`;
`tags` carry no leading `@` (`tile:auth.sign-in.valid-password`, not
`@tile:...`), so a tag matched from `tag:` has to be compared without the
`@` while a tag given in the test's title keeps it, because it never leaves
the title string; `file` is relative to `config.rootDir`, not to the
working directory or the config file's own location; an annotation pushed
at run time (`test.info().annotations.push`) appears only in a run report,
never in a list, so a claim that must be visible before any test has run
has to be a tag or a static annotation, never a runtime one.

Test cases are Markdown, not YAML: the standard library carries no YAML
parser and this plugin adds no dependency to get one, so the fixed skeleton
in `formats.md` is read line by line instead of parsed as structured data.

### What is not yet built

`tiling-coverage` (`tests.py`, `tile.py`), `recording-test-cases`
(`cases.py`) and `signoff-report` (`report.py`) are deterministic scripts
with no judgment call in them, and are fully built — this is signoff build
A, konyklabs/roadmap#120. Three things are not: nothing writes
`.qa/map.json` (`exploring-app`'s `mapcheck.py` validates a map, it does not
produce one), nothing writes `.qa/rules.json` (there is no `mine` skill
directory at all yet, not even a placeholder), and nothing writes test-case
prose from a tile on its own (`recording-test-cases` lints and exports
cases the conductor wrote from a tile). Explore and mine are one build
(build B, konyklabs/roadmap#121). Filling the gaps is the next (build C,
konyklabs/roadmap#122): a spec per uncovered tile handed to a cheap worker
under the supervisor's delegate flow, a TypeScript Playwright skill so the
worker is competent on either stack, and the new test tagged with its tile;
the cases stay with the conductor. The plugin carries no agents yet.

## What was verified in the field, and what was not

Captured with `SUPERVISOR_DEBUG=1` (the engine appends every raw hook input to
`hook-inputs.jsonl` in the state dir) on 2.1.258:

- `transcript_path` is always the session transcript, also on events fired
  inside a subagent; the subagent's own file arrives only as
  `agent_transcript_path` on `SubagentStop`. The engine maps either form to
  the session file regardless.
- `agent_type` for a plugin agent is namespaced: `supervisor:scout`. The
  contract lookup strips the prefix.
- `SubagentStop` carries `last_assistant_message` and `stop_hook_active`;
  the engine prefers the message over re-reading the transcript.
- `effort` and `model` were **absent** from every hook input, although the
  docs list them. The engine takes both from the transcript instead
  (`effort` is recorded on each assistant line; `message.model` on each
  message) and only uses the hook fields when present.
- A `supervisor:scout` report in a real session met its contract
  (`## Findings` with path:line) and passed `SubagentStop` without a block; a
  `supervisor:implementer` report met the worker contract the same way.
- `agent_id` in hook input equals the suffix of the subagent's transcript
  filename (`agent-<id>.jsonl`), which the budget gate relies on to look up
  a worker's own model.
- `updatedInput` on `PreToolUse` takes effect without a `permissionDecision`
  (the rewritten spawn ran on Sonnet), so the rewrite does not approve
  anything.

Not verified:

- `claude plugin eval` is early access and not enabled on the authoring
  account. The eval cases follow the embedded early-access layout and have
  not been run.
- `skills:` preloading in `test-implementer` is documented; whether plugin
  skill names need the `py-testing:` prefix there was not tested. The
  validator accepts the bare names.
- A `SubagentStop` block in the field: every worker spawned during testing
  reported compliantly, so the block path is covered by unit tests only.
- Prices were taken from the Claude API skill's table (cached 2026-06-24)
  and the Fable 5.1 migration notes, not from the live pricing page.
- The brief skill's `model: sonnet` override across the `AskUserQuestion`
  round-trips within one turn: the docs say the override applies to the
  invoking turn and are silent on whether a question round-trip ends it.
- `AskUserQuestion` behaviour in `-p` mode, which is where an eval would
  run the brief skill.
- The `brief-writes-file` eval case, like the others, has not been run.

- The explore checkpoint's deny-once behaviour in a real session: unit
  tests cover the flag and both calls; no session has yet reached its
  budget in explore mode.

- `run-level` under a real API overload: the retry path is covered by a
  fake `claude` that dies with a 529 on its first call; no real overload has
  been observed through it yet. The worktree and index paths were checked
  with a real `claude` on a two-slice plan; the run record is on
  konyklabs/roadmap#97.

## Rejected

- **A CLAUDE.md-style rules file in the plugin.** Not loadable from a plugin.
- **Environment variables instead of the hook** (`CLAUDE_CODE_SUBAGENT_MODEL`,
  `_FORCE`). They work and the engine honours them, but they are per-shell,
  invisible in the session, and cannot express "expensive spawns need a
  brief" or a budget. The hook is per-project, visible every turn, and
  records what it did.
- **Bash hooks.** Not testable at the granularity the budget and contract
  logic needs.
- **One plugin.** Would couple the Python skills to the supervisor's release
  cadence and make the next domain a fork instead of a sibling.
- **Enforcing the report contract on every agent.** Foreign agents (org
  roles, project agents) have their own contracts. Plugin agents arrive
  namespaced (`supervisor:scout`, verified) and are governed when their
  namespace is in `contract_namespaces`; a bare agent type is a project or
  user agent and is governed only if listed in `govern_bare_agents`.
