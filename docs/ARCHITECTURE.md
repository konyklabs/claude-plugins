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

`governor` is generic: it knows about models, spend and contracts, nothing
about Python. `py-testing` is a domain: it knows about pytest, Playwright and
SQLAlchemy, and reuses the governor's report contract for its worker. The
next domain (TypeScript, a framework, a vendor API) becomes a third plugin
beside `py-testing` without touching the governor. Each installs on its own.

## governor: what is enforced, and how

Everything below is a hook in `hooks/hooks.json` calling one stdlib Python
file, `bin/governor.py`, with the hook's JSON on stdin. Python because the
decisions need JSON, regexes and a small state file, and because a bash
implementation of the same logic would not be testable. Stdlib only so the
plugin has no install step.

| guardrail | event | mechanism | verified |
|---|---|---|---|
| A model-less spawn is pinned to the worker model | `PreToolUse` on `Agent` | `hookSpecificOutput.updatedInput` rewrites `model` | real session: `general-purpose` spawn rewritten, subagent transcript shows `claude-sonnet-5` |
| `fork` is denied | `PreToolUse` on `Agent` | `permissionDecision: deny` | real session: denied with reason |
| A spawn onto the expensive tier needs a brief | `PreToolUse` on `Agent` | deny unless the prompt has `## Question`, `## Context`, `## Definition of done`, is under 8000 chars, and the session has consults left (3) | unit tests |
| Budget gate | `PreToolUse` on every tool | ledger from the transcript; deny when expensive-tier spend ≥ budget and the caller's own model is expensive | unit tests; ledger priced a real session |
| Worker report contract | `SubagentStop` | `decision: block` with the missing sections, at most twice per agent | unit tests; real session: a compliant report passed without a block |
| Policy and spend in context | `SessionStart`, `UserPromptSubmit` | `additionalContext` | real session: state written, readout generated |
| Session history | `SessionEnd` | append to `history.jsonl` | unit tests |

Agent definitions carry `model:` and `effort:`; both fields are documented
and effort was verified in a real session (`governor:implementer` ran on
`claude-sonnet-5` at `medium` while the session default was `xhigh`).

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
passed as `--state-dir` from `hooks.json`, with `~/.cache/governor` as the
fallback. Verified: a `--plugin-dir` session wrote to
`~/.claude/plugins/data/governor-inline/`.

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

### Two operating modes

- **Fable conducts.** The session is Fable; the hooks stop it from doing the
  cheap work. Right for design sessions where the value is in the thinking
  and the tool output is small.
- **Fable consults.** The session is Opus or Sonnet; `governor:architect` is
  Fable with `Read`, `Grep`, `Glob` and a brief. Right for long
  implementation runs. This is the cheaper mode by construction: the
  expensive model sees a brief instead of a transcript. The `consult` skill
  and the brief enforcement exist for it.

For unattended runs, Claude Code's own `--max-budget-usd` (print mode only,
documented) is the hard cap; the governor's gate is the interactive
equivalent and the ledger works in both.

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
governor's report contract by the same hook.

## What was verified in the field, and what was not

Captured with `GOVERNOR_DEBUG=1` (the engine appends every raw hook input to
`hook-inputs.jsonl` in the state dir) on 2.1.258:

- `transcript_path` is always the session transcript, also on events fired
  inside a subagent; the subagent's own file arrives only as
  `agent_transcript_path` on `SubagentStop`. The engine maps either form to
  the session file regardless.
- `agent_type` for a plugin agent is namespaced: `governor:scout`. The
  contract lookup strips the prefix.
- `SubagentStop` carries `last_assistant_message` and `stop_hook_active`;
  the engine prefers the message over re-reading the transcript.
- `effort` and `model` were **absent** from every hook input, although the
  docs list them. The engine takes both from the transcript instead
  (`effort` is recorded on each assistant line; `message.model` on each
  message) and only uses the hook fields when present.
- A `governor:scout` report in a real session met its contract
  (`## Findings` with path:line) and passed `SubagentStop` without a block; a
  `governor:implementer` report met the worker contract the same way.

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

## Rejected

- **A CLAUDE.md-style rules file in the plugin.** Not loadable from a plugin.
- **Environment variables instead of the hook** (`CLAUDE_CODE_SUBAGENT_MODEL`,
  `_FORCE`). They work and the engine honours them, but they are per-shell,
  invisible in the session, and cannot express "expensive spawns need a
  brief" or a budget. The hook is per-project, visible every turn, and
  records what it did.
- **Bash hooks.** Not testable at the granularity the budget and contract
  logic needs.
- **One plugin.** Would couple the Python skills to the governor's release
  cadence and make the next domain a fork instead of a sibling.
- **Enforcing the report contract on every agent.** Foreign agents (org
  roles, project agents) have their own contracts; the hook applies only to
  the agent types in `report_contracts`, which a project can extend.
