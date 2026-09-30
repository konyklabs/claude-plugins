---
name: preflighting-workflows
description: The deterministic checks to run on a repository's GitHub Actions workflows before a review is asked for or a branch is pushed — actionlint and zizmor when on PATH reduced to counts and path:line rows, and the structural checks no linter owns (an unpinned action, a missing or write-all permissions block, a fork head checked out under pull_request_target, event text inside run:, a job with no timeout, a deploy with no concurrency, secrets inherited across organisations, a schedule with no concurrency), failing closed on the blocking ones. Use before pushing a workflow change, when asked whether a workflow is safe, as the first step of any Actions review, or to re-run one check after a fix.
---

# Preflighting GitHub Actions workflows

One command, one table, and the table goes into the PR body.

```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/preflighting-workflows/scripts/preflight.py" .            # the repository root
python3 "${CLAUDE_PLUGIN_ROOT}/skills/preflighting-workflows/scripts/preflight.py" . --json     # for tooling
python3 "${CLAUDE_PLUGIN_ROOT}/skills/preflighting-workflows/scripts/preflight.py" . --allow-unpinned ORG/.github/
```

Exit 0: nothing blocking. Exit 2: at least one blocking row, or an
external tool that ran and failed. Exit 1: no `.github/workflows`
directory. The script installs nothing and opens no sockets; a tool that
is missing, errors or times out is a `skip` row with its reason, never a
pass. It prints no workflow text: a finding is `path:line rule`.

## What runs

**External tools, when on PATH.** `actionlint` (syntax, expression types,
runner labels, shell errors through shellcheck when present) and `zizmor`
(the security audits: template injection, dangerous triggers, excessive
permissions, unpinned uses, impostor commits, cache poisoning, secrets
inherit and more). Both are reduced to a count and `path:line tool/rule`
rows; their messages are not printed. Install them once per machine;
`zizmor` is offline by default and needs no token.

**Structural rules, always.** These are the traps a linter does not own or
a repository may have configured away:

| rule | severity | what it means |
|---|---|---|
| `parse` | blocking | `path:line parse-incomplete`: a line the scanner could not place (rules still ran on the rest); `path:1 parse-error`: the file could not be parsed at all (nesting deeper than 64, or a scanner fault). Never a silent pass |
| `uses-unpinned` | blocking | a `uses:` whose ref is not a 40-hex SHA (or a Docker image with no digest), quoted or not; `./local` paths and `--allow-unpinned` prefixes are exempt. By SHA shape only: whether the commit exists in that repository is zizmor's impostor-commit audit |
| `uses-version-comment-missing` | minor | a SHA pin with no `# vN` comment (after the value, quoted or not), so Dependabot cannot bump it |
| `permissions-missing` | minor | no top-level `permissions:` and a job with none of its own |
| `permissions-write-all` | blocking | `write-all` anywhere |
| `pull-request-target-checkout` | blocking | under `pull_request_target`, a checkout `ref:` that names the PR's head (`github.event.pull_request.head.*`, `github.head_ref`, `refs/pull/…/head` or `…/merge`), or a `repository:` that names the head repository (`github.event.pull_request.head.repo.*`), with or without a `ref:`; a `base.*` ref is not flagged |
| `expression-injection` | blocking | any `${{ … }}` in a `run:` or `script:` body (inside `toJSON`, `format`, `\|\|`, `&&` too, bracket access read as dots) reading `github.head_ref`; a `github.event.*` path ending `.title`, `.body`, `.message`, `.page_name`, `.head_branch`, `.default_branch` or `.email`; `github.event.pull_request.head.ref` or `.head.label`; anything under `.head_commit` but `.id` and `.sha`; anything under `.commits`; a whole event object that carries such text (`github.event`, `.issue`, `.pull_request`, `.comment`, `.review`, `.review_comment`, `.discussion`, `.discussion_comment`, `.head_commit`, `.commits`), interpolated or passed to a function. Not flagged: `.number`, `.id`, `.sha`, `github.sha`, `github.event.repository.name` |
| `inputs-in-run` | minor | `inputs.*` or `github.event.inputs.*` inside `run:`: the same class one trust level up |
| `timeout-missing` | minor | a job with no `timeout-minutes` |
| `concurrency-missing-on-deploy` | minor | a deploy, release, apply or publish job with no `concurrency` at either level |
| `secrets-inherit-external` | blocking | `secrets: inherit` on a callee outside the organisation, which cannot work and states an intent to over-share |
| `schedule-without-concurrency` | minor | a scheduled workflow with no workflow-level `concurrency` |

The org-internal allowlist matters: an organisation's own reusable
workflows are often called at `@main` on purpose. The default exempts
`konyklabs/.github/`; pass `--allow-unpinned` with your organisation's
prefix, and write the policy down in the repository's rules so the
review lens can cite it.

## Reading the table

- A `fail` row on a tool means the tool's own exit code; its rows are
  `path:line actionlint/<kind>` or `path:line zizmor/<audit>`. Fix or
  suppress in the tool's config, never in the preflight.
- A blocking structural row is fixed before the review: the lens treats
  the table as settled ground and will not re-report it, so an unfixed
  blocking row is a defect nobody reports twice.
- A `skip` row is a gap, not a pass: say in the PR body which tool was
  missing, so the reviewer knows what was not checked.
- `--json` carries the same rows with `severity`, `count`, `findings`
  (`path`, `line`, `rule`) and a `note`; paths are relative, sanitized
  and capped.

## After the preflight

Paste the table into the PR body under a "Preflight" heading, then ask for
the lens in `reviewing-workflows` (or the `workflow-reviewer` agent) with
the table attached. The lens's mandate starts where this table ends.

## Sources

actionlint usage docs (`-format`, exit codes 0/1/2, SARIF), the zizmor
docs (offline audits, personas, audit catalogue as summarised there), and
the docs.github.com pages the structural rules encode (security
hardening, `pull_request_target`, permissions, reusing workflows, limits);
fetched 2026-09-29. The rule set was calibrated on the planted fixture in
`../../tests/fixtures/planted-workflows/` with its answer key.
