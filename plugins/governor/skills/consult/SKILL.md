---
name: consult
description: Asks the expensive-tier architect one structured question — a decision between named options, a decomposition, a diagnosis a cheaper model failed at twice — with the brief the governor hook requires (Question, Context, Definition of done). Use from a session on a cheaper model when a decision is above its pay grade; the count per session is capped.
---

# Consult

The cheapest way to use the expensive model is as a consultant: a session on
Opus or Sonnet does the reading, the running and the delegating, and calls
`governor:architect` for the one question that needs it. The architect sees a
brief, not a session, so it costs a few thousand tokens instead of a few
hundred thousand.

The hook denies a spawn onto the expensive tier unless the prompt carries the
three headings below, is under the size cap, and the session has consults
left. This is on purpose: if the brief cannot be written, the question is not
ready.

## When

- Two or more named options, and the choice is hard to reverse.
- A decomposition (see /governor:decompose) where the cut points are unclear.
- A diagnosis that has failed twice on the current model with genuinely
  different attempts.

Not for: anything with a spec, anything a scout can look up, a second opinion
on work that already has a reviewer verdict.

## The brief

```
## Question
One sentence, a question mark at the end. Name the options if there are
options.

## Context
- what is known: path:line references, the failing output, the constraint
- what was tried and why it did not settle it
- the files that bound the answer (the architect reads these, so point, do
  not paste)

## Definition of done
What the answer must contain to be usable: a choice with the deciding
reason, the rejected options, the consequences, and (if asked) slices with
files and tests.
```

Under 8000 characters. Point at files; the architect has Read, Grep and Glob.

## Spawn

`subagent_type: "governor:architect"`, no `model` argument (the agent pins
`fable`), the brief as the prompt. Wait for it; do not start the work in
parallel on a guessed answer.

## After

Turn `## Decision` and `## Slices` into specs via /governor:delegate. Write
the decision somewhere durable (an ADR, the driving issue, the plan file)
before the next step: the consult was expensive precisely because its output
is meant to outlive the session.

If the answer came back with `## Needs`, supply what it asked for in a second
brief that includes the first answer. That is two of the session's consults;
plan the first brief accordingly.
