# claude-plugins

Claude Code plugin marketplace (`konyklabs-plugins`): `governor` (token
guardrails for expensive-model sessions), `py-testing` (Python test
engineering skills), `prod-readiness` (security and readiness scanning).
`README.md` says how to install and use them; this file is for working on
them.

## Before any commit

```
bash scripts/validate.sh                                        # must end: all manifests and components valid
bash scripts/audit-deps.sh                                      # must end: audit ok
uv run --with pytest python -m pytest -q plugins                # all green, paste the summary line
uv run --python 3.9 --with pytest python -m pytest -q plugins   # 3.9 is the floor the scripts promise
```

Paste the output in the commit or PR; never say "tests pass" without it.

## Rules that shape every change

- **Standard library only, no network.** The three Python scripts import
  nothing outside the stdlib and open no sockets; the audit fails
  otherwise. External tools are run only if already on PATH, never
  installed, and their output is reduced to counts.
- **Findings never carry matched text.** A finding is `path:line` plus a
  rule name. Every string that originates in a scanned repository or a tool
  is sanitized, length-capped and origin-marked before it reaches a report.
  The report is untrusted input to the model that reads it.
- **Fail closed.** A tool that errors, times out or is missing is a
  `review` or `skip` row with its reason, never a `pass`. An unknown model
  is priced at the top rate. A budget of 0 is a closed gate.
- **Project config may only tighten.** `.claude/governor.json` in a
  repository can lower a budget or forbid forks; it cannot loosen anything.
  Loosening lives in the user's own file.
- **Deterministic first.** A step that can be a script is a script with a
  test; a skill tells the model to run it, not to reason it out. Every
  constant carries the reason for its value.
- **Skills**: third-person descriptions with the words a person would type;
  SKILL.md under 150 lines; references one level deep with a contents list;
  one home per fact; sources and fetch dates at the end; where the docs are
  silent, say so.
- **Agents**: pin `model` and `effort` in frontmatter (subagents otherwise
  inherit the session's effort); state the report contract; keep tools to
  what the job needs.
- **Cleanroom.** No company, product, vertical, hostname or account
  identifier anywhere, including examples and fixtures. Technology names
  are fine. Placeholders are obvious placeholders.

## Conventions

- Branches `<type>/<roadmap-issue>-<slug>`; PR titles are conventional
  commits carrying the task ref (`konyklabs/roadmap#N`); squash merges.
- Every change is reviewed locally with the org's lens prompts
  (`konyklabs/.github` `review/lenses/`) before the single push; findings
  are fixed with a test each or answered on the driving issue.
- An installed plugin is a versioned copy: bump `version` in the plugin's
  `plugin.json` (and the release-please manifest keeps it in step) or
  develop with `claude --plugin-dir ./plugins/<name>`.
- `GOVERNOR_DEBUG=1` writes redacted hook-input shapes to the state dir;
  use it to verify a hook contract against a real session.
- Zips for machines without git access come from `scripts/dist.sh` only
  (committed tree, tests excluded, archive-hygiene checked).

## Where things are

- Design and verification record: `docs/ARCHITECTURE.md`.
- Cost runbook and supply-chain statement: `docs/COST-TRACKING.md`.
- Operator playbook: `docs/PLAYBOOK.md`.
- Hook engine: `plugins/governor/bin/governor.py` (one file); prices in
  `pricing.json` beside it, with the date checked.
- Scanner: `plugins/prod-readiness/skills/readiness-review/scripts/readiness.py`.
- Inventory: `plugins/py-testing/skills/untangling-test-suites/scripts/inventory.py`.
- Driving tasks: konyklabs/roadmap#60 and #61.
