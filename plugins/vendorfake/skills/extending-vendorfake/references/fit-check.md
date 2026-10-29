# The fit check

Fetched 2026-10-29 (first 2026-10-29) from vendorfake at tag v0.8.0 (0145227): docs/concepts/unit.md (near misses, request log), docs/reference/control-plane.md, docs/reference/cli.md, docs/reference/routes-clover.md.

## Contents

1. The inventory
2. `fit_check.py`
3. The runtime oracles
4. Known non-fits
5. Sources

## 1. The inventory

List every outbound vendor call from the consumer's **production code** (its HTTP
client module, its SDK calls), not from its documentation or its tests: a README
describes what the integration was meant to call, the code what it calls. Per call:

- the method and the path, with concrete ids as the code sends them (a template is
  fine too: `{mId}` matches any one segment either way);
- the fields the consumer reads from the answer;
- the statuses it branches on (a 404 it treats as "deleted", a 429 it retries).

Only method and path go into `calls.txt`; the fields and statuses go into the
issue if a row is missing, and into the tests if it fits.

## 2. `fit_check.py`

```
vendorfake routes --vendor clover --json > routes.json
python3 "${CLAUDE_SKILL_DIR}/scripts/fit_check.py" --routes routes.json --calls calls.txt [--mount clover] [--json]
```

- `--routes`: what `vendorfake routes --vendor V --json` prints, a JSON array of
  objects with `method`, `path`, `operation_id` (other keys ignored). cli.md: "--json
  Machine output: one JSON document on stdout, nothing else on stdout." —
  https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/cli.md (the startup log goes to stderr, so a redirect of stdout is clean).
- `--calls`: one `METHOD /path[?query]` per line; `#` lines and blank lines are
  ignored; a line without a method or with a path not starting `/` exits 2 naming the line,
  and so does a file with no calls left after that (fail closed: an inventory that
  matched nothing is not a fit).
- Matching: method upper-cased; query string dropped; trailing slash ignored; each
  `{placeholder}` in a template matches exactly one segment.
- Verdicts: `fit` with the operation id (two matching routes print both, joined by
  `|`); `method` when the path is served only under other methods, which are
  listed; `missing`.
- Exit codes: 0 every call fits; 1 any `method` or `missing`; 2 malformed input or no calls.
- `--mount V` strips a leading `/V` from every call, for a process that mounts
  several vendors under `/<vendor>/`. It is never guessed: a prefixed call without
  it is `missing`, which is the right answer for a wrong base URL.

A sample run against the 0.8.0 Clover table (2026-10-29):

```
fit      GET /oauth/v2/authorize  ->  Authorize
fit      POST /oauth/v2/token  ->  ExchangeToken
fit      GET /v3/merchants/HRVSTRYE12345  ->  GetMerchant
fit      GET /v3/merchants/HRVSTRYE12345/items  ->  GetItems
method   PUT /v3/merchants/HRVSTRYE12345/items/ESPRESSO00300  ->  GET, POST
missing  GET /v3/merchants/HRVSTRYE12345/shifts  ->  -
fit_check: 4 fit, 1 method-mismatch, 1 missing
```

The `method` row is real Clover behaviour the fake reproduces: routes-clover.md
lists `UpdateItem` as "Sparse update of an item (POST)" —
https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/routes-clover.md. A consumer sending PUT has a bug, not a gap.

## 3. The runtime oracles

The static check says what the table serves; these say what the consumer actually sent.

- unit.md: "A path this unit does not serve answers the vendor's own 404 on the
  wire, on every binding, with a `Vendorfake-Near-Miss` header naming the closest
  routes." — https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/unit.md#unmatched-requests
- In Python the drivers raise instead: "The Python drivers (`unit()`, `served()`,
  `serve_in_thread()`) turn that answer into `vendorfake.testing.UnmatchedRequest`,
  an `AssertionError`". `driver.requests(unmatched=True)` lists "anything that
  landed nowhere". — https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/unit.md#the-request-log-what-was-called
- control-plane.md: `GET /__unit/requests/unmatched/near-misses`, "Unmatched
  requests, each with the routes it nearly asked for." —
  https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/control-plane.md
- A multi-vendor process: "A path naming no mount is a 404 carrying a
  `vendorfake-mounts` header, which is worth checking first when a base URL looks
  right and everything 404s." — https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md#docker-compose
- unit.md's own table: "Why did my client get a 404 / near-miss?" — "Request log,
  `unmatched=True`, or the `Vendorfake-Near-Miss` header". —
  https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/unit.md#which-one-answers-your-question

## 4. Known non-fits

<!-- this section: the repository's own distillation of a first adoption; no vendorfake page states it as a list -->

- **Lightspeed.** vendorfake models Lightspeed Retail X-Series (seed.md's section
  is titled "Lightspeed Retail X-Series"). A consumer of the Restaurant K-Series
  API needs a new vendor package, not a missing route.
- **Square's SDK.** It has to be constructed with a base URL the test controls; if
  the consumer hard-wires the vendor host, the seam is the consumer's to add.
- **A route missing at one tag.** Check the next tag and `main` before filing:
  Square's `GET /v2/channels` (`ListChannels`) arrived in 0.8.0.

## 5. Sources

- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/unit.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/control-plane.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/cli.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/routes-clover.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/seed.md

The docs give the oracles but no procedure for an inventory; sections 1 and 4 are
this repository's. Fetched 2026-10-29.
