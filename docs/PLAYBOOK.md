# Governor playbook

*How to hand an expensive-model session one high-level problem and let the plugins keep the spend on judgment. Any machine, any repository, nothing else installed.*

## What the governor does to a session

You run the session on the expensive model and it conducts. Six hooks make that cheap enough to be a habit:

- **Spawns are pinned.** An agent whose definition pins no model, such as a bare `general-purpose` spawn, runs on Sonnet instead of inheriting the session's model. The plugin agents pin their own, listed in the tier table below. A `fork` is denied outright.
- **A bare spawn gets a worker, not just a pin.** `general-purpose` with no other agent named routes to `governor:worker` (Sonnet, medium effort, every tool, no report contract), so it does not inherit the session's effort the way a pinned-model-only spawn would.
- **A dead worker is not silent.** The hook that reports the death says which kind: retry once on a transient overload, never on a usage limit — the hook denies further spawns onto that model for the session instead.
- **Expensive spawns need a brief.** Sending a question to `governor:architect` (Fable) works only with `## Question`, `## Context` and `## Definition of done` in the prompt, and at most three times per session.
- **Spend is priced and gated.** Every turn, from the transcript, at API list price. At 15 USD of expensive-tier spend, tool calls are denied until you switch model or raise the budget. Cheap spawns stay allowed.
- **Workers cannot stop without evidence.** A report missing its result, changed files or pasted command output is sent back, twice at most.

The rest is skills. They do not fire on their own; you name them in the brief and the model runs them.

## One-time setup on a new machine

```
claude plugin list                 # governor, py-testing, prod-readiness: enabled
claude plugin details governor     # 7 skills, 6 agents, 6 hooks
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

## Not sure what the task is yet?

Rigor attaches to the first push, not to the start of work. For a question
that is not yet a task, start with `/governor:explore <the question>`: it
switches the governor to explore mode (workers still pinned, forks denied,
report contracts off, the budget a one-time checkpoint instead of a wall),
frames the question in three lines in `.governor/explore.md`, and lets you
read, try and edit loosely on a throwaway branch. At the checkpoint, or when
the question is answered, it asks: ship, spike or drop. Ship runs
`/governor:start`, which returns to enforce and starts the sequence below;
spike leaves a five-line note; drop leaves one line saying why.

## Driving a task

### 1. Start fresh, in the repository

The first thing in context is the governor's policy text and a spend line, unless `readout` is `off`, in which case the status line carries the spend instead. If the plugin is not loaded, nothing below is enforced; `claude plugin list` settles it.

```
[governor] expensive-tier $0.00 of $15.00 (out 0 tok, cache-read 0) · total $0.00 · session model unknown · spawns: none
```

The model reads `unknown` until the first reply has been priced; from then on it names the session model.

### 2. Run `/governor:start <the task in one sentence>` **(your turn, two stops)**

One skill runs the brief, the triage and the cut, and stops exactly twice. The
first stop is one screen of up to four questions, each with a recommended
answer first ("whatever you think" takes them all). The second is the plan
card: the task line and its definition of done, the tier table (each slice,
its tier, its agent, why), the plan levels when the work was cut, and the
budget profile it picked with the reason. You answer go, adjust, or explore
instead. On go it sets the budget to the profile and starts delegating in the
same turn; nothing else is typed.

The budget is a profile, not a number: `small`, `medium` and `large` by
default, chosen from the table (Sonnet-only and three slices at most is
small; an Opus row, four to eight slices, or a written decision is medium;
more than eight slices, a consult or an unattended run is large). A personal ceiling caps every
profile: `governor.py budget ceiling 60`. When spend reaches the budget, the deny reason names the next profile
under the ceiling, so raising it is one command with a name in it; when
none fits, it says so and names the ceiling command instead.

The parts are still there for anyone who wants them one at a time:
`/governor:brief` (the interview on Sonnet, five questions at most),
`/governor:triage` (the table before any work), `/governor:decompose` (the
cut into slices and levels). A worked brief is below.

### 3. Read the plan card **(your turn)**

The table is written before any tool call that would start the work. This is
where a veto costs nothing: strike a row, move a slice up a tier, or answer
"adjust" and say what changes. After go, everything is delegation.

### 4. What happened between the two stops

Scouts on Haiku returned `path:line` facts; the model did not read the tree itself. For a test suite, the inventory script returns fixtures, duplicates, shadowing and markers as a table with no model in the loop. The model wrote its decisions down as one paragraph each with the rejected option, and when the work was large, cut it into slices and levels, checked by a script that refuses cycles and same-level file collisions. `/governor:decompose` is that cut on its own, for a branch or a suite that arrives already built.

### 5. Let it delegate, slice by slice

One spec per slice under `.governor/specs/`, then a Sonnet worker (`governor:implementer` or `py-testing:test-implementer`) in its own worktree, then `governor:reviewer` on Opus against the same spec. You read reports, not diffs. Every worker report has the same three sections:

- `## Result` DONE, PARTIAL or BLOCKED. PARTIAL or BLOCKED means a question came back; the conductor answers it and re-delegates.
- `## Changed files` Checked against the spec's file list. A file outside the list is a finding even if the change looks right.
- `## Evidence` The command on a `$` line and its output. The hook verified the command really ran; the conductor verifies it proves the definition of done.

