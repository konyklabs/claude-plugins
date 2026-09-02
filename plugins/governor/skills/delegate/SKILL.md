---
name: delegate
description: Hands one slice of work to a cheap worker the right way — a written spec with the files, the definition of done and the tests to run, the matching governor agent, and the check of the returned evidence. Use whenever implementation, test writing, or a look-up is about to happen in an expensive-model session, instead of doing it inline.
---

# Delegate

A worker on Sonnet implements a good spec about as well as the expensive model
would, at a fifth of the price. A worker with a vague prompt produces a wrong
thing at a fifth of the price, and the conductor then pays full price to sort
it out. The spec is where the saving is made or lost.

## 1. Write the spec

Use `references/spec-template.md`. Save it to `.governor/specs/<slug>.md` in
the project (add `.governor/` to `.gitignore` or `.git/info/exclude` once) so
the reviewer can read the same spec and a retry does not start from memory.

Every spec has:

- **Goal**: one sentence, the observable outcome.
- **Files**: the files to change and the files to leave alone. A worker may not
  touch anything outside this list without reporting PARTIAL.
- **Definition of done**: checkable statements. "Tests in `tests/api/` pass
  under `pytest -q tests/api`" is checkable; "API tests are clean" is not.
- **Tests to run**: the exact commands.
- **Decisions already made**: the choices the worker must not re-open, with
  the reason in one line each. This is the section that stops a capable model
  from "improving" the design.
- **Out of scope**: what looks adjacent and is not wanted.

Keep it under a page. A spec that needs more is two slices.

## 2. Pick the worker

From the triage tier:

| tier | agent | note |
|---|---|---|
| look-up | `governor:scout` | returns path:line, never whole files |
| execution | `governor:implementer` | Sonnet, medium effort |
| hard slice | `governor:senior-implementer` | Opus, high effort |
| review | `governor:reviewer` | Opus, medium; findings JSON |

Spawn with `subagent_type` set to the agent and **no `model` argument**; the
agent pins its own model and effort. For slices that run in parallel, pass
`isolation: "worktree"` so each has its own checkout; the conductor merges.
Never spawn `fork`; the hook denies it, and it would carry the whole context
onto the expensive model.

The prompt to the worker is short: the path of the spec, the spec's goal
restated in one line, and the sentence "Report in the format your definition
requires." Do not paste the codebase into the prompt; the worker reads.

## 3. Read the evidence, not the prose

When the worker returns:

1. `## Result` first. DONE, PARTIAL or BLOCKED.
2. `## Evidence`: is there a command, is there its output, does the output
   prove the definition of done? A summary line is proof; "tests pass" is not.
   The hook already sent back reports with no evidence block; it cannot check
   that the evidence is the right evidence. That is this step.
3. `## Changed files` against the spec's file list. A file outside the list is
   a finding even when the change looks right.

PARTIAL or BLOCKED means a question came back. Answer the question here (that
is the expensive model's job) and re-delegate with the spec amended. Do not
finish the slice inline: it is the most expensive way to finish it, and it
leaves no spec for the reviewer.

## 4. Review with a different model

For any slice that changes behaviour, spawn `governor:reviewer` with the spec
path and the instruction to review `git diff` (or the worktree) against it.
Read its JSON: `blocking` findings go back to the worker as a spec amendment;
`minor` and `nit` are the conductor's call. Do not re-review the diff yourself
unless the reviewer's findings contradict the evidence.

## 5. Record

One line per slice somewhere durable (the driving issue, a plan file, a
commit message): slice, worker, result, evidence command. A session is
scratch; the record is what survives it.
