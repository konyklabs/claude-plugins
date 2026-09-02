---
name: decompose
description: Turns a large body of work — an unmerged branch, a messy test suite, a multi-file refactor — into independent slices with a spec each, an order, and a worktree plan, so cheap workers can implement them in parallel. Use when a task touches more than about five files, when a branch is too big to review, or before an unattended run.
---

# Decompose

A large change that arrives as one unit cannot be delegated, cannot be
reviewed, and cannot be merged in pieces when part of it is wrong. Decomposing
it is the one job in the whole flow that pays for the expensive model: the cut
points are where the judgment is. Everything after the cuts is delegation.

## 1. Inventory, without reading it all yourself

Spawn `governor:scout` (several in parallel if the tree is large) for:

- the files the change touches, grouped by directory, with line counts
- the tests that cover each group (`Grep` for imports and fixture names)
- the shared things: fixtures, base classes, config, helpers that more than
  one group depends on
- for a branch: `git diff --stat main...HEAD`, and the commits' subjects

For a test suite, prefer the deterministic inventory script from the
`py-testing` plugin (`untangling-test-suites`) when it is installed: it
returns fixtures, markers, duplicates and imports as JSON without spending a
model on it.

Read the scouts' findings. Do not read the files they cite unless a cut
depends on it.

## 2. Cut

Cluster by coupling, not by directory. Two files that change together are one
slice; two directories that never import each other are two slices even when
they are siblings. Aim for slices of one to five files, each with:

- its own tests, runnable in isolation, named by command
- a definition of done a worker can check without asking
- at most one dependency on another slice

The shared things from the inventory are the first slice, and they land
first; every other slice depends on them and nothing else should.

A slice whose definition of done cannot be written is not a slice, it is a
decision. Make the decision now (it is the expensive model's job) or route it
through /governor:consult if this session is on a cheaper model.

## 3. Order and parallelism, computed

Write the slices as JSON (`.governor/slices.json`: `id`, `files`, `deps`,
`command`, `dod` per slice) and let the script order them:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" plan build .governor/slices.json --name <plan>
```

It computes the levels (a slice runs in the first level where all its
dependencies are done), refuses cycles and unknown dependencies, and refuses
two slices in one level that change the same file, which is the collision
that breaks parallel worktrees. It writes `.governor/plan.md` and
`.governor/plan.json`; `plan check .governor/plan.json` re-validates after an
edit. Slices within a level run in parallel in worktrees (`isolation:
"worktree"` on the spawn, or `run-worker` from a shell loop); levels run in
sequence. The conductor integrates each level before starting the next,
running the full test command once per level, not per slice.

## 4. Specs

One spec per slice, via /governor:delegate's template, saved under
`.governor/specs/`. The **Decisions already made** section is where the
decomposition's reasoning goes: the worker must not re-open a cut.

## 5. The plan file

`.governor/plan.md` is what `plan build` wrote; add the decisions block
under it, and post it as a comment on the driving issue if there is one:

```
# Plan: <name>

Levels:
0. shared-fixtures — tests/conftest.py, tests/_support/db.py — `pytest -q tests/_support`
1. api-orders — tests/api/test_orders*.py — `pytest -q tests/api -k orders`  (parallel)
1. api-payments — tests/api/test_payments*.py — `pytest -q tests/api -k payments`  (parallel)
2. e2e-checkout — tests/e2e/test_checkout.py — `pytest -q tests/e2e`  (needs 1)

Integration check per level: `pytest -q tests`
Decisions: see specs; summary —
- fixture ownership: conftest, not a plugin (one place, no import magic)
- ...
```

The plan is the deliverable of this skill. A session that ends after the plan
has done the expensive part; any session on any model can run the levels.

## Stop and reconsider when

- a slice keeps growing while its spec is written: the cut is wrong
- two slices in one level both need to change the same file: they are one
  slice, or the shared part is a new level-0 slice
- a worker returns BLOCKED on a question the plan should have answered: answer
  it in the plan, not in the chat
