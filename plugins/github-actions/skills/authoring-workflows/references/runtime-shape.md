# Runtime shape

Fetched 2026-10-29 from https://docs.github.com/en/actions/reference/limits, https://docs.github.com/en/actions/using-workflows/caching-dependencies-to-speed-up-workflows, https://docs.github.com/en/actions/using-workflows/storing-workflow-data-as-artifacts, https://github.blog/news-insights/product-news/supercharging-github-actions-with-job-summaries, https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax, https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations, https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows, https://github.com/orgs/community/discussions/86087, https://github.com/rhysd/actionlint/releases, https://github.com/rhysd/actionlint/blob/main/docs/usage.md, https://docs.zizmor.sh, https://docs.zizmor.sh/usage/, https://github.com/zizmorcore/zizmor/releases, https://github.com/nektos/act.

Contents: timeouts · concurrency and cancellation · cache key and
restore-keys semantics · artifacts v4 · matrix and other limits ·
`GITHUB_STEP_SUMMARY` · scheduled workflows going dark · actionlint ·
zizmor · act · sources.

## Timeouts

`jobs.<job_id>.timeout-minutes` defaults to 360 (6 hours) —
https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax
— and the limits page caps a hosted job at 6 hours and a self-hosted job at
5 days. Set `timeout-minutes` explicitly on any job that runs steps rather
than trusting the default, hosted or self-hosted. A job that calls a
reusable workflow takes no `timeout-minutes`: the reusing-workflows page
says only a fixed list of keys may sit in the calling job (`name`, `uses`,
`with`, `secrets`, `strategy`, `needs`, `if`, `concurrency`, `permissions`,
`cache-mode`), and this is not among them; the callee's jobs carry their
own.

## Concurrency and cancellation

```yaml
concurrency:
  group: deploy-${{ github.ref }}
  cancel-in-progress: true
```

`cancel-in-progress: true` cancels other in-flight runs sharing the same
`group` value when a new run starts. Without it, at most one run or job
waits as `pending` per group and a newer arrival cancels the pending one
(`queue: single`, the default). `queue: max` lets up to 100 wait in FIFO
order and cancels any beyond that; combining `queue: max` with
`cancel-in-progress: true` is a workflow validation error —
https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax.

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
cap, eviction is by last access, least recently used first. Caches are branch-scoped: a run sees caches
from its own branch and from the repository's default branch, and a pull
request run also sees the base branch's caches — there is no cross-branch
sharing beyond that (child and sibling branches are invisible). A cache
saved by a pull request run is scoped to its merge ref and restores only
in re-runs of that pull request. This scoping is also why a `restore-keys` prefix can
be poisoned across a trust boundary (see `supply-chain.md`): a fork PR
run's cache is visible to the base-branch caches it inherits from, not the
other way, but the prefix match doesn't itself check who wrote the entry.

## Artifacts v4: immutable

Artifacts uploaded with the v4 actions are immutable within a run: you
cannot overwrite an artifact by uploading the same name twice in one run.
Downloading an artifact from a different run needs an authentication
token and the run's ID, not just the artifact name —
https://docs.github.com/en/actions/using-workflows/storing-workflow-data-as-artifacts.
The page gives no default retention period or size cap: `retention-days`
may not exceed the limit set by the repository, organization or
enterprise, and per-plan artifact storage is on the limits page. Check
those pages before assuming a specific number.

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
| `GITHUB_TOKEN` API rate | 1,000 requests/hour/repo (15,000 on Enterprise Cloud) |
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

The events-that-trigger-workflows page says: "In a public repository,
scheduled workflows are automatically disabled when no repository activity
has occurred in 60 days." It does not define "activity" (that commits
count and issues, pull requests, tags and releases do not is community
lore, not a docs statement) and does not say what private repositories do.
A commit by a user with write access that changes the workflow's `cron`
reactivates it. Community thread, for the field report only:
https://github.com/orgs/community/discussions/86087. In practice: a
scheduled workflow on a low-traffic repo needs either regular commits or
a periodic manual re-enable; a "why did the nightly job stop running"
report is very often this.

## Tools

### actionlint

Version 1.7.12, released 2026-03-30 —
https://github.com/rhysd/actionlint/releases. Install via
`go install github.com/rhysd/actionlint/cmd/actionlint@latest`, Homebrew,
a prebuilt binary, the download script or the Docker image. There is no
built-in `sarif` format: `-format` takes a Go template, and the repository
ships a SARIF one at `testdata/format/sarif_template.txt` to feed it for
upload as a code scanning result. Exit codes, per
https://github.com/rhysd/actionlint/blob/main/docs/usage.md: `0` clean,
`1` problems found, `2` invalid command-line option, `3` fatal error — a
preflight script should treat `2` and `3` as their own failures, not as
"no problems found."

### zizmor

Version 1.31.1, released 2026-10-15 —
https://github.com/zizmorcore/zizmor/releases. Install with `pip install
zizmor`, `pipx install zizmor`, or `uv tool install zizmor`. Runs offline
unless `GH_TOKEN`, `GITHUB_TOKEN` or `ZIZMOR_GITHUB_TOKEN` is set (a token
turns online audits on; `--offline` forces it off) and emits SARIF with
`--format=sarif`. `--persona=pedantic` or `--persona=auditor` widen the
finding set beyond the default `regular` persona. Exit codes: `0` no
findings, `1` audit error, `2` bad arguments, `3` no inputs, and `11`-`14`
findings by highest severity (informational to high); the finding codes are
suppressed under `--format=sarif` and `--no-exit-codes`, so a gate that
reads the exit status must not use SARIF output — https://docs.zizmor.sh/usage/.

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
60-day schedule rule is a direct docs quote, but what counts as activity
is undocumented and the community thread is anecdote. Fetched 2026-10-29.
