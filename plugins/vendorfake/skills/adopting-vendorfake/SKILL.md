---
name: adopting-vendorfake
description: Wires one vendor's vendorfake into a consumer's end-to-end stack — a Docker image installed from the vendorfake release archive at a pinned ARG, a compose service with a health check on /__unit/health and its port on loopback, the no-faults profile for CI, the redirect pin, the seeded app id, secret and webhook key in the consumer's settings, an idempotent seed at init, the first-journey test and the two red runs that prove it can fail, and GET /__unit/requests as the oracle. Use when adding a fake Clover, Square, Toast or Lightspeed to docker compose or an e2e tier, pointing a service at vendorfake, choosing a vendorfake profile or image pin, or when the first e2e test against the fake will not pass.
---

# Adopting vendorfake

A unit is one process faking one vendor: a seeded scenario on the vendor's own
routes, plus a control plane at `/__unit/*` for state, chaos and the request log.
The steps below are the order that worked on a first adoption; do them in order.
Argument-level facts come from `vendorfake explain --json profile no-faults --vendor clover`
when vendorfake is installed (`route` and `profile` need `--vendor` or
`VENDORFAKE_VENDOR`; `fault`, `error` and `header` do not), else the pages in Sources.

## 0. Fit check first

Inventory the consumer's outbound vendor calls from its production code, not its
docs, one `METHOD /path` per line. Run the `extending-vendorfake` skill's
`fit_check.py` against `vendorfake routes --vendor <v> --json`. Known non-fits:
vendorfake's Lightspeed is Retail X-Series, not the Restaurant K-Series API;
Square's SDK needs a consumer-side `base_url` seam; a route missing at one tag
may be in the next (`GET /v2/channels` arrived in 0.8.0). Any `missing` or
`method` row: stop and go to `extending-vendorfake`. Exit 2 with no rows means the
inventory matched nothing: redo it, never read it as a fit.

## 1. The image

Nothing is published to a registry, and the workplace machine needs no checkout,
git access or token: install from the release's source archive behind one ARG,
`ARG VENDORFAKE_REF=v0.8.0` → `pip install "vendorfake[serve] @ https://github.com/konyklabs/vendorfake/archive/${VENDORFAKE_REF}.tar.gz"`.
The ARG is the one place to bump. A tag normally; `refs/heads/<branch>` or a sha
to consume a branch before its tag, and then `__version__` reports the previous
release and `docker compose build --no-cache` is needed, because the layer
caches on the ARG string. The Dockerfile is in `references/compose-stack.md`.

## 2. The service

```yaml
  clover:
    build:
      context: ./vendorfake            # the Dockerfile from references/compose-stack.md
      args:
        VENDORFAKE_REF: ${VENDORFAKE_REF:-v0.8.0}
    environment:
      VENDORFAKE_VENDOR: clover
      VENDORFAKE_PROFILE: no-faults    # happy paths only; `full` carries chaos
      VENDORFAKE_VENDOR_REDIRECT_URI: http://app:8000/oauth/clover/callback   # your callback; never on Toast
    ports:
      - "127.0.0.1:${VENDORFAKE_HOST_PORT:-8080}:8080"   # loopback only
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request as u; exit(0 if u.urlopen('http://127.0.0.1:8080/__unit/health', timeout=2).status == 200 else 1)"]
      interval: 5s
```

- One service per vendor when vendor-config keys differ: `VENDORFAKE_VENDOR_<SUFFIX>`
  reaches every vendor in the process, and each vendor config is `extra="forbid"`.
- Loopback only: the control plane is unauthenticated and will POST webhooks anywhere.
- `no-faults` for CI. `full` carries chaos; `chaos-demo` preloads faults that read
  as random red runs. Lightspeed's documented rate limiter still counts on `no-faults`.
- The app gets `depends_on: {clover: {condition: service_healthy}}`.

## 3. The consumer's settings

- OAuth base URL and API base URL: both `http://clover:8080` (one origin on the fake)
- App id `UNITCLOVERAPP`; app secret `unit-clover-app-secret`
- Merchant `HRVSTRYE12345`; webhook auth code `unit-seeded-clover-webhook-auth-code`
- Redirect: the consumer's own callback, pinned on the fake with
  `VENDORFAKE_VENDOR_REDIRECT_URI`; authorize answers 400 to any other.
