# APIRequestContext fixtures and helpers

Fetched 2026-09-02 from https://playwright.dev/python/docs/api-testing and
https://playwright.dev/python/docs/test-runners.

## Contents

1. Official fixture shape
2. Local server fixture
3. Roles
4. Shape assertion helper
5. Combining with browser tests

## 1. Official fixture shape

From the docs (GitHub API example), the recommended pattern:

```python
@pytest.fixture(scope="session")
def api_request_context(playwright: Playwright) -> Generator[APIRequestContext, None, None]:
    headers = {"Accept": "application/vnd.github.v3+json",
               "Authorization": f"token {GITHUB_API_TOKEN}"}
    request_context = playwright.request.new_context(
        base_url="https://api.github.com", extra_http_headers=headers)
    yield request_context
    request_context.dispose()
```

`APIResponse`: `.ok`, `.status`, `.headers`, `.text()`, `.json()`, `.body()`.
Always `dispose()` the context.

## 2. Local server fixture

```python
import subprocess, time, urllib.request

@pytest.fixture(scope="session")
def base_url():
    proc = subprocess.Popen(["uvicorn", "app.main:app", "--port", "8123"])
    url = "http://127.0.0.1:8123"
    for _ in range(50):  # 5 s; a cold app server takes ~1 s
        try:
            urllib.request.urlopen(url + "/health", timeout=0.2)
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill(); raise RuntimeError("server did not start")
    yield url
    proc.terminate(); proc.wait(timeout=5)
```

Overrides pytest-base-url's `base_url` fixture for the session. Under xdist
each worker starts its own server; use `worker_id` to pick a port.

## 3. Roles

```python
def _ctx(playwright, base_url, token):
    return playwright.request.new_context(base_url=base_url,
        extra_http_headers={"Authorization": f"Bearer {token}"})

@pytest.fixture(scope="session")
def api(playwright, base_url):
    ctx = _ctx(playwright, base_url, os.environ["API_TOKEN"]); yield ctx; ctx.dispose()

@pytest.fixture(scope="session")
def api_admin(playwright, base_url):
    ctx = _ctx(playwright, base_url, os.environ["ADMIN_TOKEN"]); yield ctx; ctx.dispose()
```

## 4. Shape assertion helper

```python
def assert_shape(obj: dict, **fields):
    """assert_shape(body, id=str, qty=int, status=("created", "paid"))"""
    for key, want in fields.items():
        assert key in obj, f"missing {key!r} in {sorted(obj)}"
        if isinstance(want, tuple):
            assert obj[key] in want, f"{key}={obj[key]!r} not in {want}"
        else:
            assert isinstance(obj[key], want), f"{key}={obj[key]!r} is not {want.__name__}"
```

## 5. Combining with browser tests

`context.request` shares cookies with the browser context (docs: "you can
reuse the browser context's APIRequestContext"). Setup through the API,
assert in the UI, or act in the UI and assert through the API:

```python
def test_pay_marks_order_paid(page, context, orders):
    order = orders.create("A1")
    page.goto(f"/orders/{order['id']}")
    page.get_by_role("button", name="Pay").click()
    expect(page.get_by_text("Paid")).to_be_visible()
    assert context.request.get(f"/orders/{order['id']}").json()["status"] == "paid"
```
