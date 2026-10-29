# Chaos and webhooks

Fetched 2026-10-29 (first 2026-10-29) from the vendorfake repository at tag v0.8.0 (0145227): docs/reference/profiles.md, docs/reference/faults.md, docs/reference/env.md, docs/concepts/chaos.md, docs/concepts/unit.md, docs/concepts/seed.md, docs/concepts/fidelity.md, docs/start/bindings.md, examples/pytest-consumer/test_clover.py, src/vendorfake/clover/surface/webhooks.py.

## Contents

1. Profiles
2. Rules and the timeout fault
3. Provenance
4. Sharing one unit across tests
5. Webhooks
6. Fidelity
7. Sources

## 1. Profiles

`vendorfake profiles --vendor clover` at 0.8.0 (run 2026-10-29), the same six
names every vendor ships (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/profiles.md):

| Profile | Summary |
| --- | --- |
| `chaos-demo` | "Full surface with a preloaded fault set: rate limiting on every third order create, a mid-flow token expiry on the fourth order read, and a duplicated then a reordered delivery. Runs on a virtual clock." |
| `full` | "Every capability on. The default profile." |
| `no-chaos` | "Full surface with delivery faults switched off: requests can still be made to fail deliberately, but a webhook that is sent is sent honestly, once." |
| `no-faults` | "Full surface with fault injection switched off entirely. What a CI suite that only wants happy paths should run." |
| `oauth-only` | "Only the OAuth dance. For a consumer testing token handling and nothing else." |
| `orders-only` | "Orders, payments and the reference data they point at. No OAuth dance, no webhooks: authenticate with a seeded token." |

Lightspeed's `no-faults` and `no-chaos` add "The documented rate limiter still
counts" (profiles.md).

## 2. Rules and the timeout fault

The grammar is chaos.md's "The grammar" (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/chaos.md#the-grammar):
"A rule has an `id`, a `scope` (`request` or `webhook`), a `fault` name, and
two optional clauses": `match` and `when`. "Clover routes are matched with the
tenant placeholder in the template, e.g. `"route": "POST /v3/merchants/{mId}/orders"`."
Every fault, its phase and its parameters: https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/faults.md, or
`vendorfake explain fault <name>`.

