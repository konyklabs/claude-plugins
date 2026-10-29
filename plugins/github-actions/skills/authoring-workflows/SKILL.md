---
name: authoring-workflows
description: How to write GitHub Actions workflows that survive the preflight and the review — the docs-first rule for syntax and limits (fetch the current page, never write from memory), least-privilege permissions with OIDC instead of long-lived keys, SHA-pinned actions with version comments, reusable workflows and what crosses the call boundary, the pull_request_target and script-injection traps, caching and concurrency that do not leak across trust levels, and the runtime limits that fail a run late. Use when writing or changing a workflow, a reusable workflow or its caller, an OIDC deploy, when a run is denied a permission, cancelled, or silently stops on a schedule.
---

# Authoring GitHub Actions workflows

## 1. Syntax and limits come from the docs, not from memory

Actions changes weekly: checkout v7, ID-pinned OIDC subjects, `cache-mode`
and lockfiles all landed in 2026. Before writing a key you have not written
this month, read its current page:

- **Context7 when the MCP server is present**: resolve the id for the
  GitHub Actions docs once, then query the key (`workflow_call outputs`).
- **Otherwise** `docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax`
  for syntax, `.../actions/reference/limits` for every number, and the
  secure-use reference under `.../actions/reference/security/`.

The files in `references/` carry the facts this file relies on, with fetch dates.

## 2. The shape of a workflow that passes

```yaml
name: deploy
on:
  push:
    branches: [main]
permissions: {}                       # nothing by default; each job names what it holds
concurrency:
  group: deploy-${{ github.ref }}
  cancel-in-progress: false           # a deploy finishes; a lint run may cancel
jobs:
  deploy:
    runs-on: ubuntu-latest
    timeout-minutes: 15               # the default is 360; say what you mean
    permissions:
      contents: read
      id-token: write                 # OIDC; the only write this job holds
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000 # v7
      - uses: aws-actions/configure-aws-credentials@0000000000000000000000000000000000000000 # v5
        with:
          role-to-assume: arn:aws:iam::123456789012:role/deploy
          aws-region: us-east-1
      - run: ./deploy.sh
```

- **Permissions.** Once any scope is named, every unnamed scope is `none`:
  `permissions: {}` at the top, a block per job. New repositories and
  organisations have had a read-only default token since 2023-02-02; older
  ones may be permissive, so say it. `contents: read` checks out; a release
  needs `contents: write`; only an OIDC exchange needs `id-token: write`.
- **OIDC, never keys.** Provider `token.actions.githubusercontent.com`,
  audience `sts.amazonaws.com`. The trust policy's `sub` condition is the
  whole access control. Write it in the ID-pinned form
  `repo:OWNER@ORGID/REPO@REPOID:ref:refs/heads/main` (or `:environment:NAME`),
  issued to repositories created, renamed or transferred after 2026-07-15
  (older ones opt in) and immune to a rename hijack; the name-only
  `repo:OWNER/REPO:…` form is for older repositories.
- **Lock, or pin to a SHA with the version in a comment.** A tag is
  mutable: CVE-2025-30066 repointed every tj-actions/changed-files tag to a
  commit that dumped runner memory into logs (23,000+ repositories used
  it). A lockfile (`.github/workflows/actions.lock`, `gh actions-lock`, GA
  2026-10-28) records every action's commit, transitive ones too, and the
  docs say it supersedes SHA pinning; it skips `jobs.<id>.uses`, so a
  reusable workflow still takes a SHA. The `# v7` comment keeps Dependabot
  able to bump one; resolve a tag with `gh api
  repos/OWNER/REPO/commits/TAG --jq .sha`. An impostor commit (a SHA that
  exists only in a fork) passes a glance and not `zizmor`.
- **Timeouts and concurrency.** No `timeout-minutes` means 360 minutes on
  a hung step. `cancel-in-progress: true` is for checks; a deploy group keeps it
  `false` so the next push never cancels a half-applied deploy.

