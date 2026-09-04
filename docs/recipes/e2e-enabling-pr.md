# Recipe: the first end-to-end enabling PR

*For a run whose outcome is one pull request that makes end-to-end tests
possible: the infrastructure they need runs locally and in CI, and one real
test passes through it. The plan exists and is mostly final; the
infrastructure is where the surprises are. Written 2026-09-04 against
governor 1.5.0; the general flow is in `../PLAYBOOK.md`.*

The rule the whole recipe follows: **you brief the run once, then prompt only
at the boundaries where your judgment is wanted.** The brief carries the
plan; each chat prompt carries the next step and nothing else.

## Before the first prompt

```
claude plugin list                                  # governor, py-testing, prod-readiness enabled, 1.5.0
echo .governor/ >> .git/info/exclude                # once per repository
```

Budget you believe in, in `~/.claude/governor.json`: `{"budget_usd": 25}`.
The default 15 closes the gate mid-decomposition on a task this size.

Start the session on **Opus**, not Fable, and let it consult
`governor:architect` through `/governor:consult` for the two or three
decisions that need the expensive model. Measured 2026-09-04: a Sonnet
conductor wanders (12 turn-cap hits to 5 across 42 paired runs), and a Fable
conductor is the expensive part of the run. Never conduct on Sonnet.

The engine, for commands typed in a shell rather than inside a skill:

```
ENGINE=~/.claude/plugins/cache/konyklabs-plugins/governor/1.5.0/bin/governor.py
```

## Prompt 1: the brief, written by you

The plan is mostly final, so skip the interview. Write `.governor/brief.md`
from the worked example in the playbook and lint it yourself:

```
python3 $ENGINE brief check .governor/brief.md
```

The sections that carry the weight for this task:

- **Task**, one line. Enable end-to-end tests for the service: the
  infrastructure they need runs locally and in CI, and one real test passes
  through it.
- **Definition of done**, checkable only. The e2e command exits 0 locally;
  the CI job exists and is green on the branch; one named test exercises the
  real path; the PR description carries the command output. No "works", no
  "properly".
- **Evidence**: the e2e command on a `$` line, and the CI run URL.
- **Decisions already made**: the plan, one line per decision with the
  rejected option. This section is what stops a capable worker from
  redesigning the infrastructure.
- **Out of scope**: the application code, production infrastructure,
  anything that needs a credential a worker cannot have.
- **Assumptions**: every part of the plan you are taking with a grain of
  salt, one line each. The run will confirm or break each one; you want them
  written before it does.
- **Procedure**:
  - `/governor:triage` first; the table before any tool call
  - scouts for the inventory: what already exists (containers, env files, CI
    config, fixtures, the commands that run them)
  - `/governor:decompose`; **level 0 is the enabling infrastructure**
  - `/governor:delegate` per slice; `governor:senior-implementer` for the
    infrastructure slices (spec-able but intricate is the Opus tier, and
    container, environment and CI wiring is exactly that);
    `governor:reviewer` on every slice
  - **re-cut levels 1 and up after level 0 lands**: this line turns "adjust
    as it moves along" into a rule
  - a BLOCKED or PARTIAL report is answered in the plan or the spec, then
    re-delegated; nothing is finished inline

Then the chat prompt is one line:

> Read .governor/brief.md and run /governor:triage. Show the table before any tool call.

## Prompt 2: veto the triage table

A table of slices, tiers and reasons. Anything infrastructure-shaped on
Sonnet: move it up. Anything that is a decision, assigned to a worker: take
it back. This is the last cheap moment; after it, everything is delegation.

## Prompt 3: the plan, and stop

> Inventory with scouts, then /governor:decompose. Level 0 is the enabling infrastructure. Stop after .governor/plan.md and show it.

The plan is where the grain of salt is applied. Edit
`.governor/slices.json` and rebuild:

```
python3 $ENGINE plan build .governor/slices.json --name e2e-enabling
```

The script refuses cycles, unknown dependencies, and two slices in one level
that change the same file.

## Prompt 4: level 0 only

> Run level 0 with /governor:delegate, senior-implementer for the infrastructure slices, reviewer on each. Integrate, run the full e2e command once, then re-cut the remaining levels from what level 0 taught and show the new plan.

Level 0 is where the infrastructure surprises live. Find them before four
parallel workers build on the wrong assumption.

## Prompts 5 onward: one level at a time

> Run level N.

Or hand a level that needs no back-and-forth to the supervisor, which runs
each slice headless in its own worktree, retries a worker that dies on an
overload, and resumes from its index if rerun:

```
python3 $ENGINE run-level .governor/plan.json --level 1 --setup "<the repository's environment command>"
```

A BLOCKED or PARTIAL report is a question for you. Answer it in the plan or
the spec, not in chat, and let the conductor re-delegate.

If a worker dies, 1.5.0 tells the conductor in the same turn which kind of
death it was and what to do: retry once on a transient API error, never on a
usage limit (that tier is denied for the rest of the session). Keep that
message; it is evidence of the hook working in the field.

## Last prompt: the record

> Write the PR description from .governor/plan.md: the decisions, one line per slice with its evidence command, and the pasted e2e output.

A session is scratch; the PR is the record.

## Do not

- Say "implement the plan" in one prompt. The measured runs are why: the
  without-skill arm spawned a worker and then re-implemented the work itself
  three times out of three.
- Name `general-purpose` anywhere in the procedure. The brief lint refuses
  it; a bare spawn is routed to `governor:worker` by the hook, but a
  procedure that names it is asking for the wrong worker.
- Let a worker "adjust the plan as it goes". Adjustment happens at a level
  boundary, by you, in the plan file.
- Conduct on Sonnet.
