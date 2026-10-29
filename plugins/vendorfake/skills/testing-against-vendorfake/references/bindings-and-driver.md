# Bindings and the driver

Fetched 2026-10-29 (first 2026-10-29) from the vendorfake repository at tag v0.8.0 (0145227): docs/start/bindings.md, docs/concepts/unit.md, docs/concepts/seed.md, docs/reference/control-plane.md, examples/pytest-consumer/conftest.py and test_clover.py.

## Contents

1. The four bindings
2. `served(env=)`'s special variables
3. The pytest plugin
4. The driver
5. One contract, three bindings
6. Seeds
7. The control-plane rows a test uses
8. Sources

## 1. The four bindings

bindings.md: "Four ways to run a unit, from fastest-and-most-isolated to
most-like-production. All four are the same [unit](../concepts/unit.md) — same
state machine, same seed, same faults — differing only in how a client reaches
it." — https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md

In process, sync (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md#in-process-sync):

```python
from vendorfake.testing import unit

with unit("square") as square:
    seed = square.seed
    response = square.client.get("/v2/locations", headers=seed.auth)
```

"By default an unmatched request raises `vendorfake.testing.UnmatchedRequest`
(an `AssertionError`) rather than answering the vendor's 404".

In process, async (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md#in-process-async): "Three entry
points, all yielding the same `vendorfake.testing.StartedUnit`: `.async_client`
on a `unit(...)` block when your test is `async def` but your fixture is not,
`async_unit(...)` when your fixture is `async def` too, and the
`vendorfake_async_unit` fixture when you would rather write a marker than a
`with` block."

```python
@pytest_asyncio.fixture
async def clover():
    async with async_unit("clover") as started:
        yield started
```

Served (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md#served):

```python
from vendorfake.testing import served, unit, serve_in_thread

with served("square") as square:
    square.base_url  # a real http://127.0.0.1:PORT

with unit("square") as started:
    with serve_in_thread(started) as driver:
        driver.base_url  # a real http://127.0.0.1:PORT
```

"Reach for one of these when the code under test needs an actual `host:port` —
a service configured by URL, a language binding with no custom-transport seam,
or a webhook subscriber that must receive a real POST over loopback."

Container (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md#container): "One image, every vendor;
which one it serves is chosen at run time, never baked into the image. Reach for
this when the consumer under test is not a Python process".

## 2. `served(env=)`'s special variables

bindings.md (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md#served), "Four variables behave specially":

- "`VENDORFAKE_HOST`, `VENDORFAKE_PORT`, `VENDORFAKE_LOG_LEVEL` — refused with
  a `ValueError` naming the parameter to use, rather than silently beaten by
  the flag `served()` passes."
- "`VENDORFAKE_PROFILE` — honoured; an explicit `profile=` beats
  `VENDORFAKE_PROFILE_<VENDOR>`"
- "`VENDORFAKE_SEED` — refused, because `.seed` is derived from the vendor's
  constants and could not describe a child hydrated from another document."
- "`VENDORFAKE_SEED_OVERLAY` — refused in favour of `seed_overlay=`"

## 3. The pytest plugin

bindings.md (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md#the-pytest-plugin): "Installing
vendorfake registers exactly one `pytest11` entry point, `vendorfake` (module
`vendorfake.pytest`): one marker and three fixtures, no options, no session hook,
no autouse fixture."

```python
@pytest.mark.vendorfake(vendor, profile=None, env=None, seed=None, clock_start=None, unmatched=None, capabilities=None)
```

- `vendorfake_unit`: "function-scoped and yields the `StartedUnit` the marker
  describes, built fresh per test, which is what makes per-test `reset()`
  unnecessary here."
- `vendorfake_async_unit`: "yields the same kind of `StartedUnit`, for a test
  that drives it through `async_client` instead of `client`".
- `vendorfake_webhook_receiver`: "function-scoped and yields the same
  `WebhookReceiver` object `vendorfake.testing.webhook_receiver()` does — a real
  HTTP endpoint on loopback".

Narrowing the seed under a type checker, "Three ways out, in order of
preference": stay on the structural `Seed` (`seed.credentials`, `seed.token`);
"Narrow once with an assertion the checker understands and the runtime
enforces: `assert isinstance(seed, CloverSeed)`"; "Call `unit("clover")` from a
fixture of your own instead: the literal overload yields
`StartedUnit[CloverSeed]` with no assertion at all." —
https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md#narrowing-the-seed-under-a-type-checker

## 4. The driver

unit.md (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/unit.md#driver): "Every method below is expressed
against the control plane, which every binding serves identically".

| Method | unit.md's meaning |
| --- | --- |
| `subscribe(url, event_types, signature_key=)` | "checks the event types against the vendor's own vocabulary and refuses one it will never send." |
| `drain(timeout_s=)` | "sleeps the retry timers for real, scaled by the profile's `time_scale`, until every delivery has settled or the bound is hit" |
| `deliveries()` | "every delivery attempt the dispatcher recorded." |
| `add_chaos_rule(rule)` / `reset_chaos()` | "arm and disarm chaos rules without hand-rolling the `POST /__unit/chaos/rules` body." |
| `reset()` | "return to the seed scenario. It also drops every subscriber a test registered, so `subscribe()` again *after* a reset." |
| `assert_called(operation_id, times=)` | "raises with every operation the unit actually saw, and its count, in the failure message." |
| `requests(operation_id=, route=, unmatched=, limit=)` | "the request log, filtered." |
| `clear_requests()` | "draws a line under setup." |

Signatures checked against the installed 0.8.0 on 2026-10-29:
`subscribe(self, notification_url, event_types, signature_key, *, id=None)`,
`requests(self, *, operation_id=None, route=None, unmatched=None, limit=100)`,
`drain(self, *, timeout_s=120.0)`.

## 5. One contract, three bindings

bindings.md (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md#one-contract-three-bindings):

| Behaviour | In-process `unit()` | `served()` | CLI / container |
|---|---|---|---|
| Unmatched path, on the wire | 404 + `Vendorfake-Near-Miss` | 404 + `Vendorfake-Near-Miss` | 404 + `Vendorfake-Near-Miss` |
| Unmatched path, Python driver | raises `UnmatchedRequest`; `unmatched="vendor-404"` opts out | the same | no driver |
| `timeout` fault, real clock | the client's own `ReadTimeout`, raised without waiting when the delay exceeds it | the client's own `ReadTimeout`, after waiting | the same |
| Seeds, chaos rules, reset, the control plane | identical | identical | identical |

## 6. Seeds

seed.md (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/seed.md#what-every-seed-has-in-common): "Five fields
on `vendorfake.testing.Seed`, for a test parametrized across vendors":
`token`, `credentials`, `auth`, `read_only_auth`, `event_types` ("the webhook
event vocabulary this vendor actually sends").

Token lifetimes (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/seed.md#token-lifetimes): "Every seed token
field is a lifetime *relative to unit start*"; Clover's are
`access_token_expires_in_ms`, `refresh_token_expires_in_ms`; "`0` means expired
the instant the unit starts." On a running unit, Clover's entity fields are
`access_token_expiration_ms` and `refresh_token_expiration_ms` "(epoch ms)".

Overlays (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/seed.md#seed-overlays): `seed_overlay=` "takes a
**partial** seed document and merges it over the profile's before the store is
hydrated". Refused: a collection the vendor does not have ("Conformance clause
**C36** asserts the refusal on every vendor."), and "On Square and Clover that is
`tokens` and `merchant`; on Toast, `tokens` and `restaurant`". "To run a unit on
different credentials or a different tenant, point the profile at a whole seed
document of your own — its `seed` key, or `VENDORFAKE_SEED`".

Ids (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/seed.md#ids-are-deterministic-not-unique): "Two
`unit("square")` blocks mint the same order ids, tokens and codes in the same
order, from separate stores". Pass `seed=2` for two units that must diverge.

The seeded values per vendor are seed.md's "The shipped scenario" tables
(https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/seed.md#the-shipped-scenario); Clover's: app
`UNITCLOVERAPP` / `unit-clover-app-secret`, merchant `HRVSTRYE12345` in every
`/v3` path, items `CRAFTBEER0750`, `ESPRESSO00300` (modifier group
`MODGROUPMILK1`), `CROISSANT0450`. Square's: app
`sandbox-sq0idb-unit-square-application`, location `18YC4JDH91E1H`, merchant
`MLQW2MYBY81PZ` implicit in the token. Read them as `seed.<field>` in a test.

## 7. The control-plane rows a test uses

control-plane.md (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/control-plane.md):

| Method | Path | Summary |
| --- | --- | --- |
| GET | `/__unit/requests` | "Every request the unit handled, newest first." |
| GET | `/__unit/requests/unmatched/near-misses` | "Unmatched requests, each with the routes it nearly asked for." |
| POST | `/__unit/state/reset` | "Wipe state and re-apply the seed scenario." |
| POST | `/__unit/state/update` | "Commit one mutation under optimistic concurrency." |
| POST | `/__unit/chaos/reset` | "Drop all rules and counters." |
| GET | `/__unit/errors` | "Every core error kind as this vendor shapes it." |
| GET | `/__unit/manifest` | "Credentials, webhook keys and entity ids: what an end-to-end script needs to address this unit." |
| POST | `/__unit/webhooks/subscriptions` | "Register a subscriber without using the vendor API." |

## 8. Sources

- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/unit.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/seed.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/control-plane.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/examples/pytest-consumer/conftest.py
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/examples/pytest-consumer/test_clover.py

The docs do not say which driver calls are safe while a delivery is in flight.
Fetched 2026-10-29.
