# The compose stack

Fetched 2026-10-29 (first 2026-10-29) from the vendorfake repository at tag v0.8.0 (0145227): docs/start/install.md, docs/start/bindings.md, docs/reference/env.md, docs/reference/profiles.md, docs/concepts/seed.md, Dockerfile, src/vendorfake/clover/config.py, src/vendorfake/clover/surface/oauth.py, src/vendorfake/clover/surface/webhooks.py, src/vendorfake/core/config/profile.py.

## Contents

1. The image
2. The service
3. The health route
4. The consumer's settings
5. Traps
6. Sources

## 1. The image

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.13-slim-bookworm
# The one place to bump. A tag normally; refs/heads/<branch> or a sha to
# consume a branch before its tag (then __version__ reports the previous
# release, and `docker compose build --no-cache` is needed: the layer caches
# on this string).
ARG VENDORFAKE_REF=v0.8.0
RUN groupadd --system --gid 10001 vendorfake \
 && useradd --system --uid 10001 --gid vendorfake --home-dir /home/vendorfake --create-home vendorfake
# No checkout, registry or token: the release's source archive on GitHub.
# [serve] carries fastapi and uvicorn, which only `vendorfake serve` needs.
RUN pip install --no-cache-dir "vendorfake[serve] @ https://github.com/konyklabs/vendorfake/archive/${VENDORFAKE_REF}.tar.gz"
ENV PYTHONUNBUFFERED=1 VENDORFAKE_HOST=0.0.0.0 VENDORFAKE_PORT=8080
USER vendorfake
WORKDIR /home/vendorfake
EXPOSE 8080
# 200 only once the unit has hydrated its seed; never token-guarded; follows
# VENDORFAKE_CONTROL_PORT when the control plane has its own listener.
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=5 \
  CMD ["python", "-c", "import os, sys, urllib.request; port = os.environ.get('VENDORFAKE_CONTROL_PORT') or os.environ.get('VENDORFAKE_PORT', '8080'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/__unit/health', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["vendorfake"]
CMD ["serve"]
```

The user, ports, environment and health check are vendorfake's own Dockerfile's
runtime stage; the build stage (a wheel built from a checkout) is replaced by the
one `pip install` from the archive.

On the extra, install.md: "The `serve` extra pulls in the ASGI stack (`fastapi`,
`uvicorn`) that `vendorfake serve` and the served/container bindings need; the
in-process bindings (`unit()`, `async_unit()`) never import it" —
https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/install.md

On pinning, install.md ("Pinning a commit instead of a tag"): "A tag is the only
pin whose version string means what it says. Release-please bumps `pyproject.toml`
at release time, so a commit on `main` between two tags still reports the
*previous* release from `importlib.metadata.version("vendorfake")` and
`vendorfake.__version__`" —
https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/install.md#pinning-a-commit-instead-of-a-tag

Archive URL forms, verified by install, not documented (install.md documents
`git+https://github.com/konyklabs/vendorfake@v0.8.0` only), 2026-10-29:

| `VENDORFAKE_REF` | URL | Result |
| --- | --- | --- |
| `v0.8.0` | `.../archive/v0.8.0.tar.gz` | installs; `vendorfake --version` prints `0.8.0` |
| `0145227` | `.../archive/0145227.tar.gz` | answers 200 (the same tree as the tag) |
| `refs/heads/main` | `.../archive/refs/heads/main.tar.gz` | answers 200; `uv pip install --dry-run` resolves it with uvicorn 0.54.0 / starlette 1.10.0 |
| an unknown tag, e.g. `v9.9.9` | `.../archive/v9.9.9.tar.gz` | answers 404, so the build fails |

## 2. The service

```yaml
services:
  clover:
    build:
      context: ./vendorfake            # the Dockerfile above
      args:
        VENDORFAKE_REF: ${VENDORFAKE_REF:-v0.8.0}
    environment:
      VENDORFAKE_VENDOR: clover
      VENDORFAKE_PROFILE: no-faults    # happy paths only; `full` carries chaos
      VENDORFAKE_VENDOR_REDIRECT_URI: http://app:8000/oauth/clover/callback   # your callback; authorize 400s any other
    ports:
      - "127.0.0.1:${VENDORFAKE_HOST_PORT:-8080}:8080"   # loopback: the control plane is unauthenticated
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request as u; exit(0 if u.urlopen('http://127.0.0.1:8080/__unit/health', timeout=2).status == 200 else 1)"]
      interval: 5s
      timeout: 3s
      retries: 5

  app:
    build: .
    environment:
      CLOVER_OAUTH_BASE_URL: http://clover:8080
      CLOVER_API_BASE_URL: http://clover:8080
      CLOVER_APP_ID: UNITCLOVERAPP
      CLOVER_APP_SECRET: unit-clover-app-secret
      CLOVER_WEBHOOK_AUTH_CODE: unit-seeded-clover-webhook-auth-code
    depends_on:
      clover:
        condition: service_healthy
```

Built this way the service came up healthy and answered
`{"status":"ok","vendor":"clover","profile":"no-faults",...,"version":"0.8.0"}`
(verified 2026-10-29). The `app` variable names are the consumer's own; only the
values come from the fake.

The variables, as env.md states them
(https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/env.md):

- `VENDORFAKE_VENDOR` — install.md: it "also takes a comma-separated list —
  `-e VENDORFAKE_VENDOR=clover,square` serves both from one container, each under
  its own prefix" —
  https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/install.md#as-a-container
- `VENDORFAKE_PROFILE`: "Profile name or path to load when none is passed."
  `VENDORFAKE_PROFILE_<suffix>`: "Prefix: the remainder names a vendor; that
  vendor's profile, beating VENDORFAKE_PROFILE."
- `VENDORFAKE_VENDOR_<suffix>`: "Prefix: the remainder becomes a snake_case
  vendor-config key." The loader copies every such key, lower-cased, into the
  process's vendor config (src/vendorfake/core/config/profile.py:337-340,
  https://github.com/konyklabs/vendorfake/blob/v0.8.0/src/vendorfake/core/config/profile.py#L337-L340),
  so with several vendors mounted each vendor sees every key.
- `VENDORFAKE_CONTROL_TOKEN`: "Every /__unit/* request but GET or HEAD
  /__unit/health must carry it in vendorfake-control-token." With
  `VENDORFAKE_CONTROL_PORT`, for a fake that must be networked on purpose —
  bindings.md, "A control plane on its own port":
  https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md#a-control-plane-on-its-own-port
- `VENDORFAKE_SEED`: "Seed document path, overriding the profile's."
  `VENDORFAKE_SEED_OVERLAY`: "Partial seed document merged over the seed: a JSON
  file path, or the JSON itself inline."

Profiles, from profiles.md
(https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/profiles.md):
Clover `no-faults` is "Full surface with fault injection switched off entirely.
What a CI suite that only wants happy paths should run." Clover `chaos-demo` is
"Full surface with a preloaded fault set: rate limiting on every third order
create, a mid-flow token expiry on the fourth order read, and a duplicated then a
reordered delivery. Runs on a virtual clock." Lightspeed's `no-faults` adds "The
documented rate limiter still counts."

## 3. The health route

vendorfake's Dockerfile: "/__unit/health is served by every vendor on every
profile, only once the unit has constructed and hydrated its seed, so a 200 means
the unit is answering -- not merely that a socket is open. It is the one route
VENDORFAKE_CONTROL_TOKEN never guards, and with VENDORFAKE_CONTROL_PORT set the
control plane has a listener of its own, so the probe follows it there." —
https://github.com/konyklabs/vendorfake/blob/v0.8.0/Dockerfile

With several vendors in one process, bindings.md: "the root `/__unit/health`
answers for the whole process, listing every vendor it mounted, and is 200 only
once all of them have hydrated their seeds, so one `service_healthy` still gates
the app on all of them." —
https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md#docker-compose

## 4. The consumer's settings

The seeded Clover scenario, seed.md
(https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/seed.md#clover):

| | |
|---|---|
| App credentials | `UNITCLOVERAPP` / `unit-clover-app-secret` |
| Full-access bearer | `unit-seeded-clover-access-token-full-permissions` (refresh `unit-seeded-clover-refresh-token-full-permissions`) |
| Read-only bearer | `unit-seeded-clover-access-token-read-only` |
| Tenant | merchant `HRVSTRYE12345` in every `/v3` **path** |
| Webhooks | "pre-verified subscriber `wbhk_seed_quickstart` (auth code `unit-seeded-clover-webhook-auth-code`), **disabled**; register through `POST /__unit/webhooks/subscriptions`" |
| Event types | "`O:`, `I:`, `C:`, `P:`, `IM:` ... × `CREATE`, `UPDATE`, `DELETE` — e.g. `O:CREATE`; globs like `O:*` accepted, and `I:*` does not match `IM:`" |

The redirect default, src/vendorfake/clover/config.py:52:
`redirect_uri: str = "https://example.test/oauth/callback"` —
https://github.com/konyklabs/vendorfake/blob/v0.8.0/src/vendorfake/clover/config.py#L52

The redirect check, src/vendorfake/clover/surface/oauth.py:103-111: a supplied
`redirect_uri` that differs from the configured one raises `INVALID_VALUE` with
"redirect_uri does not match the redirect URI registered for this app." under
`# JUDGMENT: blocks redirecting a code to an unregistered URI.` Measured
2026-10-29: 400. —
https://github.com/konyklabs/vendorfake/blob/v0.8.0/src/vendorfake/clover/surface/oauth.py#L103-L111

The https rule on the stand-in, src/vendorfake/clover/surface/webhooks.py:156-168,
`_require_https`: "DOCUMENTED -- Clover supports only HTTPS-enabled callbacks.",
waived only by the vendor config's `allow_insecure_callbacks` (set it with
`VENDORFAKE_VENDOR_ALLOW_INSECURE_CALLBACKS=true`) —
https://github.com/konyklabs/vendorfake/blob/v0.8.0/src/vendorfake/clover/surface/webhooks.py#L156-L168

The control-plane bypass, read from source and not documented:
`POST /__unit/webhooks/subscriptions` (src/vendorfake/core/control/plane.py:475)
validates the URL with `check_notification_url`
(src/vendorfake/core/webhooks/models.py:241-262), which takes `http` or `https`
and refuses only a link-local host; it does not require https.

## 5. Traps

- Redirect mismatch: 400 (oauth.py:103-111 above).
- https-only stand-in; the env waiver or the control-plane route (webhooks.py,
  plane.py and models.py above).
- A vendor-config key reaches every mounted vendor and an unknown key is a startup
  failure: clover/config.py's docstring, "`extra="forbid"`: an unknown key is a
  startup failure." Measured 2026-10-29:
  `VENDORFAKE_VENDOR_ALLOW_INSECURE_CALLBACKS=true vendorfake info --vendor square`
  exits 1 with `Extra inputs are not permitted`.
- Expirations: the token reply's `access_token_expiration` and
  `refresh_token_expiration` are `wire_seconds(...)` of the stored
  `*_expiration_ms` (oauth.py:312-323) —
  https://github.com/konyklabs/vendorfake/blob/v0.8.0/src/vendorfake/clover/surface/oauth.py#L312-L323
- Ids: seed.md, "Ids are deterministic, not unique" —
  https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/seed.md#ids-are-deterministic-not-unique
- Overlays: seed.md, "The credentials and the identity cannot be overlaid" and
  "A collection you invent is refused when the unit starts" (C36) —
  https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/seed.md#seed-overlays
- Concurrency: bindings.md, "**A networked unit is still one run's unit.** State,
  the journal, the request log and chaos rules are global to the process." —
  https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md#a-control-plane-on-its-own-port
- The bindings.md compose example's `build: https://github.com/konyklabs/vendorfake.git`
  builds whatever `main` is at build time —
  https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md#docker-compose

## 6. Sources

- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/install.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/bindings.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/env.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/reference/profiles.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/concepts/seed.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/Dockerfile
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/src/vendorfake/clover/config.py
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/src/vendorfake/clover/surface/oauth.py
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/src/vendorfake/clover/surface/webhooks.py
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/src/vendorfake/core/config/profile.py
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/src/vendorfake/core/control/plane.py
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/src/vendorfake/core/webhooks/models.py

The docs are silent on the release-archive install form and on the control-plane
route's scheme rule; both are marked where they are stated. Fetched 2026-10-29.
