# Governor playbook

*How to hand an expensive-model session one high-level problem and let the plugins keep the spend on judgment. Any machine, any repository, nothing else installed.*

## What the governor does to a session

You run the session on the expensive model and it conducts. Five hooks make that cheap enough to be a habit:

- **Spawns are pinned.** An agent whose definition pins no model, such as a bare `general-purpose` spawn, runs on Sonnet instead of inheriting the session's model. The plugin agents pin their own, listed in the tier table below. A `fork` is denied outright.
- **Expensive spawns need a brief.** Sending a question to `governor:architect` (Fable) works only with `## Question`, `## Context` and `## Definition of done` in the prompt, and at most three times per session.
- **Spend is priced and gated.** Every turn, from the transcript, at API list price. At 15 USD of expensive-tier spend, tool calls are denied until you switch model or raise the budget. Cheap spawns stay allowed.
- **Workers cannot stop without evidence.** A report missing its result, changed files or pasted command output is sent back, twice at most.

The rest is skills. They do not fire on their own; you name them in the brief and the model runs them.

## One-time setup on a new machine

```
claude plugin list                 # governor, py-testing, prod-readiness: enabled
claude plugin details governor     # 5 skills, 5 agents, 5 hooks
```

Optional, in `~/.claude/governor.json`. Leave it out and the defaults apply: enforce mode, 15 USD budget, a one-line spend readout per turn.

A higher default budget for every project:

```json
{"budget_usd": 25}
```

Or, for the first day on a machine, price everything and enforce nothing:

```json
{"mode": "observe", "readout": "off"}
```

The file is plain JSON: one object, no comments.

In each repository where the workflow will run, keep its working files out of git once:

```
echo .governor/ >> .git/info/exclude
```

A running `claude` process does not pick up a plugin installed after it started, and `/clear` does not reload plugins. Restart the process after installing.

## Driving a task

### 1. Start fresh, in the repository

The first thing in context is the governor's policy text and a spend line, unless `readout` is `off`, in which case the status line carries the spend instead. If the plugin is not loaded, nothing below is enforced; `claude plugin list` settles it.

```
[governor] expensive-tier $0.00 of $15.00 (out 0 tok, cache-read 0) · total $0.00 · session model unknown · spawns: none
```

The model reads `unknown` until the first reply has been priced; from then on it names the session model.

### 2. Write the brief **(your turn)**

Goal, definition of done as checkable statements, what is out of scope, and the skills by name. This is the one piece of writing the whole run depends on. A worked example for a test-suite restructure is below.

### 3. Read the triage table **(your turn)**

`/governor:triage` makes the model write a table before any tool call: each slice, its tier, its agent, and why. This is where a veto costs nothing. After it, everything is delegation.

### 4. Let it inventory, decide, and cut

Scouts on Haiku return `path:line` facts. For a test suite, the inventory script returns fixtures, duplicates, shadowing and markers as a table with no model in the loop. The model writes its decisions down as one paragraph each with the rejected option, then `/governor:decompose` turns the work into slices and levels, checked by a script that refuses cycles and same-level file collisions.

### 5. Let it delegate, slice by slice

One spec per slice under `.governor/specs/`, then a Sonnet worker (`governor:implementer` or `py-testing:test-implementer`) in its own worktree, then `governor:reviewer` on Opus against the same spec. You read reports, not diffs. Every worker report has the same three sections:

- `## Result` DONE, PARTIAL or BLOCKED. PARTIAL or BLOCKED means a question came back; the conductor answers it and re-delegates.
- `## Changed files` Checked against the spec's file list. A file outside the list is a finding even if the change looks right.
- `## Evidence` The command on a `$` line and its output. The hook verified the command really ran; the conductor verifies it proves the definition of done.

### 6. Integrate per level

The conductor merges a level, runs the full test command once, and for a test suite runs the inventory diff against the saved before-state. That diff is the evidence the restructure did what the plan said.

### 7. Record before the session ends **(your turn)**

The plan file, the decisions and one line per slice go somewhere that outlives the session: a PR description, a plan file committed with the branch, an issue. A session is scratch.

