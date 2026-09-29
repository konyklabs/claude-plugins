---
type: llm
focus: last_message
---
Score 1 if the workflow (a) declares `id-token: write` in a workflow- or
job-level `permissions:` block, (b) uses no long-lived AWS credentials —
no `aws-access-key-id`/`aws-secret-access-key` inputs and no hardcoded
keys, only `role-to-assume` via `aws-actions/configure-aws-credentials`
or equivalent OIDC role assumption, (c) pins every third-party action to
a full commit SHA with a same-line version comment rather than a tag or
branch, and (d) declares a `permissions:` block at the top level (or on
every job) instead of leaving the repository's default token scope in
force. Score 0 if it uses static AWS credentials, pins an action to a
tag or branch, omits `id-token: write`, or never states `permissions:`
anywhere in the file.
