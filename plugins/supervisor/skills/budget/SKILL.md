---
name: budget
description: Shows this session's priced spend per model and subagent from the supervisor ledger, the tool results that cost the most to read, and sets or raises the expensive-tier budget; also installs the zero-cost status line and switches between off, enforce, observe and explore modes. Use when asked how much has been spent, what the budget is, to raise the budget, to track cost without interfering, or when the supervisor denies a tool call for budget.
---

# Budget

The supervisor prices every assistant message in this session and its subagents
from the transcript (model, input, output, cache writes at both TTLs, cache
reads) at API list rates from `bin/pricing.json`. On a subscription the
dollars are notional, but the ratios are exact and the budget gate uses them.

## Commands

Run exactly these; the plugin root is substituted by Claude Code. They read
local files only and make no network calls.

Spend this session (per model, per subagent, biggest tool results, spawns,
and any config values that were ignored):

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" status
```

Current budget, mode, and where each setting comes from — includes the
named profiles, the ceiling (`none` or a number), and, when in force, the
profile matching the budget or `→ effective <n> (ceiling)` when clamped:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" budget show
```

Raise or lower the expensive-tier budget for this project, by number or by
profile name (`small`, `medium`, `large`, from `budget_profiles`). Written to
your own `~/.claude/supervisor.json` under `projects`, keyed by this project's
path; that is the only place a raise can come from. `--user` sets your
default for every project; `--project` writes `.claude/supervisor.json` here,
which can only lower. A ceiling never changes what is written; it caps
what is in force, and the reply says when the two differ:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" budget set 25
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" budget set medium
```

`budget set 0` closes the gate for the expensive tier; it does not disable it.

This session only, in the ledger rather than any config file, so two
sessions in one directory can hold different budgets and no file is edited.
`off` clears it and the config applies again. The hook appends
`--session <id>` to these calls so they land on this session's ledger, never
on the newest one:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" budget session 50
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" budget session medium
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" budget session off
```

Count expensive-tier spend from now, keeping the totals and the history for
the whole session (the one-shot warning and the explore checkpoint re-arm):

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" budget reset
```

The typed equivalents are `/supervisor:on <usd|profile>` and
`/supervisor:on reset`; `budget show` prints the session line first.

Set or clear the personal ceiling that caps `budget_usd` from any source
(writes to your user file's top level by default, not the per-project entry,
because a ceiling is not a per-project raise):

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" budget ceiling 60
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" budget ceiling off
```

Past sessions:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" budget history
```

Status line (spend visible under the prompt, no context cost); prints the
`settings.json` fragment to merge, does not write settings:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" statusline-snippet
```

## Modes

Two keys in `~/.claude/supervisor.json`:

- `"mode"`: `"off"` (default) is dormant: nothing is applied until the
  user arms the session with `/supervisor:start`, `/supervisor:on` or
  `/supervisor:explore`; `/supervisor:off` disarms. `"enforce"` applies the
  guardrails from the first prompt. `"observe"` keeps
  the ledger and readout but never denies, rewrites or blocks; tracking
  only. `"explore"` is for a loosely defined question: workers still pinned
  and forks denied, report contracts off, and the budget a one-time
  checkpoint (one denied call asking ship, spike or drop) instead of a
  wall. Set it per project with
  `python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" mode off|explore|enforce|observe`
  (`--user` for every project; `--project` may only set enforce); `mode show`
  prints the effective value. `/supervisor:explore` does this for you.
- `"readout": "line" | "start" | "off"` controls what goes into the
  context: a spend line every turn, only at session start, or nothing.

A project's `.claude/supervisor.json` may only tighten; it cannot switch a
user to observe or raise the budget. `docs/COST-TRACKING.md` in the
repository is the runbook.

## Reading the status

- **Subagent effort flagged `inherited session effort?`**: a cheap worker ran
  at `xhigh` or `max` because its definition pinned no effort. Pin one in the
  agent's frontmatter, or use the supervisor agents.
- **Headless workers**: `run-worker` and `run-level` are their own sessions,
  so their dollars are not in the tables above; the line and the readout's
  `workers $` figure sum the run indexes under `.supervisor/runs`.
- **`ended` column, and `Dead workers:`**: each subagent row's `ended` is
  `completed`, `working`, or `died: transient|quota|other` — the same split
  the death hook uses to say whether to retry once or switch tier. Any
  `died` rows are also summed on their own `Dead workers:` line.
- **Tool results by name**: the bytes the conductor read at the expensive
  rate. A large `Read` or `Bash` number is the signal to route look-ups
  through `supervisor:scout`.
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

1. The user types `/supervisor:on <next profile>` or `/supervisor:on <usd>`
   (the deny reason names the next profile under the ceiling, or the
   ceiling command when none fits): this session's budget, one command,
   nothing restarted. `/supervisor:on reset` counts spend from now instead.
2. `/model opus` (or `sonnet`): the ledger sees the new model on the next
   message and the gate lifts. Cheaper models cannot read the expensive
   model's thinking blocks, which is fine; write the state down first.
3. When the user says so in this turn, run `budget session <usd|profile>`
   or `budget reset` above: those two, plus the read-only `budget show`,
   `budget history`, `mode show` and `status`, are the one Bash call the
   closed gate lets through (this plugin's own script, one line, no shell
   operators), so the escape hatch is never behind the wall. `budget set`,
   `budget ceiling` and `mode <x>` write config files and stay gated. Say
   why in the same turn. Never raise or reset on your own initiative; the
   deny reason is for the user.
