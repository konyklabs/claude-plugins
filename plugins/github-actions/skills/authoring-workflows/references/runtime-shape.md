# Runtime shape

Fetched 2026-09-29 from https://docs.github.com/en/actions/reference/limits, https://docs.github.com/en/actions/using-workflows/caching-dependencies-to-speed-up-workflows, https://docs.github.com/en/actions/using-workflows/storing-workflow-data-as-artifacts, https://github.blog/news-insights/product-news/supercharging-github-actions-with-job-summaries, https://github.com/orgs/community/discussions/86087, https://github.com/rhysd/actionlint/releases, https://github.com/rhysd/actionlint/blob/main/docs/usage.md, https://docs.zizmor.sh, https://github.com/nektos/act.

Contents: timeouts · concurrency and cancellation · cache key and
restore-keys semantics · artifacts v4 · matrix and other limits ·
`GITHUB_STEP_SUMMARY` · scheduled workflows going dark · actionlint ·
zizmor · act · sources.

## Timeouts

`timeout-minutes` at the job level commonly defaults to 360 (6 hours);
this is widely cited but marked here as to-confirm against the current
limits page before relying on it —
https://docs.github.com/en/actions/reference/limits. A private-repository
variant with a different default has been seen elsewhere but was not
verified this pass. Set `timeout-minutes` explicitly on any job rather
than trusting the default, hosted or self-hosted.

## Concurrency and cancellation

```yaml
concurrency:
  group: deploy-${{ github.ref }}
  cancel-in-progress: true
```

`cancel-in-progress: true` cancels other in-flight runs sharing the same
`group` value when a new run starts. A queue cap of 100 runs per
concurrency group is lightly verified (search-summary, not read directly
off a docs page this pass) — treat a group backing up past that as an
untested edge case rather than a documented guarantee.

## Cache key and `restore-keys` semantics

`actions/cache` matches the exact `key` first; failing that, it walks
`restore-keys` in order, taking the newest cache entry whose key starts
with that prefix (newest wins on a tie) —
https://docs.github.com/en/actions/using-workflows/caching-dependencies-to-speed-up-workflows.

```yaml
- uses: actions/cache@0000000000000000000000000000000000000000 # v4
  with:
    path: ~/.cache/pip
    key: pip-${{ hashFiles('requirements.txt') }}
    restore-keys: |
      pip-
```

Caps: a key is at most 512 characters; a repository gets 10 GB by default,
raisable up to 10 TB; unused entries are evicted after 7 days; over the
cap, eviction is oldest-first. Caches are branch-scoped: a run sees caches
from its own branch and from the repository's default branch, and a pull
request run also sees the base branch's caches — there is no cross-branch
sharing beyond that. This scoping is also why a `restore-keys` prefix can
be poisoned across a trust boundary (see `supply-chain.md`): a fork PR
run's cache is visible to the base-branch caches it inherits from, not the
other way, but the prefix match doesn't itself check who wrote the entry.

## Artifacts v4: immutable

Artifacts uploaded with the v4 actions are immutable within a run: you
cannot overwrite an artifact by uploading the same name twice in one run.
Downloading an artifact from a different run needs an authentication
token and the run's ID, not just the artifact name —
https://docs.github.com/en/actions/using-workflows/storing-workflow-data-as-artifacts.
Default retention period and size caps were NOT FOUND in this pass —
check the artifacts page directly before assuming a specific number.

## Matrix and other limits

From https://docs.github.com/en/actions/reference/limits:

| limit | value |
|---|---|
| matrix jobs per workflow run | 256 |
| workflow file size | 500 KB |
| workflow run duration | 35 days |
| re-runs per workflow run | 50 |
| environment protection-rule approval wait | 30 days |
| job execution time, GitHub-hosted runner | 6 hours |
| job execution time, self-hosted runner | 5 days |
| self-hosted runner queue wait | 24 hours |
| `GITHUB_TOKEN` API rate | 1,000 requests/hour/repo |
| workflow trigger events | 1,500 per 10 seconds per repo |
| queued workflow runs | 500 per 10 seconds |

A matrix that would exceed 256 jobs fails to start rather than truncating;
see `reusable-workflows.md` for the unverified report of nested matrices
compounding past this cap.

## `GITHUB_STEP_SUMMARY`

A step can append Markdown to the file at `$GITHUB_STEP_SUMMARY`, and the
result renders on the run's summary page —
https://github.blog/news-insights/product-news/supercharging-github-actions-with-job-summaries.

```yaml
- run: echo "### Results" >> "$GITHUB_STEP_SUMMARY"
```

Use it for a preflight table or a plan summary a reviewer should see
without opening the raw log.

## Scheduled workflows going dark

On public repositories, a scheduled (`on: schedule`) workflow
automatically disables itself after 60 days with no new commits to the
repository; issues, pull requests, tags, and releases do not count as
activity for this purpose. This is community-corroborated, not a direct
docs quote — https://github.com/orgs/community/discussions/86087 — and
private-repository behavior was not found in this pass. In practice: a
scheduled workflow on a low-traffic repo needs either regular commits or
a periodic manual re-enable; a "why did the nightly job stop running"
report is very often this.

## Tools

### actionlint

Version 1.7.12, released 2026-03-30 —
https://github.com/rhysd/actionlint/releases. Install via
`go install github.com/rhysd/actionlint/cmd/actionlint@latest`, Homebrew,
or a prebuilt binary. `--format sarif` emits SARIF for upload as a code
scanning result. Exit codes, per
https://github.com/rhysd/actionlint/blob/main/docs/usage.md: `0` clean,
`1` problems found, `2` usage or I/O error — a preflight script should
treat `2` as its own failure, not as "no problems found."

### zizmor

Version 1.30.1, released 2026-09-09 — https://docs.zizmor.sh. Install
with `pip install zizmor`, `pipx install zizmor`, or `uv tool install
zizmor`. Runs offline by default (no network calls to check anything
remotely) and emits SARIF. `--persona=pedantic` or `--persona=auditor`
widen the finding set beyond the default persona.

Audit names, summarized from https://docs.zizmor.sh/audits (not
verbatim): unpinned `uses:` and unpinned container images,
template-injection (the `run:` splicing trap in `supply-chain.md`),
dangerous-triggers (`pull_request_target`, `workflow_run`,
`issue_comment`), excessive-permissions, `secrets-inherit`,
impostor-commit, cache-poisoning, known-vulnerable-actions,
bot-conditions, hardcoded-container-credentials. Treat this list as
summarized, not a literal reproduction of zizmor's own audit
descriptions — read the linked page for exact wording before quoting one.

### act

`nektos/act` runs a workflow locally in Docker —
https://github.com/nektos/act. Runner-image parity with GitHub's hosted
runners is imperfect, so a workflow that passes under `act` is a useful
smoke test, not proof it will behave identically on GitHub's own runners.

## Sources

URLs are listed in the Fetched line above and inline per section; the
scheduled-workflow and cache-queue-cap facts are community-corroborated,
not direct docs quotes. Fetched 2026-09-29.
