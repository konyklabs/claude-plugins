# External scanners

What the `tools` check runs, how each is installed from its maintainer,
and how its output is kept small. The scanner never installs anything: a
tool that is not on PATH is a `skip` row with the command below.

## Contents

1. Install discipline
2. gitleaks
3. pip-audit
4. bandit
5. semgrep
6. osv-scanner
7. trivy
8. npm audit
9. lychee
10. Output discipline

## 1. Install discipline

- Install from the maintainer's release page or the package index, pinned
  to a version, with the checksum verified. Not through a third-party
  wrapper action: several widely used CI actions have relicensed to terms
  that need a per-organization key, and a wrapper is one more party in the
  chain.
- In CI, download the release asset by version, check its `sha256` against
  the value committed in the repository, then run it. The pattern:

```
VERSION=x.y.z
curl -sSL -o tool.tar.gz "https://github.com/<org>/<tool>/releases/download/v${VERSION}/<tool>_${VERSION}_linux_x64.tar.gz"
echo "<sha256 from the release page>  tool.tar.gz" | sha256sum -c -
tar -xzf tool.tar.gz
```

- Python tools go in a separate tool environment (`uv tool install
  pip-audit==x.y.z`, or `pipx`), never into the application's lockfile.

## 2. gitleaks

Secrets across the full git history. Maintainer: the Gitleaks project,
https://github.com/gitleaks/gitleaks/releases.

```
gitleaks detect --source . --no-banner --report-format json --report-path .readiness/gitleaks.json
```

Exit code 1 means findings. Summarize to counts by `RuleID` and
`File:StartLine`; never surface `Secret` or `Match`. Needs a full clone.

## 3. pip-audit

Known vulnerabilities in the resolved Python dependency set. Maintainer:
PyPA, https://pypi.org/project/pip-audit/.

```
pip-audit -f json                       # the active environment
pip-audit -r requirements.txt -f json   # a requirements file
```

Summarize to vulnerable packages and vulnerability ids. Run against the
lockfile-resolved environment, not a dev venv with extras.

## 4. bandit

Python static analysis for common security mistakes. Maintainer: PyCQA,
https://pypi.org/project/bandit/.

```
bandit -r src -f json -q
```

Summarize by `issue_severity` and `test_id`. Exclude `tests/`; assert
statements and hard-coded test passwords are the usual noise.

## 5. semgrep

Pattern rules over source. Maintainer: Semgrep,
https://pypi.org/project/semgrep/. The registry configs (`--config auto`,
`p/ci`) fetch rules over the network at run time; in an audited
environment, vendor the rules you use into the repository and point the
scanner at them:

```json
{"semgrep_config": "tools/semgrep-rules"}
```

```
semgrep --json --config tools/semgrep-rules src
```

Summarize by `check_id` and severity.

## 6. osv-scanner

Lockfiles against the OSV database. Maintainer: Google,
https://github.com/google/osv-scanner/releases.

```
osv-scanner --format json -r .
```

Summarize to vulnerability count by package. Overlaps pip-audit for
Python; keep both when the project also has JavaScript or container
lockfiles.

## 7. trivy

Filesystem and image scanning. Maintainer: Aqua Security,
https://github.com/aquasecurity/trivy/releases.

```
trivy fs --format json .
trivy image --format json <image:tag>
```

Summarize by `Severity`. Note that `trivy` downloads its vulnerability
database on first run; in an air-gapped CI, use `--skip-db-update` with a
pre-fetched database.

## 8. npm audit

JavaScript dependencies, when a `package-lock.json` exists. Maintainer:
npm (ships with Node).

```
npm audit --json
```

Summarize `metadata.vulnerabilities` by severity.

## 9. lychee

Broken links in documentation. Maintainer: the lychee project,
https://github.com/lycheeverse/lychee/releases.

```
lychee --format json docs README.md
```

This tool makes network requests to every link. Run it deliberately, not
as part of an offline scan; the `tools` check runs it only if installed and
says so in its row.

## 10. Output discipline

Every tool above can print thousands of lines. The `tools` check reduces
each to counts and the first few locations, and the full JSON stays in
`.readiness/`. The scanner agent reads the full output when a count needs
explaining; the conductor never does.
