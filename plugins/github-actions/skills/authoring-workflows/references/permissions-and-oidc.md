# Permissions and OIDC

Fetched 2026-10-29 from https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax, https://github.blog/changelog/2023-02-02-github-actions-updating-the-default-github_token-permissions-to-read-only/, https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository, https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws, https://github.blog/changelog/2026-04-23-immutable-subject-claims-for-github-actions-oidc-tokens/, https://github.com/aws-actions/configure-aws-credentials.

Contents: permission scopes · resolution order · the read-only default ·
`permissions: {}` and `id-token: write` · OIDC to AWS · standard and
ID-pinned `sub` forms · `configure-aws-credentials` · subject
customization limits · sources.

## The permission scopes

The `permissions:` key accepts a fixed set of scopes: `actions`,
`artifact-metadata`, `attestations`, `checks`, `code-quality`, `contents`,
`deployments`, `discussions`, `id-token`, `issues`, `packages`, `pages`,
`pull-requests`, `security-events`, `statuses`, `vulnerability-alerts`, and
`copilot-requests` —
https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax.
Each takes `read`, `write`, or `none`, except three: `id-token` and
`copilot-requests` are only `write` or `none` (there is no read form — you
either can mint an OIDC token or you cannot), and `vulnerability-alerts` is
only `read` or `none`. Some scopes are shown only where the feature exists
(`copilot-requests`, `code-quality`, `artifact-metadata`).

The docs state that once any scope is explicitly set, every scope not
named becomes `none`. In practice: writing `permissions: contents: read`
alone does not leave the other scopes at their default — it drops them
all, which is usually what you want but silently breaks a step that
expected, say, `checks: write` to still be available by default.

```yaml
permissions:
  contents: read
  id-token: write   # everything else becomes none
```

`permissions: {}` is the explicit form of "no access to anything" — a
useful top-level default for a workflow whose jobs each declare their own
narrower grant. `read-all` and `write-all` set one level on every scope;
`vulnerability-alerts` is always `read` under either, since it cannot be
`write`.

`contents: read` is enough to check out the repository and list commits;
it is not enough to create a release or push a tag, which need
`contents: write`. Scope names describe an API surface, not a specific
action, so read the target step's own requirement rather than assuming
`read` covers everything short of a destructive write.

## Resolution order

Permissions resolve in this order, narrowest wins for anything explicitly
set: enterprise or organization default → repository default →
workflow-level `permissions:` → job-level `permissions:` (a job's own
block overrides the workflow-level block for that job only, not for
others) — same URL. Last, a run triggered by a pull request from a fork
has its write scopes cut to read unless the admin enabled "Send write
tokens to workflows from pull requests"; `pull_request_target` is exempt
and keeps read/write even from a fork, which is why it is a trap.

## The read-only default since 2023-02-02

New enterprises, organizations and personal-account repositories get a
default `GITHUB_TOKEN` that is read-only on `contents` and `packages` —
this became the default on 2023-02-02, and existing ones were not touched — https://github.blog/changelog/2023-02-02-github-actions-updating-the-default-github_token-permissions-to-read-only/,
also documented at https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository.
A repository inside an organization inherits the organization's setting,
and a restricted organization default disables the permissive option in
the repository. In practice: an older repository created before that date may still carry
a permissive default; do not assume every repo in an org has the same
baseline — check the repository's Actions settings rather than assuming
the read-only default applies, and set `permissions:` explicitly in the
workflow either way so the file is self-describing.

## `id-token: write`

Required to mint an OpenID Connect (OIDC) token for the run. Without it, any
action that calls the OIDC token endpoint (e.g. `aws-actions/configure-aws-credentials`
using role assumption) fails at the token-request step, not at the AWS
call — the error surfaces as a missing token, not a missing AWS
permission.

## OIDC to AWS

