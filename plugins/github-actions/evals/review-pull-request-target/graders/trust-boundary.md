---
type: llm
focus: last_message
---
Score 1 if the review names the trust-boundary defect: `pull_request_target`
runs with the base repository's `GITHUB_TOKEN` and secrets even for fork
PRs, and checking out then executing the fork PR's head
(`npm ci && npm test` from attacker-controlled code) is a "pwn request" —
it lets a malicious PR run arbitrary code with the base repository's
privileges. This must be reported as the primary or a blocking finding
with a concrete failure scenario (e.g. a malicious `package.json` install
script, `test` script, or dependency exfiltrates `GITHUB_TOKEN` or a
repository secret during `npm ci`/`npm test`). Score 0 if the review
misses this defect, demotes it to a minor or style note, buries it under
unrelated style nits (formatting, naming, step ordering) with no
trust-boundary finding, or gives a finding with no concrete failure
scenario.