## The tiers the triage sorts into

| tier | agent | what goes here |
|---|---|---|
| **fable** · this session | the conductor | Irreversible or ambiguous decisions, decompositions, a diagnosis two cheaper attempts failed at. The output is a decision, not code. |
| **opus** · high | `governor:senior-implementer` | Spec-able but intricate: concurrency, subtle fixtures, a refactor that must stay green throughout. Sonnet failed once. |
| **sonnet** · medium | `governor:implementer`<br>`py-testing:test-implementer` | The spec names the files, the definition of done and the tests. Mechanical, pattern-following, porting. |
| **opus** · medium | `governor:reviewer` | A diff against its spec. Returns findings as JSON with failure scenarios. |
| **haiku** · low | `governor:scout` | Where is X, which files touch Y, which tests use fixture Z. Returns `path:line`, never whole files. |

The test that settles most rows: if you can write the spec, it is not the expensive model's to implement.

## A worked brief: restructure an end-to-end suite

Paste as the first prompt. Replace the path. Nothing in it is specific to any organisation.

```
Task: assess the state of tests/e2e and make the structural improvements
it needs. Work on a branch named refactor/e2e-structure.

Definition of done:
- shadowed and duplicated fixtures each have one home, the others are gone
- unregistered markers at zero; test names in test_<unit>_<behaviour>_<condition> form
- dead and duplicate tests deleted, listed in the plan with the reason
- `pytest -q tests/e2e` is green before and after every level; a test that
  was failing before is listed, not silently fixed or deleted
- the inventory diff (before vs after) is in the PR description as evidence

Procedure:
- run /governor:triage first and show me the table before any work
- /py-testing:untangling-test-suites for the inventory and the
  keep/merge/rewrite/delete table; save the before-inventory to .governor/
- /governor:decompose for slices and levels; /governor:delegate for each
  slice with py-testing:test-implementer as the worker and governor:reviewer
  on every slice; workers in worktrees
- use the plugin agents by name, never general-purpose
- do not implement anything inline; if a worker returns BLOCKED, answer the
  question and re-delegate

Out of scope: the application code under test, CI configuration.
```

## Spend, and the gate

The readout line is in context every turn. `/governor:budget` shows the full picture: cost per model and per subagent, the tool results that cost the most to read, and the most recent spawns with what the hook did to each. A zero-context alternative is the status line; `governor.py statusline-snippet` prints the settings fragment.

> **When the gate closes** tool calls are denied with the reason, and spawning cheap workers is still allowed. Two ways on, both keeping the context:
>
> `/model opus` lifts the gate on the next message. Write the state down first; a cheaper model cannot read the expensive one's thinking.
>
> `/governor:budget set 25` raises the budget for this project in your own user file. A project's `.claude/governor.json` can only lower it.

Past sessions are in `governor.py budget history`; after a few runs that number, not a guess, sets the budget.

## The cheaper inversion: Fable consults

Start the session on Opus instead. It reads, runs, and delegates the same way, and calls `governor:architect` (pinned to Fable) with a brief through `/governor:consult` for the two or three decisions that need it. The architect sees a brief, not a session, so a decision costs thousands of tokens instead of hundreds of thousands. The worked brief above works unchanged.

## Things that bite

- **A bare `general-purpose` spawn** is pinned to Sonnet but keeps a high effort setting the hook cannot rewrite. The plugin agents pin effort themselves. Say so in the brief.
- **Skills are named, not guessed.** They trigger on description match, which is not reliable enough for a run you will not watch. Name them.
- **An installed plugin is a pinned copy.** It changes only when a new version is installed; a checkout edited in place is not seen.
- **Headless slices need the install path.** Inside a skill, `${CLAUDE_PLUGIN_ROOT}` resolves; from a shell, the engine lives under `~/.claude/plugins/cache/konyklabs-plugins/governor/<version>/bin/governor.py`.
- **Dollars on a subscription are notional.** The ratios are exact and the gate uses them; the absolute number is what the same work would cost at list price.
