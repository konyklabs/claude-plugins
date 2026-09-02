---
name: readiness-review
description: Production-readiness and security review of an application, token-cheap — one deterministic scan emits a categorized report, external scanners are summarized to counts, an auditor judges only what needs judgment, and the conductor reads a table. Use before a release, before publishing a sample app or a portal, when asked to check for security holes, secret leakage, denial-of-service exposure, or production readiness, and as a pre-commit subset.
---

# Readiness review

The expensive part of a security review is reading code. This workflow reads
the code once, with a script, and turns it into a table of checks with a
status each: `pass`, `fail`, `review`, `skip`. The model then spends its
tokens on the `review` rows, which are the ones that need judgment, and on
nothing else.

The catalogue comes from a real hardening pass: these are the things that
bit, ordered by how much they cost when they did. A partial run covers what
matters first.

## The two tiers

```
python3 "${CLAUDE_SKILL_DIR}/scripts/readiness.py" --tier precommit   # seconds; archive, identifiers, HTML sinks, custom credentials
python3 "${CLAUDE_SKILL_DIR}/scripts/readiness.py" --tier release     # everything, plus the installed external scanners
```

Stdout is a bounded markdown summary; the full report is written to
`.readiness/report.json` (add `.readiness/` to `.gitignore`). Findings
carry `path:line` and a rule name, never the matched text, so the report
is safe to paste into an issue. Exit code 1 means at least one `fail`.

The script installs nothing and makes no network calls; external tools run
only if already on PATH and are otherwise listed as `skip` with the exact
command and their official source (`security-scanning` skill, `references/scanners.md`).

## The workflow

1. **Scan.** Spawn `prod-readiness:scanner` (Sonnet) with the root and the
   tier; it runs the script and the installed tools and returns the
   summary in the worker report format. Do not run the scan in the
   conductor's context: the summary is bounded, tool output is not.
2. **Read the table.** `fail` rows are defects with a location; they go to
   an implementer as a spec (`/governor:delegate`) with the finding's
   `command` as the test to run. `skip` rows say what was not checked and
   why; decide whether that is acceptable for this release and say so.
3. **Judge the `review` rows.** Spawn `prod-readiness:auditor` (Opus) with
   the report path and the check ids to judge; it reads the cited sites and
   returns findings with failure scenarios, or clears them. It never fixes.
4. **The items no scanner sees.** The correctness and documentation checks
   that need running code or a reader (silent async failures, vendor mode
   mismatches, wrong troubleshooting text) are listed in
   `hardening-checks` with the test or the question that settles each.
   Delegate those tests as slices.
5. **Report.** One table, blocking / should-fix / accepted, each row with
   its check id, its location, and its evidence (the scan line or the test
   output). Record it durably; a readiness verdict that lives in a session
   is not a verdict.

## Check index

| id | tier | group | what it catches |
|---|---|---|---|
| archive-hygiene | precommit | secrets | a distribution archive built from the working tree carrying `.env`, caches, keys |
| credential-patterns | precommit | secrets | your own credential format, which hosted push protection does not know |
| identifier-shapes | precommit | secrets | real tenant or account ids in fixtures and docs |
| html-sinks | precommit | debug-surfaces | upstream strings reaching `innerHTML` and friends |
| history-secrets | release | secrets | a secret deleted in a later commit, still in history |
| config-endpoint-secrets | release | secrets | the config the browser is handed, asserted by absence |
| redaction-at-publish | release | debug-surfaces | inspectors that redact at render and replay unredacted history |
| debug-endpoint-exposure | release | debug-surfaces | a debug route without a per-request loopback check |
| async-terminal-states | release | correctness | create-then-poll flows whose tests assert only the create |
| vendor-mode-probes | release | correctness | test and live key modes mixed; capabilities inferred from a key |
| idempotency-keys | release | correctness | money-moving POSTs without one key per attempt |
| client-supplied-money | release | correctness | a client total forwarded without server recomputation |
| skipped-credentialed-tiers | release | ci-supply-chain | a green tick on a tier that skipped |
| contract-artifact-drift | release | ci-supply-chain | a regenerated pact or snapshot not diffed in CI |
| action-pinning | release | ci-supply-chain | third-party actions not pinned to a SHA; relicensed actions |
| runtime-version-drift | release | ci-supply-chain | CI, container, devcontainer and lockfile on different interpreters |
| docs-endpoint-drift | release | docs | documented endpoints not in the OpenAPI document and vice versa |
| dos-surface | release | dos | body limits, timeouts, rate limits, concurrency caps, pagination caps, ReDoS, uploads, container hardening, CORS, debug flags |
| tools | release | tools | gitleaks, pip-audit, bandit, semgrep, osv-scanner, trivy, npm audit, lychee, summarized to counts |

Each check's detail, the false-positive note, and the fix live in the two
domain skills: `security-scanning` (secrets, debug surfaces, external
scanners) and `hardening-checks` (DoS, correctness, CI and supply chain,
documentation). Load the one the row belongs to; not both.

## Configuration

`.readiness.json` at the root, all optional: `credential_patterns` and
`identifier_patterns` (lists of `{name, regex}`; the checks are disabled
until set, because an unshaped scan is noise), `archive_ignore_globs`,
`semgrep_config` (a local rules path; the registry configs need network),
`docs_dirs`, `disable` (check ids). Placeholders for the patterns are in
`security-scanning/references/secrets.md`.

## Token discipline

- The conductor reads the markdown summary and the auditor's JSON. It does
  not open `.readiness/report.json` unless a row needs the full finding list.
- One scan per review, not one grep per question. If a question the table
  does not answer comes up, add a check to the script rather than reading
  files by hand; the next review gets it for free.
- A noisy check gets disabled, and a disabled check is worse than none:
  when a check is wrong for this codebase, fix its pattern or scope it
  through the config, and say so in the report.
