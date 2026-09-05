# Cost tracking runbook

How to see what a session costs, with as little interference with the
session as you choose, and what this repository pulls in so it can be used
where dependencies are audited.

## What is in the box

Claude Code has three built-in surfaces and this repository adds one ledger.
None of them needs anything installed beyond Claude Code and `python3`.

| surface | what it shows | context cost | where |
|---|---|---|---|
| `/usage` | the session's per-model tokens and estimated dollars, plan usage attributed by skill, subagent, plugin | none | built in |
| `/insights` | an HTML report of usage over time, written under `~/.claude/usage-data/` | none | built in |
| status line | one line under the prompt, re-run on every event, from `cost.total_cost_usd` and whatever else you print | none | built in, script below |
| `--max-budget-usd N` | a hard dollar cap for a `claude -p` run; subagent spend counts; spawns fail at the cap | none | built in, print mode only |
| supervisor ledger | per-model and per-subagent spend at list price, the two cache-write tiers, the biggest tool results, spawns and their models, effort inheritance | a one-line readout per turn by default; `readout: "off"` for none | this repository |

The supervisor prices from the transcript Claude Code already writes, so it
agrees with `/usage` up to rounding and to the pricing table's freshness. It
adds what `/usage` does not have: the split between the expensive tier and
the workers, per-subagent cost with the effort each ran at, and a budget
that can close the gate.

## Three ways to run the supervisor

Set `mode` and `readout` in `~/.claude/supervisor.json`.

**Observe** (tracking, no interference):

```json
{"mode": "observe", "readout": "off"}
```

The ledger is kept, nothing is denied, rewritten or blocked, nothing is
injected into the context. Read it with `/supervisor:budget`, or put it on
the status line. Use this for a week before turning enforcement on, so the
budget you set is one you have measured.

**Enforce, quiet**:

```json
{"mode": "enforce", "readout": "start"}
```

Guardrails on, the policy injected once at session start, no per-turn
line. The status line carries the spend.

**Enforce** (default): guardrails on, one spend line per turn.

A project's `.claude/supervisor.json` may only tighten these settings; a
repository you clone cannot switch the supervisor to observe or raise your
budget. Your own file, and `$SUPERVISOR_CONFIG`, can do anything. Per-project
raises go in your own file under `"projects": {"/abs/path": {...}}`, which is
what `/supervisor:budget set N` writes.

## The status line

Zero context cost, always visible. Print the settings fragment:

```
python3 "$(claude plugin details supervisor 2>/dev/null | true; echo ~/.claude/plugins/cache/konyklabs-plugins/supervisor/*/bin)/supervisor.py" statusline-snippet
```

or, simpler, from a checkout:

```
python3 plugins/supervisor/bin/supervisor.py statusline-snippet
```

It prints something like:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 \"/Users/you/.claude/plugins/cache/konyklabs-plugins/supervisor/0.1.0/bin/supervisor.py\" statusline",
    "padding": 1
  }
}
```

Merge it into `~/.claude/settings.json`. The line reads:

```
supervisor Fable · fable $3.20/$15 · total $4.11 · spawns 3 · claude $4.09 · ctx 31%
```

`fable` is expensive-tier spend against the budget (`CLOSED` once the gate
has shut), `total` is every model, `claude` is Claude Code's own
`cost.total_cost_usd`, `ctx` the context window used. The command reads
the saved ledger only, so it returns in milliseconds; the hooks keep the
ledger current. After a plugin update the install path changes: re-run the
snippet command.

## Past sessions, and sessions with no supervisor

```
python3 plugins/supervisor/bin/supervisor.py status \
  --session <id> --transcript ~/.claude/projects/<project-dir>/<id>.jsonl
python3 plugins/supervisor/bin/supervisor.py budget history
```

The first prices any transcript, including subagents, whether or not the
supervisor was installed at the time. The second lists the sessions the
supervisor has seen, one line each, with the expensive-tier and total spend.

## What leaves the machine

Nothing. The plugin code makes no network calls; `scripts/audit-deps.sh`
proves it on every CI run by parsing every import (standard library only)
and grepping for network primitives. The ledger holds token counts, model
ids, agent ids, tool names with byte counts, and spawn types; never the
content of a prompt, a file, a command or a message. The debug capture
(`SUPERVISOR_DEBUG=1`) records field names and sizes, not values.

## A work machine

The checklist for a machine where every installed thing is audited:

1. Bring the plugins as zips built by `scripts/dist.sh` (from a committed
   tree, scanned), and load them with `claude --plugin-dir <zip>`. No
   marketplace, no git clone, no network.
2. Start in observe mode (`{"mode": "observe", "readout": "off"}` in
   `~/.claude/supervisor.json`) with the status line. Nothing is denied,
   nothing is injected; the ledger still prices every turn.
3. After a few sessions, `supervisor.py budget history` says what a session
   costs there. Set the budget from that number, switch to enforce.
4. Every file the plugins read or write is listed under "Supply chain";
   the state directory is the only thing they create.

## Supply chain

For a workplace that audits what it installs:

- **Runtime dependencies: none.** Two Python files, standard library only,
  Python 3.9 or newer. No pip, no npm, no downloads at install or run time.
- **What the plugin reads:** the session transcript Claude Code writes under
  `~/.claude/projects/`, the config files named above, and agent definition
  files to learn their pinned model. **What it writes:** its state directory
  (`~/.claude/plugins/data/supervisor*/`, or `~/.cache/supervisor`) and, on
  `budget set`, the config file you named.
- **Claude Code's own pieces used:** hooks (`PreToolUse`, `SubagentStop`,
  `SessionStart`, `UserPromptSubmit`, `SessionEnd`), agent frontmatter
  (`model`, `effort`, `tools`), skills, the status line. All documented at
  code.claude.com; nothing undocumented except the transcript layout, which
  is read defensively and degrades to "unpriced" when it changes.
- **CI pulls:** `actions/checkout`, `actions/setup-node`, `astral-sh/setup-uv`,
  the `@anthropic-ai/claude-code` npm package, and this org's own reusable
  workflows. `scripts/audit-deps.sh` prints the list so a change shows up
  in review.
- **Reading the code** takes about twenty minutes: `plugins/supervisor/bin/supervisor.py`
  (the engine, one file) and `plugins/py-testing/skills/untangling-test-suites/scripts/inventory.py`
  (the test-suite inventory, one file). Everything else is markdown that
  Claude Code loads as instructions.

Alternatives considered and not used: community transcript-cost tools
(`ccusage` and similar) do the same arithmetic with a larger dependency
tree and a Node runtime; a hook that intercepts the API client's `fetch`
would see request bodies. The supervisor reads what is already on disk and
adds nothing.
