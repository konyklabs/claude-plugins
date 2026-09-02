---
name: triage
description: Sorts a task into the model tier it deserves before any work starts — the expensive tier for decisions and decompositions, Opus for hard slices, Sonnet for execution, Haiku for look-ups — and writes the delegation table. Use at the start of any non-trivial task in an expensive-model session, when asked whether something is worth Fable, or before spawning more than one worker.
---

# Triage

The expensive model's only comparative advantage is judgment: decisions that
are hard to reverse, decompositions where the cut points are not obvious, and
diagnoses that cheaper models have already failed at. Everything else it does
at a premium a cheaper model would do as well. Triage is the ten-line step
that keeps the premium on the judgment.

## The rubric

Score the task on these. One "yes" in the first group is enough to keep it on
the expensive tier; none, and it goes down.

**Expensive tier (this session, or `governor:architect` via /governor:consult)**

- Irreversible or hard to undo: schema, public API, data migration, a
  dependency choice the whole codebase will inherit.
- Ambiguous: two competent engineers would build different things from the
  same request, and the request does not say which.
- Cross-cutting: the right answer requires holding more than about five files
  or two subsystems in mind at once.
- Already failed twice on a cheaper model with genuinely different attempts.
- The output is a decision or a decomposition, not code.

**Opus, high (`governor:senior-implementer`)**

- Spec-able but intricate: concurrency, subtle fixtures, a refactor across
  files that must stay green throughout, a migration with a rollback.
- Sonnet has failed once on it.

**Sonnet, medium (`governor:implementer`)**

- The spec can name the files, the definition of done, and the tests to run.
- Mechanical or pattern-following: add a route like the others, port a test
  to the new fixture, apply a decided rename.

**Haiku, low (`governor:scout`)**

- Where is X, which files touch Y, what does the config say, list the tests
  that use fixture Z.

The test that settles most cases: **if you can write the spec, it is not yours
to implement.** Write the spec and delegate.

## Output

A table, written before any tool call that would start the work:

```
| # | slice | tier | agent | why this tier |
|---|-------|------|-------|---------------|
| 1 | inventory fixtures under tests/ | haiku | governor:scout | look-up |
| 2 | decide fixture ownership (conftest vs plugin) | expensive | this session | ambiguous, cross-cutting |
| 3 | port tests/api/* to the new fixture | sonnet | governor:implementer | mechanical, spec-able |
| 4 | make the savepoint fixture work under xdist | opus | governor:senior-implementer | intricate, must stay green |
```

Then, for each row that is not "this session", go to /governor:delegate. For a
row that is "this session" and produces a decision, write the decision down
(one paragraph, the options rejected, the consequence) before moving on: the
decision is the deliverable, and it must survive the session.

## Signs triage was wrong

- A worker returns BLOCKED with a question: that question was the hard part.
  Answer it here, do not re-implement the slice here.
- A worker returns PARTIAL twice on the same slice: raise the tier once, then
  stop and reconsider the slice boundary.
- The conductor's own turns are long and full of tool output: the tier is
  right but the delegation is wrong. Ask a scout.
