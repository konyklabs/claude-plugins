---
name: reflect
description: Reviews the corrections, standing rules and preferences the supervisor queued from earlier prompts and writes the accepted ones to auto memory with the reason — the user accepts, edits or drops each line; nothing is written to memory or rules without that review. Use at the end of a session, when asked to reflect, to review learnings, or to stop re-explaining something.
argument-hint: [--all]
disable-model-invocation: true
allowed-tools: AskUserQuestion Read Write Edit Bash(python3 *)
---

# Reflect

The supervisor's UserPromptSubmit hook queues a prompt that reads as a
correction ("No, that's not what I meant"), a standing rule ("from now on")
or a preference ("I'd rather"), together with the assistant text it answered,
in the plugin's state directory, keyed by the project root and never inside
the repository (`learnings show` prints the path). It never writes memory or
rules: the hook
is the capture half, this skill is the review half (roadmap#150). Memory
that no one reviewed is how instruction files triple and contradict
themselves; a queue with a person at the end is how they stay short.

## 1. Show the queue

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" learnings show --state-dir "${CLAUDE_PLUGIN_DATA}"
```

Nothing pending: say so in one line and stop. `$ARGUMENTS` of `--all`
shows handled rows too, for a second look; they are not re-written.

## 2. Draft one memory per row, against what already exists

Read your auto-memory index (the `MEMORY.md` your system prompt names) and,
for each pending row, the memory files whose titles overlap. Then draft, per
row:

- **Kind**: `feedback` for a correction or a preference, `project` for a
  standing rule about this repository, `user` for a fact about the person.
- **The fact**, one sentence, in the user's words where they had them.
- **Why**, one sentence: what went wrong or what it saves. A memory without
  its reason becomes undeletable once the reason is forgotten.
- **Supersedes**: the existing memory this contradicts or refines, if any.
  A contradiction is an edit to that file, never a second file that says the
  opposite.
- **Drop** when the row is a one-off ("no, the other file"), already in
  memory or in a CLAUDE.md, or an instruction the repository's own rules
  already enforce.

## 3. One question round

Ask ONE `AskUserQuestion` with up to four questions, one row each (a second
call for the rest when the queue is longer). Each question shows the draft
fact and why, with options: **accept** (recommended when the draft is
sound), **edit** (free text via Other), **drop**. "Whatever you think" takes
the recommended option for every row.

## 4. Write what was accepted

For each accepted row, write a memory file in the auto-memory directory in
its format: frontmatter with `name` (kebab-case slug), `description` (one
line, used for recall), `metadata.type`; the body with the fact, then
`**Why:**` and `**How to apply:**` lines; `[[links]]` to related memories.
A superseding row edits the existing file instead. Add one index line to
`MEMORY.md` (`- [Title](file.md) — hook`) and keep the index under 200
lines: Claude Code loads only the first 200 lines or 25 KB of it at session
start (code.claude.com/docs/en/memory, fetched 2026-09-27), so a line past
the cap is a memory nobody reads. Near the cap, merge or drop the stalest
line first.

Then mark the rows:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" learnings ack <id> [<id>...] --state-dir "${CLAUDE_PLUGIN_DATA}"
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" learnings ack <id> --as dropped --state-dir "${CLAUDE_PLUGIN_DATA}"
```

## 5. Report

One line per row: id, accepted / accepted with edits / dropped, and the
memory file it became. An edited row is acked as `accepted` (the queue keeps
only accepted and dropped; the edit lives in the memory file). Handled rows stay in the queue for the record; `learnings clear`
removes them once the memory is committed wherever memory lives. The queue
is per project root, so a worktree has its own; run reflect where the
corrections were made.

## What this skill does not do

It does not write CLAUDE.md or `.claude/rules/`; those are the user's files
and a lesson that belongs there is a proposal in the report, not an edit. It
does not run a model over the transcript: the capture is deterministic and
the judgment is the user's. It does not read other projects' queues.
