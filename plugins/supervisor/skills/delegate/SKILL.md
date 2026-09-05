---
name: delegate
description: Hands one slice of work to a cheap worker the right way — a written spec with the files, the definition of done and the tests to run, the matching supervisor agent, and the check of the returned evidence. Use whenever implementation, test writing, or a look-up is about to happen in an expensive-model session, instead of doing it inline.
---

# Delegate

A worker on Sonnet implements a good spec about as well as the expensive model
would, at a fifth of the price. A worker with a vague prompt produces a wrong
thing at a fifth of the price, and the conductor then pays full price to sort
it out. The spec is where the saving is made or lost.

## 1. Write the spec

Use `references/spec-template.md`. Save it to `.supervisor/specs/<slug>.md` in
the project (add `.supervisor/` to `.gitignore` or `.git/info/exclude` once) so
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

Keep it under a page. A spec that needs more is two slices. Paths and values
in the spec are exact: a directory, a pin, a config value the conductor can
resolve in one command is resolved before the spec is written, never handed
to the worker as "find X". Then check it before dispatch:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" spec check .supervisor/specs/<slug>.md
```

It refuses a spec over the size cap and warns when a spec mixes three or
more kinds of work, has more than six done items or five files, or asks
the worker to find a value. The kinds rule is a keyword heuristic: it
catches the common wordings, not every phrasing; the split rule still
applies to a spec it lets through. `run-worker` and `run-level` run the same check
and print its warnings as `SPEC WARNING` lines.

## 2. Pick the worker

From the triage tier:

| tier | agent | note |
|---|---|---|
| look-up | `supervisor:scout` | returns path:line, never whole files |
| execution | `supervisor:implementer` | Sonnet, medium effort |
| hard slice | `supervisor:senior-implementer` | Opus, high effort |
| review | `supervisor:reviewer` | Opus, medium; findings JSON |
| general | `supervisor:worker` | Sonnet, medium; anything else, no evidence contract |

Spawn with `subagent_type` set to the agent and **no `model` argument**; the
agent pins its own model and effort. For slices that run in parallel, pass
`isolation: "worktree"` so each has its own checkout; the conductor merges.
Never spawn `fork`; the hook denies it, and it would carry the whole context
onto the expensive model.

The prompt to the worker is short: the path of the spec, the spec's goal
restated in one line, and the sentence "Report in the format your definition
requires." Do not paste the codebase into the prompt; the worker reads.

## 2b. Or run it headlessly, outside your context

For a slice that needs no back-and-forth, do not spend your own context on
the worker at all: run it in print mode under a hard dollar cap, and read
only the verdict line and the report file.

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" run-worker --spec .supervisor/specs/<slug>.md --agent supervisor:implementer --budget 2
```

The runner passes the spec, caps spend with `--max-budget-usd`, allows
only the tools a worker needs (`worker_allowed_tools` in `supervisor.json` to
widen), writes the report to `.supervisor/runs/`, checks it against the
contract, and prints one line: `VERDICT: DONE|PARTIAL|BLOCKED|NONCOMPLIANT
... report=<path>`. Exit 0 only on DONE.

For a whole level of a plan, do not loop by hand; the supervisor does it:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" run-level .supervisor/plan.json --level 1
```

One worker per slice (spec at `.supervisor/specs/<id>.md`; the dollar cap is
per slice across the attempts of one invocation, a retry runs under what is left), each in its own
worktree under `.supervisor/wt/<plan>/<id>` on branch `<plan>/<id>`, a bounded number
at once, a retry with backoff when a worker dies on an API overload, one
`VERDICT:` line per slice and a `LEVEL n:` summary. The index under
`.supervisor/runs/<plan>/` makes a rerun skip the slices already DONE (unless
their spec changed since), and `supervisor.py runs <plan>` prints it. A worker
needs the project's environment in its worktree: pass `--setup "uv sync"`
(or the project's equivalent) and it runs once in each new worktree. A process ends with a verdict or a
timeout; there is no idle worker to lose track of. Read the report files it
names, never the workers' output.

## 3. Read the evidence, not the prose

When the worker returns:

1. `## Result` first. DONE, PARTIAL or BLOCKED.
2. `## Evidence`: is there a command, is there its output, does the output
   prove the definition of done? A summary line is proof; "tests pass" is not.
   The hook already sent back reports with no evidence block; it cannot check
   that the evidence is the right evidence. That is this step.
3. `## Changed files` against the spec's file list. A file outside the list is
   a finding even when the change looks right.

To re-check a report deterministically (a pasted one, or one from
`.supervisor/runs/`), the same check the hook runs is a command:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" check-report <report.md> --contract worker
```

PARTIAL or BLOCKED means a question came back. Answer the question here (that
is the expensive model's job) and re-delegate with the spec amended. Do not
finish the slice inline: it is the most expensive way to finish it, and it
leaves no spec for the reviewer.

## 4. Review with a different model

For any slice that changes behaviour, spawn `supervisor:reviewer` with the spec
path and the instruction to review `git diff` (or the worktree) against it.
Read its JSON: `blocking` findings go back to the worker as a spec amendment;
`minor` and `nit` are the conductor's call. Do not re-review the diff yourself
unless the reviewer's findings contradict the evidence.

## 5. Record

One line per slice somewhere durable (the driving issue, a plan file, a
commit message): slice, worker, result, evidence command. A session is
scratch; the record is what survives it.

## 6. When a worker dies

The hook says which kind. Transient (an API overload): wait about a minute,
re-spawn once with the same spec, then stop and record if it dies again.
Usage limit: the hook denies spawns onto that tier — delegate elsewhere or
wait, never retry into it. Read what the worker left on disk (worktree,
`.supervisor/runs/`) first, and never finish the slice inline; `run-level`
already retries headless workers with backoff on its own.
