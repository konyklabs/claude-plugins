# Planted defects (answer key; reviewers never see this)

- ci.yml:13 uses-unpinned — `actions/checkout@v4` is a tag, not a 40-hex commit SHA
- ci.yml:15 uses-unpinned — `actions/setup-node@1a4442c...` is only a 7-char short SHA, not 40 hex
- ci.yml:9 permissions-missing — no top-level `permissions:` and job `build` has none either
- ci.yml:9 timeout-missing — job `build` has no `timeout-minutes:`
- ci.yml:18 expression-injection — `${{ github.event.issue.title }}` interpolated straight into a `run:` block scalar
- ci.yml:20 inputs-in-run — `${{ inputs.message }}` interpolated into `run:` instead of passed through `env:`
- ci.yml:22 expression-injection — `${{ github.event.commits }}` interpolated into `run:` (commit list is attacker-controlled on a push event)
- pr-target.yml:7 permissions-write-all — workflow-level `permissions: write-all`
- pr-target.yml:17 pull-request-target-checkout — `actions/checkout` ref is the PR head on a `pull_request_target` trigger
- deploy.yml:8 concurrency-missing-on-deploy — job id `deploy` has no `concurrency:`, workflow or job level
- deploy.yml:11 secrets-inherit-external — `secrets: inherit` on a job calling `other-org`'s reusable workflow, not this org's
- deploy.yml:12 uses-version-comment-missing — the SHA-pinned reusable-workflow call has no trailing `# vN`/tag comment
- nightly.yml:4 schedule-without-concurrency — `schedule:` trigger with no workflow-level `concurrency:`
- nightly.yml:20 uses-unpinned — `docker://ghcr.io/example/audit-tool:1.4.0` carries a tag, not an `@sha256:` digest
- nightly.yml:25 inputs-in-run — `${{ github.event.inputs.message }}` inside a `with: script:` (github-script) block
- nightly.yml:28 expression-injection — `${{ github.event.head_commit.message }}` interpolated into `run:`
