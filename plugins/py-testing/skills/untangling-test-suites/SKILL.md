---
name: untangling-test-suites
description: Workflow for a large, messy, or unmerged pytest suite — inventory it with a deterministic script, classify what to keep, merge, rewrite or delete, decide the target layout and fixture ownership, and cut it into slices with specs that cheap workers can port while the suite stays green. Use when a branch carries hundreds of tests nobody can review, when fixtures are duplicated or shadowed, or before a test-suite refactor is delegated.
---

# Untangling a test suite

A big unmerged body of tests is a decomposition problem, not a review
problem: nobody can hold it in their head, so the first move is to get the
facts out of the tree and into a table without spending a model on reading
files. Then the few real decisions get made once, written down, and the
porting is delegated slice by slice.

## 1. Inventory (execute, do not read)

Run the script; it parses every test module and conftest with `ast` and
returns the facts a decomposition needs:

```
python3 "${CLAUDE_SKILL_DIR}/scripts/inventory.py" tests            # markdown
python3 "${CLAUDE_SKILL_DIR}/scripts/inventory.py" tests --json     # for tooling
```

For a branch, run it on both sides and diff the JSON:
`git stash`/`git checkout main -- tests` is not needed; a worktree of `main`
and a second run is cleaner.

It reports: files, tests and parametrized cases; fixtures with scope,
autouse, definition sites and use counts; **fixture names defined in more
than one file** (shadowing); **test names defined in more than one file**
(copy-paste); fixtures never requested; markers and which are unregistered;
the largest files; a first cut of slices by directory with one command each;
and the fixtures shared across slices, which are the level-0 candidates.

Read the report, not the files. Ask `governor:scout` for the specific
path:line facts the report makes you want.

## 2. Classify

For each slice (or each large file), one of four verdicts:

| verdict | signal |
|---|---|
| **keep** | own fixtures, own command, tests name a behaviour, no duplicates |
| **merge** | same test names or same fixtures as another slice; two files test one thing |
| **rewrite** | fixtures shadowed, `wait_for_timeout`, shared mutable session state, tests that cannot fail (no assert, or assert on a factory default) |
| **delete** | duplicates of a kept test, tests of removed behaviour, tests that only exercise the mock |

Write the table. It is the first deliverable, and it is short.

## 3. Decide, once

These are the decisions the slices must not re-open. Make them here, or via
`/governor:consult` if this session is not on the model that should make
them:

- **Layout**: tiers as directories, from `testing-pytest-projects`.
- **Fixture ownership**: which conftest owns `engine`, `session`, `api`,
  `page` state; every shadowed fixture gets one home and the others go.
- **Database recipe**: savepoint fixture from `testing-sqlalchemy`, and
  which database (container, file SQLite).
- **Auth**: one `storage_state` per run from `testing-playwright-browser`.
- **Markers**: the registered list; everything else is deleted or
  registered.
- **Naming**: `test_<unit>_<behaviour>_<condition>`; a test whose name is a
  number or a ticket id gets renamed in its slice.

One paragraph per decision with the rejected option. This block goes into
every slice spec under "Decisions already made".

## 4. Cut and specify

Level 0 is the shared slice: conftests, `_support/`, the database and auth
fixtures. It lands first and alone. Then one slice per directory or per
merge group, each with:

- the files to port and the files to delete
- the target fixtures (by name, from level 0)
- the command that must be green at the end: `pytest -q tests/<slice>`
- the classification verdicts for its files
- "the suite stays green": the slice's command passes before and after;
  a test that was failing before is listed, not silently fixed or deleted

Use `/governor:decompose` for the levels and `/governor:delegate` for the
specs; the worker is `py-testing:test-implementer`, which has the four stack
skills preloaded.

## 5. Integrate

Save the inventory before the work starts
(`inventory.py tests --json > .governor/inventory-before.json`). Per level:
merge, run the full command, then let the script say what changed:

```
python3 "${CLAUDE_SKILL_DIR}/scripts/inventory.py" tests --diff .governor/inventory-before.json
```

Duplicates resolved, shadowed fixtures gone, unregistered markers at zero:
the diff is the evidence that the untangling did what the plan said, and
it costs no model tokens to produce.

## Signs it is going wrong

- A slice's spec keeps growing: it is two slices, or a decision is missing.
- A worker returns PARTIAL because a test "needed" a fixture from another
  slice: the fixture belongs in level 0.
- The deleted count is zero. Large suites always carry dead tests; a plan
  that deletes nothing has not looked.
