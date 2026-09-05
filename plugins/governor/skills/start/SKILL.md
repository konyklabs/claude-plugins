---
name: start
description: The one entry point for a task in an expensive-model session — interviews in one batched round, writes the brief, triages into tiers, decomposes when the work is large, picks a budget profile, and presents one plan card for go / adjust / explore. Use when a task arrives as a sentence, when asked to start, kick off, or plan a piece of work, or instead of typing brief, triage and decompose one after another.
argument-hint: [the task in one sentence]
disable-model-invocation: true
allowed-tools: AskUserQuestion Write Bash(python3 *) Bash(mkdir *) Agent Skill
---

# Start

Brief, triage and decompose are three skills because they are three different
judgments. Typing them one after another, with a budget number in front and a
reviewer spawned by hand after, is what made the first field runs feel like a
checklist. This skill runs the three in one turn and stops exactly twice: once
to ask what only the user knows, once to show the plan and take go, adjust or
explore. Everything between the stops is written, not asked.

It runs on the session model on purpose. The brief skill moves its interview
to Sonnet because five separate round-trips on an expensive model are five
re-reads of the context; one batched round is one. Triage and the cut must be
on the session model anyway: they are the judgment the expensive tier is for.

## 1. Argument and mode

`$ARGUMENTS` is the task in one sentence. Run first:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" mode show
mkdir -p .governor
```

If it reports `explore`, run `mode enforce`: a start is the first durable act
and explore ends here. Leave `observe` alone; someone chose it to measure,
and this skill does not change what it was not asked to.

## 2. Ground before asking

If the sentence names a path, a module or an area, spawn ONE `governor:scout`
for path:line facts: the files involved, the tests that cover them, the
command that runs those tests. Do not read the files yourself. If the sentence
names nothing in the tree, skip this step.

## 3. Stop one: the batched round

Rank the five brief gaps (outcome, the check that proves it, the evidence
command, out of scope, decisions already made) by uncertainty times impact, as
the brief skill does. Then ask ONE `AskUserQuestion` call carrying up to four
questions, highest products first, two to four options each, the recommended
option first and marked "(recommended)". If `$ARGUMENTS` was empty, the first
question asks for the task itself.

A second call is allowed only when an answer opened a gap that would make a
worker guess, and it carries one question. Five questions in total is the
cap, as in the brief skill. "Whatever you think" takes the recommended
options. Every gap not asked about becomes one line under `## Assumptions`.

Skip the round entirely when the sentence is already a brief (files, check,
what must not change): write the assumptions and fold the confirmation into
stop two.

## 4. Write and lint the brief

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" brief template
```

Fill the template (its one home is the script; do not reproduce it from
memory), `Write` it to `.governor/brief.md`, then:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" brief check .governor/brief.md
```

Fix each problem it lists and re-run, at most twice. Still NONCOMPLIANT: show
the problems and stop; nothing downstream can use it.

## 5. Triage, in writing

Apply the triage rubric: the expensive tier for what is irreversible,
ambiguous, cross-cutting, already failed twice on a cheaper model, or a
decision rather than code; Opus high for intricate spec-able slices; Sonnet
medium for mechanical spec-able slices; Haiku for look-ups. The test that
settles most rows: if you can write the spec, it is not yours to implement.
Produce the tier table in the triage skill's shape (slice, tier, agent, why).

## 6. Decompose when it is large

If the table has more than one implementation row, or any slice touches more
than about five files, follow the decompose skill: scouts for the inventory,
cuts by coupling, `.governor/slices.json`, then

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" plan build .governor/slices.json --name <plan>
```

which writes `plan.json` and `plan.md` and refuses cycles. A single-slice
task skips this step and says so on the card.

## 7. Pick the budget profile

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" budget show
```

lists the profiles (small, medium, large by default) and the ceiling. Pick by
the table, not by feel:

- **small**: every row Sonnet or Haiku, at most three slices, no decision
  beyond this plan.
- **medium**: any Opus row, four to eight slices, or one decision the session
  must write down.
- **large**: more than eight slices, a consult to the architect, or a
  `run-level` run that will be left unattended.

A profile above the ceiling is clamped by the script; the card says so.

## 8. Stop two: the plan card

Print, in this order and nothing else: the task line and the definition of
done from the brief; the tier table; the plan levels when there are any;
`budget: <profile> ($<value>)` with the one-line reason from step 7; the
files that will be written. Then ONE `AskUserQuestion` with three options:
**go (recommended)**, **adjust**, **explore instead**.

- **go**: run `python3 "${CLAUDE_PLUGIN_ROOT}/bin/governor.py" budget set <profile>`,
  then continue in this turn: invoke the
  delegate skill for the first row that is not "this session". The plan is
  now the conductor's job.
- **adjust**: the user types what changes (via "Other"). Revise the brief, the
  table or the profile, re-lint the brief, and show the card once more. Two
  cards is the cap; after that, save what stands and stop with the
  differences listed.
- **explore instead**: the task was not a task yet. Run `mode explore`, leave
  the brief on disk as a draft, and end with: `Run /governor:explore <the
  question>`.

## 9. Hand off

When the turn ends with a go, the record is `.governor/brief.md`, the tier
table in the transcript, and `plan.json` when it exists. Every one of them is
scratch until it is in a PR body or an issue; the playbook's last step still
applies.

## Sources

Fetched 2026-09-05. `AskUserQuestion` reference: one to four questions per
call, two to four options each, "Other" always available, unavailable inside
subagents. Skill frontmatter: `model:` applies to the invoking turn only,
which is why brief runs on Sonnet and hands the next turn back; this skill
stays on the session model so the triage and the cut are where they belong.
The brief, triage and decompose skills in this plugin: the rubrics above are
theirs, restated in one line each so this file loads without them.
