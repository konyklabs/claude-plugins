---
name: testing-playwright-api
description: API tests with Playwright's APIRequestContext in pytest — the session-scoped request fixture, base URL, auth headers, response assertions, a client class per resource, and sharing cookies with a browser context. Use when writing or fixing HTTP API tests in a Playwright-based suite, setting up backend state before a browser test, or verifying server-side effects after one.
---

# Playwright API tests

Playwright's `APIRequestContext` is an HTTP client with the same lifecycle,
tracing and auth model as the browser. The official docs frame it for three
jobs: testing the API on its own, preparing server state before a browser
test, and checking server-side effects after one. A suite that also drives
a browser gets one client, one auth story and one trace for all three.

## The fixture

```python
# tests/api/conftest.py
import os
import pytest
from playwright.sync_api import APIRequestContext, Playwright

@pytest.fixture(scope="session")
def api(playwright: Playwright, base_url: str):
    ctx = playwright.request.new_context(
        base_url=base_url,
        extra_http_headers={"Accept": "application/json",
                            "Authorization": f"Bearer {os.environ['API_TOKEN']}"},
    )
    yield ctx
    ctx.dispose()
```

- Session scope: one context per run, `dispose()` at the end. Per-test
  contexts are for tests that need a different identity.
- `base_url` comes from `pytest-base-url` (`--base-url http://localhost:8000`
  or `base_url` in config); relative paths then work: `api.get("/orders")`.
- Never build auth into each test. A second fixture with a different token
  (`api_as_admin`) is the pattern for roles.

## Assertions

```python
r = api.post("/orders", data={"sku": "A1", "qty": 2})
assert r.ok, f"{r.status} {r.text()}"
body = r.json()
assert body["status"] == "created"
assert r.headers["content-type"].startswith("application/json")
```

`r.ok` is 200–299. Put `r.text()` in the assertion message: the failure
output is the only evidence you get in CI. For shape checks, one helper
that asserts required keys and types beats a schema library in most suites;
reach for a schema validator when the API publishes one.

## A client per resource

The API equivalent of a page object: one class per resource that knows the
paths and the payloads, so tests read as intent and a path change is one
edit.

```python
class Orders:
    def __init__(self, api: APIRequestContext):
        self.api = api
    def create(self, sku: str, qty: int = 1):
        r = self.api.post("/orders", data={"sku": sku, "qty": qty})
        assert r.ok, r.text()
        return r.json()
    def get(self, order_id: str):
        return self.api.get(f"/orders/{order_id}")
```

Expose it as a fixture (`orders = Orders(api)`) from the same conftest.

## Sharing state with the browser

`context.request` is an `APIRequestContext` that shares the browser
context's cookies. Use it to create data through the API and then look at it
in the UI, or to check the backend after a click, without a second login.

```python
def test_order_shows_up(page, context):
    context.request.post("/orders", data={"sku": "A1"})
    page.goto("/orders")
    expect(page.get_by_role("cell", name="A1")).to_be_visible()
```

## Rules

- API tests live in `tests/api/` with their own conftest and command; they
  do not launch a browser. `playwright` (session-scoped) is all they need.
- The server under test is a fixture or an external URL, never started
  inside a test. For a local app, a session-scoped fixture starts it once
  and waits for a health endpoint.
- Test data is created through the API in the test that needs it and
  identified uniquely (a uuid suffix), so tests run in parallel and in any
  order.
- Full fixture and helper code in `references/api-fixtures.md`.

## Sources

playwright.dev/python/docs/api-testing, /test-runners; pytest-base-url on
GitHub; fetched 2026-09-02. The docs do not compare `APIRequestContext` with
`httpx`/`requests`; the case for it here is the shared lifecycle and auth
with browser tests.