## 3. Reusable workflows: what crosses the boundary

```yaml
jobs:
  gate:
    uses: ORG/.github/.github/workflows/gate.yml@main   # org-internal callers may ride main by policy
    permissions: { contents: read, pull-requests: write }
    secrets: { TOKEN: ${{ secrets.TOKEN }} }              # named; `inherit` only for a same-org callee
    with: { strict: true }
```

- The callee declares typed `inputs` (`boolean`, `number`, `string`),
  `secrets`, and `outputs` mapped from a job's outputs. `secrets: inherit`
  passes every caller secret within one organisation or enterprise; a
  calling job cannot set `environment:`, the callee job names its own.
- Permissions only narrow down the chain. Secrets travel one hop: a
  three-level chain re-passes them at every level or the last finds none.
- Ten levels on github.com (the caller plus nine reusable), four on
  Enterprise Server (the caller plus three); 50 unique reusable workflows
  per file; a matrix in the caller multiplies one in the callee against the
  256-jobs-per-run cap.
- Pin an external callee to a SHA. An org-internal `@main` callee is a
  policy to write in the repository's rules; the preflight allows it if told.

## 4. The two traps that hand the repository to a stranger

- **`pull_request_target` with the PR's head checked out.** The event runs
  with the base repository's token and secrets, for fork PRs too; checking
  out `github.event.pull_request.head.sha` and running anything from it is
  the "pwn request". checkout v7 refuses it (also under `workflow_run` of a
  `pull_request*` event), backported 2026-07-20 to every supported major:
  floating tags refuse, an old SHA pin does not. `allow-unsafe-pr-checkout:
  true` is the opt-out never to write; `git fetch`, `gh pr checkout` and a
  downloaded artifact escape the check. Public repositories also get a
  default event policy on `pull_request_target` (enforced 2026-12-02): allow
  it there or its runs are blocked. Untrusted code runs under
  `pull_request` with no secrets; `workflow_run` reads artifacts only.
- **Event data inside `run:`.** A PR title, an issue body, a commit message
  or a branch name in `${{ github.event.* }}` interpolated into a shell line
  is code its author runs on your runner. Bind it to `env:` and read the
  variable; `workflow_dispatch` inputs too:

```yaml
      - env:
          TITLE: ${{ github.event.pull_request.title }}
        run: echo "$TITLE"                 # data, not code
```

## 5. Caches, artifacts, schedules

- `actions/cache` restores the exact `key`, then the first `restore-keys`
  prefix match, newest first, from the current branch, the default branch
  or a PR's base. A privileged workflow restoring a broad prefix can restore
  what a low-privilege one wrote: key on the lockfile hash, never share a
  prefix across trust levels. `cache-mode` (workflow or job, inherited by
  reusable calls) narrows it: `read` by default on `pull_request_target`,
  `write` on `push`. 10 GB per repository, seven days unused, keys to 512
  characters.
- A scheduled workflow in a public repository is disabled after 60 days
  with no repository activity, and runs only from the default branch. A
  nightly gate needs a keepalive or outside monitoring.
- `GITHUB_STEP_SUMMARY` holds a check's table for a human; `outputs` plus
  `needs` pass a value to a later job.

## 6. Before the push

Run `preflighting-workflows`, paste its table into the PR, fix every
blocking row; `reviewing-workflows` reads the rest.

## Sources

docs.github.com (reusing workflows, workflow syntax, OIDC in AWS, secure
use, `pull_request_target`, caching, lockfiles, limits), github.blog
changelogs (2023-02-02 to 2026-10-28), the GitHub Security Lab, CISA,
GHSA-mrrh-fwg8-r2c3 and Wiz on CVE-2025-30066, actionlint and zizmor docs;
fetched 2026-10-29. The four-level Enterprise Server limit is from that
version's reusing-workflows page (enterprise-server@3.17 to 3.20).
