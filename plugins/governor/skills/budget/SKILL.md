---
name: budget
description: Shows this session's priced spend per model and subagent from the governor ledger, the tool results that cost the most to read, and sets or raises the expensive-tier budget; also installs the zero-cost status line and switches between enforce, observe and explore modes. Use when asked how much has been spent, what the budget is, to raise the budget, to track cost without interfering, or when the governor denies a tool call for budget.
---

# Budget

The governor prices every assistant message in this session and its subagents
from the transcript (model, input, output, cache writes at both TTLs, cache
reads) at API list rates from `bin/pricing.json`. On a subscription the
dollars are notional, but the ratios are exact and the budget gate uses them.

## Commands

Run exactly these; the plugin root is substituted by Claude Code. They read
local files only and make no network calls.

Spend this session (per model, per subagent, biggest tool results, spawns,
and any config values that were ignored):

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" status
```

Current budget, mode, and where each setting comes from:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" budget show
```

Raise or lower the expensive-tier budget for this project. Written to your
own `~/.claude/governor.json` under `projects`, keyed by this project's
path; that is the only place a raise can come from. `--user` sets your
default for every project; `--project` writes `.claude/governor.json` here,
which can only lower:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" budget set 25
```

`budget set 0` closes the gate for the expensive tier; it does not disable it.

Past sessions:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" budget history
```

Status line (spend visible under the prompt, no context cost); prints the
`settings.json` fragment to merge, does not write settings:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" statusline-snippet
```

## Modes

Two keys in `~/.claude/governor.json`:

- `"mode"`: `"enforce"` (default) applies the guardrails. `"observe"` keeps
  the ledger and readout but never denies, rewrites or blocks; tracking
  only. `"explore"` is for a loosely defined question: workers still pinned
  and forks denied, report contracts off, and the budget a one-time
  checkpoint (one denied call asking ship, spike or drop) instead of a
  wall. Set it per project with
  `python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" mode explore|enforce|observe`
  (`--user` for every project; `--project` may only set enforce); `mode show`
  prints the effective value. `/governor:explore` does this for you.
- `"readout": "line" | "start" | "off"` controls what goes into the
  context: a spend line every turn, only at session start, or nothing.

A project's `.claude/governor.json` may only tighten; it cannot switch a
user to observe or raise the budget. `docs/COST-TRACKING.md` in the
repository is the runbook.

## Reading the status

- **Subagent effort flagged `inherited session effort?`**: a cheap worker ran
  at `xhigh` or `max` because its definition pinned no effort. Pin one in the
  agent's frontmatter, or use the governor agents.
- **Headless workers**: `run-worker` and `run-level` are their own sessions,
  so their dollars are not in the tables above; the line and the readout's
  `workers $` figure sum the run indexes under `.governor/runs`.
- **`ended` column, and `Dead workers:`**: each subagent row's `ended` is
  `completed`, `working`, or `died: transient|quota|other` — the same split
  the death hook uses to say whether to retry once or switch tier. Any
  `died` rows are also summed on their own `Dead workers:` line.
- **Tool results by name**: the bytes the conductor read at the expensive
  rate. A large `Read` or `Bash` number is the signal to route look-ups
  through `governor:scout`.
- **Spawns marked `rewrite`**: spawns that named no model and were pinned to
  the worker model. Harmless, but the caller should name a model.
- **Spawns marked `deny`**: forks, brief-less expensive spawns, or the
  consult cap. The reason was returned to the caller at the time.
- **Models "charged at the top rate"**: a model id missing from
  `pricing.json`, priced as Fable until the table is updated.
- **Config ignored**: a value a project file tried to loosen, or a value of
  the wrong type; the previous value stayed in force.

## When the gate has closed

Tool calls are denied with a reason once expensive-tier spend reaches the
budget. Spawning cheap workers is still allowed. Two ways on, both
preserving the session's context:

1. `/model opus` (or `sonnet`): the ledger sees the new model on the next
   message and the gate lifts. Cheaper models cannot read the expensive
   model's thinking blocks, which is fine; write the state down first.
2. `budget set <usd>` above, then continue. Say why in the same turn.
