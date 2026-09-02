# Secret and identifier leakage

Checks 1 to 5 of the catalogue, in observed-impact order. Each: what
happened, the check, the fix, when the check is wrong.

## Contents

1. archive-hygiene
2. history-secrets
3. credential-patterns
4. identifier-shapes
5. config-endpoint-secrets

## 1. archive-hygiene

**What happened.** A release zip was made from the working directory. The
working directory had `.env`. The zip had `.env`.

**Check.** Build distribution artifacts from `git archive`, so only tracked
files *can* appear:

```
git archive --format=zip -o dist/app.zip HEAD
python3 readiness.py --only archive-hygiene --archive dist/app.zip
```

The scanner enumerates the archive and fails on `.env*`, `.git/`, caches,
`node_modules/`, key files (`.pem`, `.key`, `.p12`, `id_rsa`), and on
key-shaped strings inside text entries (private key blocks, cloud access
keys, publishable-vs-secret key prefixes, JWTs, `secret = "..."` literals).
It also counts ignored files present in the working tree, which is the
number a working-tree zip would have shipped.

**Fix.** The build step uses `git archive`; the release workflow runs the
check on the artifact it is about to upload.

**When it is wrong.** The `secret = "..."` literal rule matches test
fixtures with fake secrets. Scope them out with `archive_ignore_globs` in
`.readiness.json` rather than disabling the rule.

## 2. history-secrets

**What happened.** A secret was committed, then removed in a later commit.
The repository was published. The secret was in the history.

**Check.** A full-history scan, which needs a full clone:

```
gitleaks detect --source . --no-banner --report-format json --report-path .readiness/gitleaks.json
```

In CI the checkout must use `fetch-depth: 0`; the scanner fails a workflow
that runs a secret scan on a shallow checkout. The report's `Secret` and
`Match` fields are never copied anywhere; the scanner keeps `File`,
`StartLine`, `RuleID`.

**Fix.** Rotate the secret first (history rewriting does not un-publish
it), then rewrite history if the repository is private and small enough to
re-clone everywhere, or accept the rotation as the fix.

**When it is wrong.** Generic rules flag high-entropy strings in lockfiles
and test vectors. Add a `.gitleaksignore` with the fingerprint, not a
blanket path allowlist.

## 3. credential-patterns

**What happened.** Hosted push protection recognizes registered partner
formats. The organization's own credential format was not one of them, so
the scanner that mattered most was not scanning for the format that
mattered most.

**Check.** Register a custom pattern with the hosting provider's secret
scanning, and put the same regex in `.readiness.json`:

```json
{"credential_patterns": [
  {"name": "org-api-key", "regex": "\\bORGKEY_[A-Za-z0-9]{32}\\b"}
]}
```

`python3 readiness.py --only credential-patterns --history 200` scans the
tip and the last 200 commits. Placeholder above; the real shape is the
organization's and stays in its config.

**When it is wrong.** A pattern without an anchor or a length matches
prose. Anchor with `\b`, fix the length, test the regex on a fixture before
shipping it.

## 4. identifier-shapes

**What happened.** "No real identifiers" was enforced on source and config.
Fixtures were not checked. The shipped tree carried real account ids in
test data.

**Check.** The exact shape of the identifier, scoped to fixture directories:

```json
{"identifier_patterns": [
  {"name": "tenant-id", "regex": "\\bT-[0-9]{8}-[A-Z]{2}\\b"}
]}
```

**When it is wrong.** This is the check with the worst false-positive
history. Coordinates, phone numbers and card numbers are all long digit
runs; a blanket numeric scan is noise, and noise gets the check disabled.
Match the prefix, the length and the separators, or do not ship the check.

## 5. config-endpoint-secrets

**What happened.** A config endpoint handed the browser what it needed:
publishable keys, feature flags, endpoints. Whether the client secret was
among them was checked by reading the field list.

**Check.** A test that serializes the response and asserts absence:

```python
def test_browser_config_carries_no_secret(client, monkeypatch):
    monkeypatch.setenv("CLIENT_SECRET", "canary-value-9f2c")
    body = client.get("/api/config").text
    assert "canary-value-9f2c" not in body
    assert "CLIENT_SECRET" not in body
```

The scanner lists config-shaped routes and reports whether an absence test
mentions each. Public keys in the response are fine; the property is that
the secret and the bearer token never reach the browser.

**When it is wrong.** A route the scanner names "config" may not serve the
browser at all. The auditor confirms the consumer before the row counts.
