---
name: hardening-checks
description: The production-readiness half of a review — denial-of-service surface (body and upload limits, timeouts, rate limits, concurrency and pagination caps, regex DoS, container hardening, CORS, debug flags), correctness traps that pass their tests (silent async failure, vendor key modes, token generations, idempotency keys, server-authoritative money, entity rate limits, error bodies), CI and supply chain (skipped tiers, contract drift, action pinning, runtime versions, licence allowlists, link checks), and documentation as a failure surface. Use when a readiness scan reports a dos, correctness, ci-supply-chain or docs row, when asked about DoS exposure, rate limiting, timeouts, idempotency, or why a green build shipped a bug.
---

# Hardening checks

Sixteen checks in four groups, ordered by what they cost when they were
missed. The scanner settles the mechanical ones; the rest name the test or
the question that settles them. Load the reference for the row in hand.

## Denial-of-service surface — `references/dos-limits.md`

One scanner check, `dos-surface`, with a sub-row per control:

| control | present looks like |
|---|---|
| request body size limit | `client_max_body_size`, `MAX_CONTENT_LENGTH`, a body-limit middleware |
| outbound timeouts | every `httpx`/`requests` call with `timeout=`, or a client built with one |
| rate limiting | `slowapi`, a limiter middleware, a proxy rule |
| concurrency and keep-alive caps | `--limit-concurrency`, `--timeout-keep-alive`, worker counts in the process manager |
| pagination caps | `limit` parameters with `le=` or a clamp |
| regex DoS | no nested quantifiers in `re.compile` patterns fed user input |
| upload limits | a size check before reading an upload |
| container hardening | `HEALTHCHECK`, non-root `USER`, base image pinned by digest, compose resource limits |
| CORS | never `allow_origins=["*"]` together with `allow_credentials=True` |
| debug flags | no `debug=True`, `DEBUG = True`, `reload=True` in non-test code |
| graceful shutdown | a lifespan or signal handler that drains in-flight requests |

## Correctness traps — `references/correctness.md`

| id | catches | settled by |
|---|---|---|
| async-terminal-states | a 2xx create whose resource later fails silently | a test that polls to a terminal state and asserts it is not a failed one |
| vendor-mode-probes | test versus live keys, permissions that differ between accounts | a probe call at startup or in a smoke test; never infer a capability from a key |
| token generations | two API generations of one provider emitting different id prefixes | a fixture per generation, and the same async test |
| idempotency-keys | retries that double-charge or declines that never retry | one key per attempt, minted before the call, reused for that attempt's retry, new after a decline |
| client-supplied-money | a client total forwarded as authority | evidence of upstream recomputation at the forwarding site, or the forward is the bug |
| entity rate limits | a per-entity cooldown returning a generic 4xx that reads as flakiness | documentation of the limit and a test that names the cause |
| error bodies | an upstream that mostly emits `problem+json` and sometimes does not | a consumer contract that branches on content type |

## CI and supply chain — `references/ci-supply-chain.md`

| id | catches | settled by |
|---|---|---|
| skipped-credentialed-tiers | a green tick on a tier that skipped for want of a secret | credentials required wherever expected; skip only where the platform withholds them (fork PRs) |
| contract-artifact-drift | a regenerated pact or snapshot that quietly went stale | `git diff --exit-code` after the suite in CI |
| action-pinning | third-party actions on a tag or branch; relicensed actions | SHA pins, or the upstream CLI by version and checksum |
| runtime-version-drift | CI, container, devcontainer and lockfile on different interpreters | one version, asserted in a test |
| licence allowlist | an automated bump that swaps a licence | a test over the resolved lockfile |
| link checking | published guide URLs that moved | `lychee` on the docs, deliberately |

## Documentation as a failure surface — `references/docs.md`

| id | catches | settled by |
|---|---|---|
| docs-endpoint-drift | documented endpoints not in the OpenAPI document, and the reverse | the scanner's drift table |
| troubleshooting symptoms | text written from inference and never revisited | each symptom mapped to a code path that can produce it |
| upstream examples | a provider's own guide showing a format its gateway rejects | samples executed in CI, not read |
| unsupported claims | a sample asserting a model the API never describes | every claim traced to a spec line |

## Rules that apply here

- A `fail` row is a spec for an implementer: the location, the control to
  add, the scanner's `command` as the test.
- A `review` row is a question for the auditor with the cited sites; the
  auditor answers with a failure scenario or clears it.
- The correctness traps mostly need running code. They are test slices,
  delegated, with the assertion written in the reference; a readiness
  review that only ran the scanner has not covered them and says so.
