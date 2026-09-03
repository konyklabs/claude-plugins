---
name: brief
description: Interviews the developer about a task with at most five structured questions and writes the brief the governor flow needs — task, checkable definition of done, evidence command, out of scope, decisions, assumptions, procedure — then lints it. Use before any non-trivial task in an expensive-model session, when a prompt is a sentence, or when asked to write a brief, a spec, or a definition of done.
argument-hint: [the task in one sentence]
disable-model-invocation: true
model: sonnet
effort: medium
allowed-tools: AskUserQuestion Write Bash(python3 *) Bash(mkdir *)
---

# Brief

A run is judged against its brief, and a one-sentence prompt is not one. The
sentence says what the user wants; it does not say how anyone will know it
is done, what must not change, or which decisions are already made. Those
are the gaps a worker fills with guesses, and each guess is paid for twice:
once by the worker, once by the conductor sorting it out. This skill closes
the gaps with at most five questions, writes `.governor/brief.md` in the one
format the rest of the flow reads, and lints it with a script.

This turn runs on Sonnet on purpose: the interview follows a fixed rubric and
the user supplies the judgment. The override lasts for the invoking turn and
the session model resumes on the next prompt, so the triage that follows is
not on Sonnet.

## 1. The argument

`$ARGUMENTS` is the task in one sentence. If it is empty, the first question
asks for it (free text, via "Other").

## 2. Skip the interview when the sentence is already a brief

If the sentence names the files, the check that proves it, and what must not
change (a one-line diff, in effect), do not interview. Write the brief from
it, put every field you inferred under `## Assumptions`, one line each, and
ask one confirmation question before saving.

## 3. Ground the questions in the tree

If the task names a path or an area, spawn ONE `governor:scout` before asking
anything, for path:line facts: the files involved, the tests that cover
them, the command that runs those tests. Questions grounded in the tree are
the ones worth asking; ungrounded clarification rounds lose the user after
about three (the Ambig-SWE finding). Do not read files yourself; the scout
returns locations, and the brief points at them.

## 4. Rank the gaps

Five fields: the outcome, the check that proves it, the evidence command,
what is out of scope, the decisions already made. Score each twice:
uncertainty (does the sentence answer it?) and impact (would a wrong guess
waste a worker?). Ask about the highest products only. A field the sentence
answers is not asked about; a field where any answer is fine is assumed.

## 5. Ask

`AskUserQuestion`, one call per round, one or two questions per call, two to
four options each. The recommended option comes first and is marked
"(recommended)"; the user can always type their own. **Hard cap: five
questions in total.** Stop early when every field is answered, when the user
says "done" or "whatever you think", or when the cap is reached. On
"whatever you think", take the recommended option as the answer and move
on. Every gap not asked about becomes one line under `## Assumptions`,
written as the assumption made: the user can strike a line faster than
they could have answered a question.

## 6. Write

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" mode enforce
mkdir -p .governor
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" brief template
```

The first line ends explore mode if it was on: a brief is the first durable
act, and rigor starts there.

Fill the template it prints (the format has one home,
`references/brief-template.md`; do not reproduce it from memory) and `Write`
the result to `.governor/brief.md`. The slug is kebab-case from the task, at
most five words. Every done item must be something a script or a glance can
confirm: a command in backticks, a count, a path, a state that is zero or
green. The evidence block holds the command whose output proves the
definition of done, on a `$ ` line.

## 7. Lint

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" brief check .governor/brief.md
```

`OK` or `NONCOMPLIANT` with one line per problem: a missing heading, a task
that is not one line, a done item nothing can check, a vague word, no
evidence command, a procedure without triage or one naming
`general-purpose`, a brief over the size cap. The procedure must not name
`general-purpose` at all, not even to forbid it: the check is a substring
match, and the plugin agents are the whole list. Fix each problem and
re-run, at most twice. If it is still NONCOMPLIANT, show the problems and stop; do
not hand off a brief the flow cannot use. The lint cannot tell whether the
evidence is the right evidence; that is the user's read of the printed
brief.

## 8. Hand off

Print the brief, then exactly:

> Brief saved to .governor/brief.md. Type /governor:triage to start; the table comes before any work.

Do not start triage in this turn: the turn is on the cheap model on purpose,
and the triage belongs on the session model.

## Sources

Fetched 2026-09-03. Claude Code best practices, "let Claude interview you":
ask the questions before writing the plan. spec-kit `clarify`: five
questions at most, one at a time, ordered by impact times uncertainty.
`AskUserQuestion` reference: one to four questions per call, two to four
options each, "Other" always available; not available inside subagents,
which is why this skill has no `context: fork`. Skill frontmatter: `model:`
applies to the invoking turn. The docs are silent on whether that override
survives the AskUserQuestion round-trips within one turn, and on how
AskUserQuestion behaves in `-p` mode; neither has been verified here.
