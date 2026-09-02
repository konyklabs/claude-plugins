---
name: budget
description: Shows this session's priced spend per model and subagent from the governor ledger, the tool results that cost the most to read, and sets or raises the expensive-tier budget. Use when asked how much has been spent, what the budget is, to raise the budget, or when the governor denies a tool call for budget.
allowed-tools: Bash(python3 *)
---

# Budget

The governor prices every assistant message in this session and its subagents
from the transcript (model, input, output, cache writes at both TTLs, cache
reads) at API list rates from `bin/pricing.json`. On a subscription the
dollars are notional, but the ratios are exact and the budget gate uses them.

## Commands

Run exactly these; the plugin root is substituted by Claude Code.

Spend this session (per model, per subagent, biggest tool results, spawns):

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" status
```

Current budget and where it is configured:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" budget show
```

Raise or lower the expensive-tier budget for this project (writes
`.claude/governor.json`; add `--user` to write `~/.claude/governor.json`):

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" budget set 25
```

Past sessions:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" budget history
```

## Reading the status

- **Subagent effort flagged `inherited session effort?`**: a cheap worker ran
  at `xhigh` or `max` because its definition pinned no effort. Pin one in the
  agent's frontmatter, or use the governor agents.
- **Tool results by name**: the bytes the conductor read at the expensive
  rate. A large `Read` or `Bash` number is the signal to route look-ups
  through `governor:scout`.
- **Spawns marked `rewrite`**: spawns that named no model and were pinned to
  the worker model. Harmless, but the caller should name a model.
- **Spawns marked `deny`**: forks, brief-less expensive spawns, or the
  consult cap. The reason was returned to the caller at the time.

## When the gate has closed

Tool calls are denied with a reason once expensive-tier spend reaches the
budget. Two ways on, both preserving the session's context:

1. `/model opus` (or `sonnet`): the ledger sees the new model on the next
   message and the gate lifts. Cheaper models cannot read the expensive
   model's thinking blocks, which is fine; write the state down first.
2. `budget set <usd>` above, then continue. Say why in the same turn.