- What the fake cannot supply (a post-callback redirect base, say): set a placeholder host.
- Square and Lightspeed: the same shape (redirect pin, authorize → code → callback;
  Lightspeed's authorize is the `GET /connect` stand-in); read the seeded values from
  `GET /__unit/manifest` or seed.md.
- Toast has no redirect flow: drop `VENDORFAKE_VENDOR_REDIRECT_URI`, which Toast's
  config refuses at startup (`extra_forbidden`, so the health check never passes).
  Its journey is the machine-client `POST /authentication/v1/authentication/login`,
  then the bearer on one read; steps 1-2 of section 5 do not apply.

## 4. Seed the consumer, idempotently

The consumer's own test-data command runs at init and must be safe to re-run, so
the company the journey connects (one with no merchant yet) exists on every
`compose up`, not only the first.

## 5. The first journey

1. Authorize with redirects off; read `code` from the `Location` query (302).
2. Call the consumer's callback with `code`, `merchant_id`, `state`; assert the
   success redirect, not the failure one.
3. Read the stored POS connection row; assert its merchant id.
4. Present the stored token to `GET /v3/merchants/{id}`; expect 200.
5. Read the request log (section 7) and assert what the service actually called.

```
authorize = GET {fake}/oauth/v2/authorize?client_id=UNITCLOVERAPP&redirect_uri={callback}&state=s1   (redirects off)
code      = query(authorize.headers["Location"])["code"]           # 302; merchant_id is in the query too
callback  = GET {app}{callback}?code={code}&merchant_id=HRVSTRYE12345&state=s1   (redirects off)
assert callback.headers["Location"] ends with the success path, not the failure path
row       = the consumer's stored POS connection for that company
assert row.merchant_id == "HRVSTRYE12345"
assert GET {fake}/v3/merchants/HRVSTRYE12345 with "Authorization: Bearer " + row.access_token  -> 200
assert [r.operation_id for r in GET {fake}/__unit/requests] starts with ["GetMerchant", "ExchangeToken", "Authorize"]   # newest first
red run 1: fake down (compose stop clover)  -> connection error at line 1
red run 2: code="never-issued"              -> line 4 lands on the failure path
```

## 6. Prove it can fail twice

Run each red case once and watch it fail for the stated reason: the fake
unreachable (a connection error at the first call), and a code the vendor never
issued (the token exchange is refused and the callback lands on the failure path).
A journey that cannot go red proves nothing about the integration.

## 7. The oracle

`GET /__unit/requests` (newest first; filters `operation_id`, `route`, `unmatched`,
`limit`; no bodies) shows authorize, token exchange and merchant read in order.
A wrong path: `GET /__unit/requests/unmatched/near-misses` and the
`Vendorfake-Near-Miss` header. `GET /__unit/journal` shows committed mutations only.
`POST /__unit/state/reset` between tests drops subscribers and keeps chaos rules.

## Traps

- A `redirect_uri` other than the pinned one: authorize answers 400.
- The `/__clover/` registration stand-in takes https callbacks only: set `VENDORFAKE_VENDOR_ALLOW_INSECURE_CALLBACKS=true`, or register through `POST /__unit/webhooks/subscriptions`, which accepts `http://` (read from source).
- A Clover-only `VENDORFAKE_VENDOR_*` key kills the Square mount in a `clover,square` process.
- Clover's token reply carries its expirations in epoch seconds; the stored entity fields are epoch ms.
- Ids are deterministic per unit, not unique across units: read them from responses.
- A seed overlay cannot touch `tokens` or the identity collection and refuses an unknown collection (C36); another tenant or credential means a whole document via `VENDORFAKE_SEED`.
- One networked unit per concurrent run: state, log and chaos rules are process-global.
- The bindings.md compose example builds from the repository URL and tracks `main`: pin the ARG.

## Sources

vendorfake docs at tag v0.8.0 (0145227): start/install.md, start/bindings.md,
concepts/unit.md, concepts/seed.md, reference/env.md, reference/control-plane.md,
reference/profiles.md, and the Clover surface source (clover/surface/oauth.py,
clover/surface/webhooks.py, clover/config.py); fetched 2026-10-29. The docs are silent on the
release-archive install form (install.md documents the git+ tag pin; the archive form was
verified by building the image) and on whether the control-plane subscription route enforces
https (it does not: read from core/control/plane.py and core/webhooks/models.py). The journey
shape, the two red runs and the settings list are this repository's own distillation of a first
adoption, not a vendorfake page. Details and quotes in `references/`.