The timeout matrix, unit.md
(https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/unit.md#the-clock-and-the-timeout-fault):

- "**Real clock, in-process**: if `delay_ms` exceeds the calling client's read
  timeout, the client raises `httpx.ReadTimeout` **without actually
  waiting** — a millisecond test proves a five-second retry path."
- "**Virtual clock, in-process**: the delay advances scenario time on the
  calling thread and the response comes back immediately"
- "**Virtual clock, served**: answers the 504 immediately."

bindings.md: "`timeout=None` is a setting and not a missing one: a caller who
said they would wait for anything waits, and gets the 504." —
https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md#rehearsing-a-client-timeout-in-a-millisecond

## 3. Provenance

chaos.md (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/chaos.md#provenance): "Every shaped error carries a
`status_provenance` field — `"documented"` or `"judgment"` — alongside `kind` and
any extra `info`. By default it rides as the `Vendorfake-Status-Provenance`
response header". "A `documented` status is the vendor's own answer for this
failure. A `judgment` status is this project's choice where the vendor never
said." For a fault, `provenance: vendor` "reproduces a failure mode the vendor
itself documents" and `provenance: transport` "reproduces something no vendor
documents because it isn't a vendor behaviour".

"Every faulted response carries `Vendorfake-Fault` and `Vendorfake-Rule`
headers, so a test can tell a faulted answer from a real one without parsing
the body." — https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/chaos.md#the-fault-catalogue

## 4. Sharing one unit across tests

chaos.md (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/chaos.md#sharing-one-unit-across-tests): "**A vendor
with single-use or rotating state needs an explicit `reset()` between tests.**"

```python
@pytest.fixture(scope="session")
def clover():
    with served("clover", "oauth-only") as child:
        yield child


@pytest.fixture(autouse=True)
def fresh(clover):
    clover.reset()  # the seed scenario again: single-use token back, request log empty
    clover.reset_chaos()  # no rule from a previous test, whatever order ran
    yield
    clover.reset_chaos()  # a rule this test leaked cannot blame the next one
```

"`reset()` also drops every webhook subscriber a test registered — subscribe
*after* the reset, not in a session fixture above it." "**A virtual clock is not
rewound.**" And: "The pytest plugin's `vendorfake_unit` is function-scoped for
exactly this reason: a fresh in-process unit costs milliseconds, so it builds one
per test and none of the above applies."

## 5. Webhooks

A Clover modifier webhook end to end, run against 0.8.0 on 2026-10-29 (passed):

```python
with webhook_receiver() as rx:
    clover.subscribe(rx.url, ["IM:*"], signature_key="k1")
    path = seed.path(f"/modifier_groups/{seed.modifier_group_milk_id}/modifiers/{seed.modifier_oat_id}")
    assert clover.client.post(path, headers=seed.auth, json={"available": False}).status_code == 200
    clover.drain()
    (delivery,) = rx.received
    assert verify_clover_auth(delivery.headers, "k1")
```

Its body was `{"appId":"UNITCLOVERAPP","merchants":{"HRVSTRYE12345":[{"objectId":"IM:MODIFIEROAT01","type":"UPDATE","ts":...}]}}`;
the same change with `["I:*"]` delivered nothing.

- Receiver: bindings.md, "Webhooks leave over real HTTP on every binding, so a
  `webhook_receiver()` on loopback sees signed bytes; `drain()` is synchronous on
  both entry points" — https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md#rehearsing-a-client-timeout-in-a-millisecond
- Event types: seed.md's Clover row, "globs like `O:*` accepted, and `I:*` does
  not match `IM:`"; `subscribe()` "refuses a name that will never fire rather
  than registering it silently." — https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/seed.md#what-every-seed-has-in-common
- Seeded subscribers: Clover's `wbhk_seed_quickstart` and Toast's
  `sub_seed_quickstart` ship **disabled** (seed.md, the shipped scenario).
- Retry timing: `drain()` sleeps the vendor's retry schedule scaled by the
  profile's `time_scale`; `VENDORFAKE_WEBHOOK_TIME_SCALE` is "Multiplier on every
  retry delay." and `POST /__unit/webhooks/retry-policy` adjusts it at runtime
  (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/env.md, https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/control-plane.md).
- Verify helpers, signatures from the installed 0.8.0:
  `vendorfake.clover.signer.verify_clover_auth(headers, expected)`,
  `vendorfake.square.signer.verify_square_signature(signature_key, notification_url, raw_body, signature)`,
  `vendorfake.toast.signer.verify_toast_signature(secret, raw_body, signature)`,
  `vendorfake.lightspeed.signer.verify_lightspeed_signature(secret, raw_body, header_value)`.
- Clover's https rule: the `/__clover/webhooks/subscriptions` stand-in refuses
  an `http://` callback ("DOCUMENTED -- Clover supports only HTTPS-enabled
  callbacks.", src/vendorfake/clover/surface/webhooks.py:156-168,
  https://github.com/konyklabs/vendorfake/blob/v0.8.0/src/vendorfake/clover/surface/webhooks.py#L156-L168). Two ways around it:
  `VENDORFAKE_VENDOR_ALLOW_INSECURE_CALLBACKS=true`, or `driver.subscribe()` /
  `POST /__unit/webhooks/subscriptions`, which take an `http://` loopback
  receiver (read from source, not documented). vendorfake's own example uses the
  second: "Clover has no subscriptions API -- callbacks are configured in the
  developer dashboard -- so the fake's control plane stands in for it." —
  https://github.com/konyklabs/vendorfake/blob/v0.8.0/examples/pytest-consumer/test_clover.py#L110-L112

## 6. Fidelity

fidelity.md (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/fidelity.md): a consumer opts in to response
validation "with `served(validate=True)`, `vendorfake serve --validate` or the
`ValidatingClient`". "Clover has no fidelity leg. It publishes no
machine-readable specification". "A vendor with no fidelity leg has no schema to
check against, so both refuse rather than serving a unit whose flag reads as
satisfied."

## 7. Sources

- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/profiles.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/faults.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/env.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/control-plane.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/chaos.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/unit.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/seed.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/fidelity.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/examples/pytest-consumer/test_clover.py
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/src/vendorfake/clover/surface/webhooks.py

The docs are silent on the control-plane subscription route's scheme rule and on
which driver calls are safe mid-delivery. Fetched 2026-10-29.
