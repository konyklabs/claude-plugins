---
name: "on"
description: Arms the plugin for this session in enforce mode without a brief, optionally with this session's own budget (a number or a profile name) and a spend reset; use when asked to turn it on, arm it, enable it, give the session a budget, raise the budget, or reset spend.
argument-hint: [usd|profile] [reset]
disable-model-invocation: true
---
<!-- supervisor:arm enforce -->

# On

The hook armed the session the instant this prompt arrived; nothing here does
the arming. Reply with the one line the hook added to this turn's context (it
starts with `supervisor armed` or `supervisor:`), and stop. Do not run the
script to confirm: from a skill it cannot know which session it is in.

From here, the policy text placed in context applies: workers are pinned,
forks are denied, spend is tracked, and the budget gate is live.

## Arguments

`/supervisor:on 50`, `/supervisor:on medium`, `/supervisor:on reset`,
`/supervisor:on large reset`. The hook reads them from the typed command:

- a number or a profile name (`small`, `medium`, `large`) sets **this
  session's** budget, in the ledger, not in any config file. Two sessions
  in one directory can hold different budgets; the personal ceiling still
  caps it. `budget session off` (in `/supervisor:budget`) clears it.
- `reset` counts expensive-tier spend from now: the gate's view moves,
  the totals and the history keep the whole session.
- anything else is named in the banner as ignored; the arm still happens.

Typing the command again with a bigger number is the one-command raise the
deny reason names. A skill's marker never carries a budget; only the typed
command does.