The identity provider is `https://token.actions.githubusercontent.com`.
For the official `configure-aws-credentials` action, the audience (`aud`
claim) must equal `sts.amazonaws.com`, which the action sends by default
(its `audience` input exists for non-default partitions such as China
regions) —
https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws.
The trust policy must carry at least one `sub` condition next to the `aud`
one, or any GitHub repository could assume the role.

### Standard subject form

```
repo:OWNER/REPO:ref:refs/heads/BRANCH
```

Also used: `repo:OWNER/REPO:environment:NAME` (a run against a GitHub
Environment) and `repo:OWNER/REPO:pull_request` (a run triggered by a pull
request). An AWS trust policy's `StringEquals` condition on `sub` should
match one of these forms exactly, or `StringLike` with an explicit
suffix — a bare `*` turns one repository's trust into the whole owner's.

### ID-pinned subject form (immutable)

```
repo:OWNER@ORGID/REPO@REPOID:ref:refs/heads/BRANCH
```

This form applies automatically to repositories created after
2026-07-15, and to repositories renamed or transferred after that date
(the changelog's timeline names renames only); existing repositories keep
the name-only form unless they opt in. It is github.com only, not GitHub
Enterprise Server. The delimiter between name and ID is `@` (an earlier
draft of the post said `-`) —
https://github.blog/changelog/2026-04-23-immutable-subject-claims-for-github-actions-oidc-tokens/.
In practice: a trust policy written against the name-only form
(`repo:OWNER/REPO:...`) is vulnerable to a rename or ownership-transfer
hijack — a renamed-away repository frees the name for someone else to
claim, and the old trust policy still matches it. The ID-pinned form
binds to the numeric org and repo IDs, which do not change on a rename.
Use the ID-pinned form for any repository created (or renamed into its
current name) after 2026-07-15. An older repository opts in with a toggle
in the OIDC settings of the organization or repository, or through the
API; a preview endpoint shows the subject prefix it will then emit. GitHub
rewrites no trust policies: flip the toggle and the old name-only
condition stops matching, and the AWS side fails with `Not authorized to
perform sts:AssumeRoleWithWebIdentity`. Update the `sub` condition first
or in the same change.

## `configure-aws-credentials` needs

`role-to-assume` (the IAM role ARN) and `aws-region` are the two inputs
the action needs to complete OIDC-based role assumption (`aws-region` is
required always; `role-to-assume` only for some auth types, OIDC among
them); the current major is v6, and the thumbprint, if given, is ignored —
https://github.com/aws-actions/configure-aws-credentials. The job also
needs `permissions: id-token: write` (and usually `contents: read`, for
the checkout step that normally runs alongside it).

```yaml
permissions:
  id-token: write
  contents: read

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000 # v4
      - uses: aws-actions/configure-aws-credentials@0000000000000000000000000000000000000000 # v6
        with:
          role-to-assume: arn:aws:iam::123456789012:role/deploy
          aws-region: us-east-1
```

## Subject template customization limits

GitHub lets an organization or repository customize which claims compose
the `sub` value (subject template customization), but AWS does not support
custom claims for OIDC, so only `sub` and `aud` are usable in a trust
policy — oidc-in-aws. Whatever template is configured, an AWS trust policy can only reference claims
GitHub actually issues in the token; a trust policy condition on a claim
that doesn't exist in the token for that workflow's trigger type simply
never matches, and the failure looks like a permissions problem rather
than a malformed condition. Check the token's actual claim set (the
`sub` and `aud` GitHub documents for the trigger in use) before writing
the trust policy condition, not the other way around.

## Sources

https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax;
https://github.blog/changelog/2023-02-02-github-actions-updating-the-default-github_token-permissions-to-read-only/;
https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository;
https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws;
https://github.blog/changelog/2026-04-23-immutable-subject-claims-for-github-actions-oidc-tokens/;
https://github.com/aws-actions/configure-aws-credentials. Fetched 2026-10-29.
