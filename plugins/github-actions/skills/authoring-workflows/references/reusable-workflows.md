# Reusable workflows

Fetched 2026-09-29 from https://docs.github.com/en/actions/using-workflows/reusing-workflows, https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows, https://github.com/orgs/community/discussions/8488, https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow.

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
nothing, with no error at the call site.

## `secrets: inherit` and its scope

`secrets: inherit` passes every secret the caller has to the called
workflow, but the docs limit this to the same organization or enterprise —
same URL. `workflow_call` has no `environment` key, so an environment
secret cannot be inherited this way; it has to be re-declared and passed
explicitly by name.

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
a SHA, a tag, or a branch name; the docs recommend a SHA, and when a tag and
a branch share the same name the tag wins — https://docs.github.com/en/actions/using-workflows/reusing-workflows.

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
9 more); GitHub Enterprise Server allows 4. Loops are not allowed —
https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows,
corroborated by community discussion https://github.com/orgs/community/discussions/8488.
A workflow chain meant to be portable to GHES has less nesting headroom
than one written only for GitHub.com.

Reusable workflows count as a single unit against run limits, but the docs
do not fully specify how nested matrices compose. A community report
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

Not re-verified this pass; the input-syntax details (types, `choice`
options, defaults) should be re-checked against
https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow
before writing a `workflow_dispatch` block from memory.

## Traps

- `secrets: inherit` over-sharing is a named zizmor audit
  (`secrets-inherit`, see `runtime-shape.md`); prefer naming secrets
  explicitly unless the called workflow is trusted with everything.
- A tag-pinned `uses:` on a cross-repo call is mutable by the repo owner;
  see `supply-chain.md` for why that matters and the tj-actions incident.

## Sources

URLs are listed in the Fetched line above and inline per section; the
`workflow_dispatch` input syntax is the one item not re-verified this
pass. Fetched 2026-09-29.
