---
name: authoring-workflows
description: How to write GitHub Actions workflows that survive the preflight and the review — the docs-first rule for syntax and limits (fetch the current page, never write from memory), least-privilege permissions with OIDC instead of long-lived keys, SHA-pinned actions with version comments, reusable workflows and what crosses the call boundary, the pull_request_target and script-injection traps, caching and concurrency that do not leak across trust levels, and the runtime limits that fail a run late. Use when writing or changing a workflow, a reusable workflow or its caller, an OIDC deploy, when a run is denied a permission, cancelled, or silently stops on a schedule.
---

# Authoring GitHub Actions workflows

## 1. Syntax and limits come from the docs, not from memory

Actions changes weekly: checkout v7 (2026-06-18) refuses fork heads under
`pull_request_target` by default, OIDC subjects gained an ID-pinned form
(effective for new repositories from 2026-07-15), the scope list grows. Before writing a key you
have not written this month, read its current page:

- **Context7 when the MCP server is present**: resolve the id for the
  GitHub Actions docs once, then query the key (`workflow_call outputs`).
- **Otherwise** `docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax`
  for syntax, `.../actions/reference/limits` for every number, and the
  security-hardening guide under `.../actions/security-for-github-actions/`.

`references/reusable-workflows.md`, `permissions-and-oidc.md`,
`supply-chain.md` and `runtime-shape.md` carry the facts this file relies
on, each with its fetch date.

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
    timeout-minutes: 15               # the default is hours; say what you mean
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
  organisations have had a read-only default token since 2023-02-02; an
  older one may still be permissive, which is why the workflow says it.
  `contents: read` checks out; a release needs `contents: write`; only an
  OIDC exchange needs `id-token: write`.
- **OIDC, never keys.** Provider `token.actions.githubusercontent.com`,
  audience `sts.amazonaws.com`. The trust policy's `sub` condition is the
  whole access control. Write it in the ID-pinned form
  `repo:OWNER@ORGID/REPO@REPOID:ref:refs/heads/main` (or `:environment:NAME`),
  which repositories created after 2026-07-15 and renamed or transferred
  ones issue and a rename cannot hijack; the name-only `repo:OWNER/REPO:…`
  form is for older repositories only, until they opt in.
- **Pin to a SHA, comment the version.** A tag is mutable: CVE-2025-30066
  rewrote every tj-actions/changed-files tag to a commit that dumped
  runner memory for secrets in 23,000+ repositories. The docs call the
  full SHA "the only way to use an action as an immutable release"; the
  `# v7` comment keeps Dependabot able to bump it. Resolve a tag with
  `gh api repos/OWNER/REPO/commits/TAG --jq .sha`. A SHA that exists only
  in a fork (an impostor commit) passes a glance and not `zizmor`.
- **Timeouts and concurrency.** No `timeout-minutes` means hours on a hung
  step. `cancel-in-progress: true` is for checks; a deploy group keeps it
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
  hands over every caller secret and works only inside one organisation
  or enterprise; environment secrets never cross.
- Permissions only narrow down the chain. Secrets travel one hop: a
  three-level chain re-passes them at every level or the last finds none.
- Ten nesting levels on GitHub.com, four on Enterprise Server; a matrix in
  the caller multiplies one in the callee against the 256-jobs-per-run cap.
- Pin an external callee to a SHA like an action. An org-internal callee at
  `@main` is a policy to make on purpose and to write in the repository's
  rules, because the preflight allowlists it only when told.

## 4. The two traps that hand the repository to a stranger

- **`pull_request_target` with the PR's head checked out.** The event runs
  with the base repository's token and secrets, for fork PRs too; checking
  out `github.event.pull_request.head.sha` and running anything from it is
  the "pwn request". checkout v7 refuses it by default and
  `allow-unsafe-pr-checkout: true` is the opt-out never to write. Untrusted
  code runs under `pull_request` with no secrets; a `workflow_run` job then
  reads artifacts only.
- **Event data inside `run:`.** A PR title, an issue body, a commit message
  or a branch name in `${{ github.event.* }}` interpolated into a shell line
  is code its author runs on your runner. Bind it to `env:` and read the
  shell variable; `workflow_dispatch` inputs get the same treatment:

```yaml
      - env:
          TITLE: ${{ github.event.pull_request.title }}
        run: echo "$TITLE"                 # data, not code
```

## 5. Caches, artifacts, schedules

- `actions/cache` restores the exact `key`, then the first `restore-keys`
  prefix match, newest first, from the current branch, the default branch
  or a PR's base. A privileged workflow restoring by a broad prefix can
  restore what a low-privilege workflow wrote: key on the lockfile hash and
  never share a prefix across trust levels. 10 GB per repository, seven-day
  eviction, keys up to 512 characters.
- A scheduled workflow in a public repository stops after 60 days without
  a commit, with nothing surfaced but the Actions tab (community-
  corroborated, not a docs quote). A nightly gate needs a keepalive or
  outside monitoring.
- `GITHUB_STEP_SUMMARY` is where a check writes its table for a human;
  `outputs` plus `needs` is how a later job reads a value.

## 6. Before the push

Run the preflight in `preflighting-workflows`, paste its table into the PR,
fix every blocking row; the lens in `reviewing-workflows` reads the rest.

## Sources

docs.github.com (reusing workflows, workflow syntax, permissions, OIDC in
AWS, security hardening, `pull_request_target`, caching, artifacts,
limits), github.blog changelogs (read-only token 2023-02-02, immutable
subject claims 2026-04-23, checkout v7 2026-06-18), the GitHub Security
Lab on pwn requests, CISA and Wiz on CVE-2025-30066, actionlint and zizmor
docs; fetched 2026-09-29. Left out or hedged as unconfirmed that day: the
private-repository job timeout default, checkout's `persist-credentials`
default, default artifact retention, `workflow_dispatch` input syntax, the
exact zizmor audit names. Details and quotes in `references/`.
