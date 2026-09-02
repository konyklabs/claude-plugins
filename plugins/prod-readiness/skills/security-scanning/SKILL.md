---
name: security-scanning
description: The security half of a readiness review — secret and identifier leakage (distribution archives, full-history scans, custom credential formats, fixtures, the browser config endpoint), debug and observability surfaces (redaction at publish, debug endpoints, HTML-string sinks), and the external scanners run from official sources with small output. Use when a readiness scan reports a secrets or debug-surfaces row, when asked about secret leaks, push protection, gitleaks, pip-audit, bandit, semgrep, trivy, or before publishing a sample app.
---

# Security scanning

Nine checks and a set of scanners. Each check below names what it catches,
the command that answers it with small output, and when it is wrong. The
detail, the fix, and the placeholders are in the references; load the one
the row needs.

## Secret and identifier leakage — `references/secrets.md`

| id | catches | answer |
|---|---|---|
| archive-hygiene | an archive built from the working tree ships `.env`, caches, keys | build from `git archive`; the scanner enumerates any archive and fails on the wrong entries and key-shaped strings |
| history-secrets | a secret removed later is still published in history | `gitleaks detect` on a full clone (`fetch-depth: 0` in CI); tip-only scans are theater |
| credential-patterns | your own credential format is unknown to hosted push protection | a registered custom pattern upstream, and the same regex in `.readiness.json` here |
| identifier-shapes | real account or tenant ids in fixtures, examples, docs | the exact shape as a regex, scoped to fixture directories; never a bare digit-run scan |
| config-endpoint-secrets | the config the browser is handed | a test that asserts the secret is absent from the serialized response |

## Debug and observability surfaces — `references/debug-surfaces.md`

| id | catches | answer |
|---|---|---|
| redaction-at-publish | an inspector that scrubs in the client and replays unredacted history to every new subscriber | redact before the buffer, state allowlist or denylist as a decision |
| debug-endpoint-exposure | a debug route reachable once the server binds `0.0.0.0` | a per-request loopback check plus a per-process token that is a tripwire, not auth |
| html-sinks | upstream-controlled strings reaching `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `DOMParser`, `createContextualFragment`, `eval`, `new Function` | a test over the static directory; a hand review missed one |

## External scanners — `references/scanners.md`

The scanner runs whichever of these is on PATH and reduces the output to
counts by severity and rule; the rest is a `skip` row with the command and
the official source. Install from the maintainer's release page or the
package index, pinned by version and checksum, never through a third-party
wrapper action; the reference has the lines.

| tool | scope | maintainer |
|---|---|---|
| gitleaks | secrets in the full history | Gitleaks project |
| pip-audit | known vulnerabilities in resolved Python dependencies | PyPA |
| bandit | Python static analysis | PyCQA |
| semgrep | pattern rules, local rules only (registry configs need network) | Semgrep |
| osv-scanner | lockfiles against the OSV database | Google |
| trivy | filesystem and container images | Aqua Security |
| npm audit | JavaScript dependencies | npm |
| lychee | links in docs (makes network requests; run deliberately) | lychee project |

## Rules that apply to every check here

- A finding is a location and a rule name. The matched text never appears
  in a report, a comment, or an issue.
- A check with a known false-positive shape ships with that note in its
  row; a noisy check is scoped through the config, not disabled.
- Scanner output is summarized before it reaches the conductor. If a tool
  prints a thousand lines, the scanner agent reads them, not the session on
  the expensive model.