Or hand the whole level to the supervisor and read one line per slice:
`governor.py run-level .governor/plan.json --level 1` runs every slice as a
headless worker in its own worktree, retries a worker that dies on an API
overload, and resumes from its index if rerun. Workers that sit idle or get
lost are not a thing it can do; a process ends with a verdict or a timeout.

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

What `/governor:brief` writes for a test-suite restructure after its interview. It passes `governor.py brief check`. Replace the path; nothing in it is specific to any organisation. Paste it into `.governor/brief.md` to skip the interview.

````
# Brief: restructure-e2e-suite

## Task
Restructure tests/e2e so every fixture has one home, every marker is registered, every test is named for what it checks, and the suite is green throughout.

## Definition of done
- [ ] the inventory diff shows zero shadowed fixtures and zero duplicated fixtures
- [ ] unregistered markers at zero; test names in test_<unit>_<behaviour>_<condition> form
- [ ] dead and duplicate tests deleted, each listed in .governor/plan.md with the reason
- [ ] `pytest -q tests/e2e` exits 0 after every level; a test that was failing before is listed, not silently fixed or deleted
- [ ] the inventory diff (`inventory.py tests --diff .governor/inventory-before.json`) is in the PR description

## Evidence
```
$ pytest -q tests/e2e
$ python3 <py-testing root>/skills/untangling-test-suites/scripts/inventory.py tests --diff .governor/inventory-before.json
```

## Out of scope
- the application code under test
- CI configuration

## Decisions already made
- branch refactor/e2e-structure — one branch, every level lands on it

## Assumptions
- tests/e2e collects on main before the work starts; a test failing there is listed first, not fixed
- worktrees are available for parallel slices

## Procedure
- run /governor:triage first and show the table before any work
- /py-testing:untangling-test-suites for the inventory and the keep/merge/rewrite/delete table; save the before-inventory to .governor/inventory-before.json
- /governor:decompose for slices and levels; /governor:delegate for each slice with py-testing:test-implementer as the worker and governor:reviewer on every slice; workers in worktrees
- plugin agents by name, nothing implemented inline, no other agent type
- a BLOCKED or PARTIAL report is answered by the conductor and re-delegated
````

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

- **A bare `general-purpose` spawn** is pinned to Sonnet but keeps a high effort setting the hook cannot rewrite. The plugin agents pin effort themselves. The brief lint refuses a procedure that names it.
- **Skills are named, not guessed.** They trigger on description match, which is not reliable enough for a run you will not watch. Name them.
- **An installed plugin is a pinned copy.** It changes only when a new version is installed; a checkout edited in place is not seen.
- **Headless slices need the install path.** Inside a skill, `${CLAUDE_PLUGIN_ROOT}` resolves; from a shell, the engine lives under `~/.claude/plugins/cache/konyklabs-plugins/governor/<version>/bin/governor.py`.
- **A worker's worktree has no environment.** An IDE type checker reading the main workspace flags the worker's new files turn after turn. Pass `--setup "uv sync"` to `run-level` and exclude `.governor/` from the editor's diagnostics.
- **Headless workers' dollars are not in the session ledger.** They are their own sessions; the readout's `workers $` figure and `/governor:budget` sum them from the run indexes.
- **Dollars on a subscription are notional.** The ratios are exact and the gate uses them; the absolute number is what the same work would cost at list price.
