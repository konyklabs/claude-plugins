# The first journey

Fetched 2026-10-29 (first 2026-10-29) from the vendorfake repository at tag v0.8.0 (0145227): docs/reference/control-plane.md, docs/concepts/unit.md, docs/concepts/chaos.md (Sharing one unit across tests), docs/start/quickstart.md, examples/pytest-consumer/test_clover.py.

## Contents

1. The five steps
2. The two red runs
3. The oracle
4. Per-test hygiene
5. Consumer-side things the fake cannot supply
6. Sources

## 1. The five steps

| Step | Route on the fake | What it proves |
| --- | --- | --- |
| 1. Authorize, redirects off | `GET /oauth/v2/authorize` (`Authorize`) | the consumer builds the vendor's URL; the fake answers 302 to the pinned callback with `code`, `merchant_id`, `client_id`, `state` |
| 2. Call the consumer's callback with `code`, `merchant_id`, `state` | `POST /oauth/v2/token` (`ExchangeToken`), called by the consumer | the consumer exchanges the code and redirects to its success path |
| 3. Read the stored POS connection row | none | the consumer persisted the merchant id and token it was given |
| 4. Present the stored token | `GET /v3/merchants/{mId}` (`GetMerchant`) | the stored token is a live one, not a placeholder |
| 5. Read the request log | `GET /__unit/requests` | the service called exactly what the test thinks it called |

The operation ids are what `vendorfake routes --vendor clover --json` prints at
0.8.0 (checked 2026-10-29); write no name that is not in that output.

The authorize flow as vendorfake's own example drives it, in process
(examples/pytest-consumer/test_clover.py:26-38,
https://github.com/konyklabs/vendorfake/blob/v0.8.0/examples/pytest-consumer/test_clover.py#L26-L38):

```python
    authorize = client.get("/oauth/v2/authorize", params={"client_id": seed.client_id})
    assert authorize.status_code == 302
    redirect = parse_qs(urlsplit(authorize.headers["location"]).query)
    assert redirect["merchant_id"] == [seed.merchant_id]
    code = redirect["code"][0]

    token = client.post(
        "/oauth/v2/token",
        json={"client_id": seed.client_id, "client_secret": seed.client_secret, "code": code},
    )
    assert token.status_code == 200, token.text
    granted = token.json()
    assert set(granted) == {"access_token", "access_token_expiration", "refresh_token", "refresh_token_expiration"}
```

Measured 2026-10-29 against 0.8.0 with `VENDORFAKE_VENDOR_REDIRECT_URI` pinned:
the authorize call carrying the pinned `redirect_uri` answered 302 with a
`Location` on that callback and the query keys `client_id`, `code`,
`merchant_id`, `state`; the token reply had the four keys above, its
`access_token_expiration` in epoch seconds; the merchant read with that token
answered 200; the request log then listed `GetMerchant`, `ExchangeToken`,
`Authorize` newest first (and an earlier, refused `Authorize` after them: the
log records 4xx answers too).

## 2. The two red runs

- **Fake unreachable** (`docker compose stop clover`): the first call fails
  with the test client's connection error before any assertion runs. If it
  does not, the test is not talking to the fake.
- **A code the vendor never issued** (`code=never-issued` on the callback): the
  consumer's token exchange is refused (the fake answered 401 to
  `POST /oauth/v2/token` with an unknown code, measured 2026-10-29) and the
  callback's `Location` is the consumer's failure path, not its success path.

A journey that cannot be made to fail for a stated reason proves nothing.

## 3. The oracle

The control-plane rows, control-plane.md
(https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/control-plane.md):

| Method | Path | Summary |
| --- | --- | --- |
| GET | `/__unit/requests` | "Every request the unit handled, newest first." |
| GET | `/__unit/requests/unmatched/near-misses` | "Unmatched requests, each with the routes it nearly asked for." |
| GET | `/__unit/journal` | "Append-only log of committed state mutations." |
| POST | `/__unit/state/reset` | "Wipe state and re-apply the seed scenario." |
| GET | `/__unit/manifest` | "Credentials, webhook keys and entity ids: what an end-to-end script needs to address this unit." |

unit.md on the request log: "`GET /__unit/requests` (filters: `operation_id`,
`route`, `unmatched`, `limit`), `DELETE /__unit/requests` and
`GET /__unit/requests/unmatched/near-misses` answer what actually landed on
this unit, matched or not, 2xx or 4xx." and "It records no bodies and no
headers, control-plane calls never appear in it, and `reset()` clears it with
the state store." —
https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/unit.md#the-request-log-what-was-called

unit.md on the journal: "only a *committed* mutation appears: a read, a 4xx and
a call that matched no route leave no trace here at all." —
https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/unit.md#the-journal-what-changed

unit.md on a wrong path: "A path this unit does not serve answers the vendor's
own 404 on the wire, on every binding, with a `Vendorfake-Near-Miss` header
naming the closest routes." —
https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/unit.md#unmatched-requests

chaos.md on reset: "`POST /__unit/state/reset` — `driver.reset()` — re-hydrates
the store from the seed document with no restart, putting the seeded
single-use token back, and clears the request log and the journal with it.
Armed chaos rules are the one thing it leaves in place." and "**A virtual clock
is not rewound.**" —
https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/chaos.md#sharing-one-unit-across-tests

Address the unit from `GET /__unit/manifest` in a script, never from memory:
quickstart.md's rule for ids is "read it from the response", not "this literal
string" —
https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/quickstart.md

## 4. Per-test hygiene

- Per test: `POST /__unit/state/reset` and `POST /__unit/chaos/reset`. chaos.md:
  "The same two calls over HTTP, for a suite in another language or a container".
- Subscribe after the reset: "`reset()` also drops every webhook subscriber a
  test registered — subscribe *after* the reset, not in a session fixture above
  it." (chaos.md, same section).
- Single-use state is why a shared unit flakes: "Clover retires a refresh token
  the moment it is used, so the second test in a session to refresh gets
  Clover's real `401` for a reason unrelated to what it tests ... Under
  `pytest-randomly` *which* test pays changes run to run, which reads as a flake
  and is not one." (chaos.md, same section).
- One networked unit per concurrent run, bindings.md: "Two test runs driving one
  fake at the same time reset and fault each other, so concurrent runs each need
  their own process" —
  https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md#a-control-plane-on-its-own-port

## 5. Consumer-side things the fake cannot supply

<!-- this section: the repository's own distillation; no vendorfake page -->

- **An idempotent seed.** The consumer's own test-data command runs at init and
  must be safe to run again: the record the journey connects (a company with no
  POS connection yet) has to exist on every `compose up`, and a second run must
  not fail on a unique key or leave the first run's connection in place.
- **A post-callback redirect base.** A consumer that redirects a browser after
  its callback reads a front-end base URL the fake has no answer for; set an
  obvious placeholder (`http://frontend.invalid`) and assert on the path.
- **The stored row.** Step 3 reads the consumer's own storage (its database, or
  an internal read endpoint); the fake can only show what the consumer called.

## 6. Sources

- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/control-plane.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/unit.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/chaos.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/quickstart.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/examples/pytest-consumer/test_clover.py

The docs give no end-to-end journey for a consumer behind compose; the steps
and red runs here are this repository's, built on the quoted pages. Fetched 2026-10-29.
