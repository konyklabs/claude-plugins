---
name: testing-against-vendorfake
description: Writing tests against an installed vendorfake — choosing between unit(), async_unit(), served() and the container, the StartedUnit driver (seed, client, subscribe, drain, deliveries, add_chaos_rule, reset, requests, assert_called), the pytest marker and its three fixtures, webhook receivers and signature verification, profiles and chaos rules, seeds and deterministic ids, the Vendorfake-Status-Provenance and Vendorfake-Near-Miss headers, and the traps (I:* does not match IM:, items?expand=modifierGroups, epoch-seconds token expirations, reset drops subscribers). Use when writing or fixing a pytest test against a vendor fake, when a test 404s or raises UnmatchedRequest, when a webhook never arrives, when a shared fake flakes under random order, or when rehearsing a 429, a timeout or a token expiry.
---

# Testing against vendorfake

Default to the in-process binding. One unit per test is cheap (milliseconds) and
makes `reset()` unnecessary. `vendorfake explain --json route|fault|profile|error|header <name>`
is the argument-level source when vendorfake is installed, the docs pages in Sources
otherwise. This skill is for a consumer repository; a change to the fake itself is
`extending-vendorfake`.

## Pick the binding

- `unit("clover")`: in process, sync `httpx.Client` over a transport into the router; no socket.
- `async_unit("clover")` (or `.async_client` on a `unit()` block): the same unit for async code.
- `served("clover")` / `serve_in_thread(started)`: a real `http://127.0.0.1:PORT`.
- The container: one image, vendor chosen at run time; for a consumer that is not Python.

| | Speed | Isolation | Needs a real URL | Language |
|---|---|---|---|---|
| In-process sync / async | fastest | one test process | no | Python |
| Served | fast | one child process/thread | yes | Python |
| Container | slower (image start) | full process/network | yes | any |

Sync unless the code under test is already async; served only when something needs
a socket; container only when the consumer is not Python.

## The shape of a test

```python
import pytest
from vendorfake.testing import unit

@pytest.fixture
def clover():
    with unit("clover") as started:
        yield started

def test_the_espresso_carries_its_milk_modifiers(clover):
    seed = clover.seed
    listed = clover.client.get(seed.path("/items"), params={"expand": "modifierGroups"}, headers=seed.auth)
    assert listed.status_code == 200, listed.text
    items = {row["id"]: row for row in listed.json()["elements"]}   # ids from the response
    (milk,) = items[seed.item_espresso_id]["modifierGroups"]["elements"]
    assert seed.modifier_oat_id in milk["modifierIds"].split(",")
```

The marker form: `@pytest.mark.vendorfake("clover")` with the function-scoped fixtures
`vendorfake_unit`, `vendorfake_async_unit` and `vendorfake_webhook_receiver`. The marker
takes `unit()`'s arguments (`profile=`, `env=`, `seed=`, `clock_start=`, `unmatched=`,
`capabilities=`). The fixtures are typed `StartedUnit[Seed]`; for `seed.merchant_id`,
narrow with `assert isinstance(seed, CloverSeed)` or use `unit("clover")` in your own fixture.

## Webhooks

- `subscribe(url, event_types, signature_key)` on a receiver on loopback
  (`webhook_receiver()` or the fixture); trigger the change through the fake; `drain()`
  before asserting; then read `receiver.received`.
- Verify with the vendor's helper: `vendorfake.clover.signer.verify_clover_auth(headers, key)`,
  `verify_square_signature`, `verify_toast_signature`, `verify_lightspeed_signature`.
- `subscribe()` refuses an event type the vendor never sends: pick from `seed.event_types`.
- Clover: `I:*` does not match `IM:` (modifier events); subscribe to both when both matter.
- Subscribe AFTER `reset()`. The seeded Clover and Toast subscribers ship disabled.

## Faults and profiles

- `no-faults` for happy paths; `full` is the default and carries chaos; `chaos-demo`
  preloads faults on a virtual clock.
- `add_chaos_rule({"id": "slow", "scope": "request", "fault": "timeout", "match": {"route": "GET /v2/locations"}, "params": {"delay_ms": 5000}})`:
  in process the client's own read timeout decides, so a five-second rule proves the
  retry path in a millisecond; `timeout=None` waits and gets the 504.
- "Is this status the vendor's or the fake's?": the `Vendorfake-Status-Provenance`
  header (`documented` / `judgment`) and `GET /__unit/errors`.
- `served(validate=True)` validates responses against the vendor's OpenAPI document;
  Clover has no fidelity leg, so it refuses there.

## Sharing one unit

Only served and container units need this. Per test: `reset()` and `reset_chaos()`.
`reset()` drops subscribers, keeps chaos rules, and does not rewind a virtual clock.
Single-use state (Clover retires a refresh token on use) is what flakes under
`pytest-randomly`. One networked unit per concurrent run.

## Seeds and ids

`seed.credentials`, `seed.token`, `seed.auth`, `seed.path()`. Seed token lifetimes are
relative to unit start (`*_expires_in_ms`; `0` = already expired). `seed_overlay=` for
entities, never `tokens` or the identity collection; an unknown collection is refused
(C36). `VENDORFAKE_SEED` (a whole document) for another tenant. Ids are deterministic
per unit, not unique across units: read them from responses or `GET /__unit/manifest`.

## When a test 404s

`UnmatchedRequest` is an `AssertionError` by default (`unmatched="vendor-404"` opts
out). Then: `requests(unmatched=True)`, the `Vendorfake-Near-Miss` header,
`vendorfake routes --vendor V`. A multi-vendor mount needs the `/<vendor>` prefix
(its 404 carries a `vendorfake-mounts` header). `served(env=)` refuses
`VENDORFAKE_HOST`, `_PORT`, `_LOG_LEVEL`, `_SEED` and `_SEED_OVERLAY` and says what to use.

## Traps

- Clover's token reply: expirations in epoch seconds; entity fields in epoch ms
  (patch `POST /__unit/state/update` in ms).
- Clover items: `?expand=modifierGroups`, or the groups are not in the answer.
- Lightspeed's documented rate limiter counts on `no-faults` too.
- `vendorfake explain route|profile` needs `--vendor` when several vendors are installed.
- The startup log goes to stderr; `--json` stdout stays clean for a pipe.
- The journal shows committed mutations only; "what did my client call" is the request log.

## Sources

vendorfake docs at tag v0.8.0 (0145227): start/bindings.md, concepts/unit.md,
concepts/seed.md, concepts/chaos.md, concepts/fidelity.md, reference/control-plane.md,
reference/profiles.md, reference/faults.md, examples/pytest-consumer; fetched 2026-10-29.
The docs do not state the unit of the Clover token reply's expirations (read from
clover/surface/oauth.py: seconds on the wire, ms in the entity) and do not say which
Driver calls are safe mid-delivery. Details and quotes in `references/`.
