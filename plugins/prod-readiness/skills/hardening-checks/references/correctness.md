# Correctness traps that pass their tests

Checks 10 to 16. Each of these shipped with green tests. The write-ups for
10 and 14 are from the hardening pass that found them, generalized.

## Contents

10. async-terminal-states (silent async failure)
11. vendor-mode-probes
12. token generations
13. idempotency-keys
14. client-supplied-money (server-authoritative money)
15. entity rate limits
16. error bodies

## 10. Silent async failure

**Observed.** `POST /v1/orders` returned **201 in about 460 ms** with the
full resource: `{"id": "...", "external_id": "<caller ref>", "status":
"received", ...}`. Nothing in that response differed between the healthy
and the doomed case: same status code, same shape, same latency band.

The failure arrived only through the status endpoint: `GET
/v1/orders/{id}/status` returned `200 {"id": "...", "status": "rejected"}`.
No 4xx anywhere, no `problem+json`, no error object, no field pointer. The
resource transitioned.

Timing: the first poll at +5 s already showed `rejected`; a controlled run
with a resolvable payment reference stayed `received` through +30 s. The
observed window is under 5 s; treat it as unbounded and poll to terminal.

Three unrelated causes produced this one identical shape: a payment
reference the processor could not resolve because it was minted by the
wrong API generation of the same provider, or under the wrong key mode, or
fabricated. All indistinguishable from the create response.

**The test that catches it.**

```python
def test_order_reaches_a_non_failed_state(api, orders):
    created = orders.create(sku="A1")
    assert created["status"] == "received"
    deadline = time.monotonic() + SETTLE_SECONDS   # derived from the sandbox's real settling, see below
    polls = 0
    while time.monotonic() < deadline:
        polls += 1
        status = api.get(f"/v1/orders/{created['id']}/status").json()["status"]
        if status in TERMINAL:
            break
        time.sleep(1)
    assert status not in {"rejected", "cancelled", "failed"}, f"status={status} after {polls} polls"
```

Asserting only the create passes on all three triggers above.

**Caveat that ships with the check.** Derive the deadline from the
sandbox's real settling behaviour. One sandbox never advanced past
`received`, so a test waiting for a *success* state hung for the full
timeout on a healthy resource: 90 s and 182 polls before anyone noticed.
Assert the absence of a failed state, not the presence of a succeeded one.

**Scanner.** `async-terminal-states` lists create-then-poll candidates and
whether any test mentions a terminal state. The candidate list is the
auditor's; the test above is the implementer's slice.

## 11. Vendor credential modes and permissions

Test and live key pairs, and account-level API permissions that differ
between two accounts at the same provider. Two failure shapes to keep
apart: a refusal at call time, and an accepted call whose artifact is
unusable downstream (which then shows up as check 10).

**Rule.** Never infer a capability from possessing a key. Probe it: a
startup or smoke-test call that exercises the permission and fails loudly.
The scanner flags files where both test-mode and live-mode markers appear.

## 12. Reference and token type mismatches

Two API generations of one provider emit different id prefixes. An
upstream may accept only the older one and reject the newer silently
(check 10 again). Provider documentation can be wrong about which.

**Rule.** One fixture per generation in the test suite, and the
terminal-state test from check 10 run against each.

## 13. Idempotency discipline

One key per attempt, minted before the operation, reused for a retry of
that same attempt, a **new** key after a decline. Keep it distinct from the
caller's own business reference id, which stays constant across attempts.

```python
attempt_key = str(uuid.uuid4())            # before the call, once per attempt
for retry in range(3):
    r = upstream.post("/charges", json=body, headers={"Idempotency-Key": attempt_key})
    if r.status_code < 500:
        break                              # a decline is final for this key
```

**Scanner.** `idempotency-keys` reports header sites, whether a key is
generated nearby, and money-moving POSTs with no key at all.

## 14. Server-authoritative money

**Observed.** A quote-then-create flow. The quote returned a computed
breakdown: `"totals": {"subtotal": {...}, "tax": {...}, "total":
{"amount": 3465, "currency": "USD"}, "total_due": {...}}`. Money was always
integer minor units plus a currency; never a decimal string, never a float.
That is its own sub-check: formatting is the caller's job.

The create request re-sent two things from the quote: `"catalog_version_id"`
(pinning the prices quoted against) and `"validated_total": {"amount":
3465, "currency": "USD"}` beside the items. The backend forwarded
`validated_total` from the browser untouched.

**What made it safe.** The upstream re-computed the total from the line
items and the pinned catalog version and rejected a mismatch with a
dedicated 400 code meaning "the total no longer matches, re-quote". The
client's number was a consistency assertion ("this is what I showed the
user"), not an authority. A stale catalog version got its own distinct
code, so the caller knew to re-quote rather than retry blindly.

**What makes it a bug.** Copy this shape into a service that does *not*
re-compute, and the client now sets the price. The request body is
byte-identical in both cases, which is why review misses it: the
vulnerability lives in the absence of something at the other end of the
wire.

**The check.** Wherever a client-supplied total, amount or price is
forwarded toward a charge or an order, require evidence of upstream
recomputation. When it cannot be located, flag the forward itself. Put the
finding as a comment at the forwarding site; a design document does not
travel with the code when someone copies the function.

**Scanner.** `client-supplied-money` lists handlers that read a money field
from the request and forward it without a recomputation word in the same
function. The auditor locates the upstream check or confirms its absence.

## 15. Entity-scoped rate limits that read as flakiness

A per-customer or per-entity cooldown returning a generic 4xx that names
no cause. Retry machinery hides it; the test suite sees intermittent
failures. The fix is documentation: the limit, the window, the entity, and
a test whose failure message names the cause.

## 16. Non-conforming error bodies

An upstream that mostly emits RFC 9457 `problem+json` and occasionally
emits a non-JSON body breaks the error contract consumers branch on. The
consumer contract (Pact or equivalent) must include the non-JSON case, and
the client must branch on `Content-Type` before parsing.
