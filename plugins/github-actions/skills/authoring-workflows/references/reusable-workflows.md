# Reusable workflows

Fetched 2026-10-29 from https://docs.github.com/en/actions/using-workflows/reusing-workflows, https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows, https://github.com/orgs/community/discussions/8488, https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow, https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax.

Contents: `workflow_call` inputs/outputs/secrets · `secrets: inherit` scope ·
`uses:` ref forms · nesting limits · permissions down the chain · one-hop
secrets · the job-to-job outputs pattern · `workflow_dispatch` · sources.

## `workflow_call` inputs, outputs, secrets

The docs describe three top-level keys under `on.workflow_call`: `inputs`
(typed `boolean`, `number` or `string`), `outputs` (mapped from a job's own
outputs), and `secrets` — https://docs.github.com/en/actions/using-workflows/reusing-workflows.

```yaml
# called workflow: .github/workflows/deploy.yml
on:
  workflow_call:
    inputs:
      environment:
        type: string
        required: true
    secrets:
      DEPLOY_TOKEN:
        required: true
    outputs:
      deployed_url:
        value: ${{ jobs.deploy.outputs.url }}

jobs:
  deploy:
    runs-on: ubuntu-latest
    outputs:
      url: ${{ steps.push.outputs.url }}
    steps:
      - id: push
        run: echo "url=https://example.invalid" >> "$GITHUB_OUTPUT"
```

In practice: an output is declared twice — once on the job that produces it
(`jobs.<job>.outputs.<name>`), once on `workflow_call.outputs` pointing at
`jobs.<job>.outputs.<name>`. Miss the second declaration and the caller sees
nothing, with no error at the call site. The `value` must be a job-level
output; a step output has to be mapped to a job output first. When the call
runs in a matrix, the output is the last successfully completed run that
actually set a value, so a later run that sets an empty string does not
overwrite an earlier real one.

## `secrets: inherit` and its scope

`secrets: inherit` passes every secret the caller has to the called
workflow, but the docs limit this to the same organization or enterprise —
same URL. An inherited secret can be referenced in the called workflow even
when `on.workflow_call.secrets` does not declare it.

Environment secrets: the called workflow's job sets `environment`; the
calling job cannot use that keyword. The caller must still pass the secret
(`inherit` or by name; a name pass works even if the secret exists only in
the environment). If it does not, the secret resolves to an empty string
with no error; `required: true` fails the run but only checks that the
secret was passed, not that it has a value. An environment secret wins over
a repository or organization secret of the same name.

```yaml
jobs:
  call-deploy:
    uses: my-org/shared/.github/workflows/deploy.yml@main
    secrets: inherit          # only valid when shared/ is in the same org
```

Crossing an org boundary (a public template repo, a different org's
reusable workflow) means `secrets: inherit` cannot be used at all — name
each secret explicitly instead.

## `uses:` ref forms

Two shapes: same-repo (`./.github/workflows/x.yml`, no `@ref`) and
cross-repo (`{owner}/{repo}/.github/workflows/x.yml@{ref}`). `@ref` accepts
a SHA, a tag, or a branch name; the docs call a SHA the safest option, and when a tag and
a branch share the same name the tag wins — https://docs.github.com/en/actions/using-workflows/reusing-workflows.
`refs/heads/` and `refs/tags/` prefixes are not allowed, and contexts or
expressions cannot be used in `uses:`. A same-repo call runs the called file
from the caller's own commit. Reusable workflows are called at job level,
never from a step.

```yaml
jobs:
  call-deploy:
    uses: my-org/shared-workflows/.github/workflows/deploy.yml@a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2  # v3
```

In practice: pin the cross-repo case the same way an action is pinned (see
`supply-chain.md`) — a same-repo call has no separate supply chain, since
it moves with the caller's own commit.

## Nesting limits

GitHub.com allows 10 levels of reusable-workflow nesting (the caller plus
9 more) and, since the November 2025 changelog, up to 50 workflow calls in
total from one run (previously 4 levels and 20 calls). The docs page's
GHES variant still says 4 levels (the caller plus 3). The 50-calls count is
per run; the reusing-workflow-configurations page separately caps one
workflow file at 50 unique reusable workflows. Loops are not allowed —
https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows,
corroborated by community discussion https://github.com/orgs/community/discussions/8488
(older posts there quote 2, then 4 levels; the accepted answer is the
10/50 change). A chain meant to be portable to GHES has less nesting
headroom than one written only for GitHub.com.

The docs do not fully specify how nested matrices compose against run limits. A community report
(discussion #38704, unverified against a docs page) describes nested
matrix jobs collectively exceeding the per-run 256-job cap (see
`runtime-shape.md`) — treat that as unverified but plausible.

## Permissions down the chain

Permissions granted to a caller workflow can only be maintained or reduced
by the workflows it calls, never elevated — https://docs.github.com/en/actions/using-workflows/reusing-workflows.
Setting `permissions: contents: write` at the caller does not let a
called workflow escalate to `id-token: write` if the caller never granted
it. See `permissions-and-oidc.md` for the resolution order within a workflow.

## Secrets are one hop

Secrets pass only to the workflow directly called — a workflow does not
automatically forward secrets to something *it* calls. Each level in a
nested chain must re-pass secrets explicitly (by name or `inherit`, subject
to the same-org rule above) for a secret to reach the bottom of the chain.
A three-level chain that forgets to re-declare `secrets: inherit` at the
middle level silently starves the bottom workflow of every secret, and
that job's failure looks like a missing credential, not a missing pass-through.

## The job-to-job outputs pattern

This is standard job dependency wiring, not specific to reusable
workflows, but it's the mechanism `workflow_call.outputs` sits on top of:

```yaml
jobs:
  producer:
    runs-on: ubuntu-latest
    outputs:
      version: ${{ steps.v.outputs.value }}
    steps:
      - id: v
        run: echo "value=1.2.3" >> "$GITHUB_OUTPUT"

  consumer:
    needs: producer
    runs-on: ubuntu-latest
    steps:
      - run: echo "building ${{ needs.producer.outputs.version }}"
```

`needs: producer` orders the jobs and exposes
`needs.producer.outputs.version` — the same pattern a caller uses to read
a called workflow's `workflow_call.outputs` value.

## `workflow_dispatch`

Input `type` is one of `boolean`, `choice`, `number`, `environment` or
`string`; `required` is a boolean (workflow-syntax reference). A
`workflow_dispatch` event takes at most 25 inputs, the workflow file must be
on the default branch for the trigger and the Run workflow button to appear,
and the actor needs write access
(https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).
Inputs passed to a reusable workflow with `jobs.<job>.with` are read through
the `inputs` context, not as environment variables. The manual-run page does
not cover `choice` options or defaults; re-check the syntax reference before
writing those from memory.

## Traps

- `secrets: inherit` over-sharing is a named zizmor audit
  (`secrets-inherit`, see `runtime-shape.md`); prefer naming secrets
  explicitly unless the called workflow is trusted with everything.
- A tag-pinned `uses:` on a cross-repo call is mutable by the repo owner;
  see `supply-chain.md` for why that matters and the tj-actions incident.

## Sources

URLs are listed in the Fetched line above and inline per section; the
`workflow_dispatch` `choice` options and defaults are the one item not
re-verified this pass. Fetched 2026-10-29.
