---
name: explore
description: Puts the session into supervisor explore mode for a loosely defined question — workers pinned and forks denied, no report contracts, the budget a checkpoint — frames the question in three lines, and routes the outcome to ship, spike or drop. Use when asked to explore, look into, figure out, poke at or think through something that is not yet a task.
argument-hint: [the question in one sentence]
disable-model-invocation: true
allowed-tools: AskUserQuestion Write Bash(python3 *) Bash(mkdir *)
---
<!-- supervisor:arm explore -->

# Explore

Rigor attaches to the first push, not to the start of work. Before anything
is pushed, a session may work loosely: no issue, no brief, no spec, no
review. What stays on is what costs nothing: workers pinned to cheap models,
forks denied, spend tracked. This skill switches the supervisor into that mode,
frames the question, and makes sure the session ends with a decision and a
note rather than a trail of half-things.

The cheap way to explore is a session on Opus that consults
`supervisor:architect` for the one or two decisions that need the expensive
model. This skill sets no model itself; the session model is your choice.

## 1. Switch

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" mode explore
mkdir -p .supervisor
```

The mode applies from the next hook call and is written per project in
your own `~/.claude/supervisor.json`; a project file may only set `enforce`.

## 2. Frame, in three lines

- **Question**: `$ARGUMENTS`. If it is empty, one `AskUserQuestion` for it.
- **Checkpoint**: the `budget_usd` from
  `python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" budget show`.
- **Worth keeping if**: one sentence, what a result would have to look like
  to deserve a brief.

Print the three lines and `Write` them to `.supervisor/explore.md`. Ask no
other questions; the exploration answers them.

## 3. Work loosely

Read what is needed, try things, edit directly. A throwaway branch or a
worktree, never a default branch. Spawn `supervisor:scout` and
`supervisor:implementer` by name with plain prompts: no spec, prose answers
are fine, the report contract is off. No issue, no brief, no review yet.

## 4. Checkpoint

When spend reaches the budget the hook denies one tool call, with the
question in its reason, then gets out of the way. Then, or as soon as the
question is answered, one `AskUserQuestion`: **ship**, **spike** or
**drop**, recommended option first, chosen from what was learned.

- **ship**: `python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" mode enforce`,
  then `/supervisor:start <the task in one sentence>`. The interview is short
  because the exploration answered it; the brief is the first durable act.
- **spike**: append to `.supervisor/explore.md`, five lines: what was learned,
  what was rejected and why, the open question. That is the issue body if
  the project tracks work. Stop.
- **drop**: append one line saying why. `mode enforce`. Stop.

## 5. Before the session ends

In every branch: the note in `.supervisor/explore.md` exists, and the mode is
back to `enforce` unless the user said to keep exploring. A session is
scratch; the note is what survives it.

## Why

The conveyor's rigor exists to protect things that are hard to undo. Nothing
before the first push is hard to undo, so applying the full process there
only slows the thinking down; the protections kept in explore mode are the
ones that cost nothing to keep.
