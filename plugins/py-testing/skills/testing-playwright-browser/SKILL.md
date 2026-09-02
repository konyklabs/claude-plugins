---
name: testing-playwright-browser
description: Browser tests with pytest-playwright — the page and context fixtures, role-based locators, web-first assertions, page objects, logging in once and reusing storage state, tracing on failure, network mocking, and running under xdist. Use when writing or fixing in-browser tests, when a test is flaky or slow, when a selector broke, or when auth is repeated in every test.
---

# Playwright browser tests

Most browser-test cost is not in writing tests; it is in re-running slow,
flaky ones. The choices below (locators that wait, assertions that retry,
one login per run, a trace on every failure) are what keep the suite fast
enough to run on every PR.

## Fixtures

pytest-playwright provides them; do not create your own browser.

| fixture | scope | use |
|---|---|---|
| `page` | function | a fresh page in a fresh context per test |
| `context` | function | the page's context; `context.request` for API calls |
| `new_context` | function | factory for a second identity in one test |
| `browser` | session | one browser per run (per xdist worker) |
| `browser_context_args` | session | override to set `storage_state`, viewport, `base_url` |
| `browser_type_launch_args` | session | override for `slow_mo`, channel |

Options come from the CLI or `addopts`: `--browser chromium`, `--headed`,
`--base-url`, `--tracing retain-on-failure`, `--screenshot only-on-failure`,
`--video retain-on-failure`, `--output test-results`.

## Locators and assertions

Priority from the docs: `get_by_role` > `get_by_text` > `get_by_label` >
`get_by_test_id` > CSS or XPath last. Role locators read like the user sees
the page and survive markup changes; a CSS chain is a bug waiting for the
next refactor.

```python
from playwright.sync_api import expect

page.get_by_role("textbox", name="Email").fill("a@example.com")
page.get_by_role("button", name="Sign in").click()
expect(page.get_by_role("heading", name="Orders")).to_be_visible()
expect(page).to_have_url(re.compile(r"/orders$"))
```

- `expect(...)` retries until its timeout (5 s default); a bare `assert` on
  `locator.text_content()` does not. Web-first assertions are the fix for
  most "works locally, flaky in CI".
- Never `page.wait_for_timeout(n)`. Wait for the thing you need:
  `expect(locator).to_be_visible()`, `page.wait_for_url(...)`.
- `expect.set_options(timeout=10_000)` once in conftest when the app is slow;
  do not sprinkle timeouts per assertion.

## Page objects

One class per page or component; it owns the locators and exposes actions.
Tests contain intent, not selectors.

```python
class LoginPage:
    def __init__(self, page):
        self.page = page
        self.email = page.get_by_role("textbox", name="Email")
        self.password = page.get_by_role("textbox", name="Password")
        self.submit = page.get_by_role("button", name="Sign in")
    def goto(self):
        self.page.goto("/login")
    def login(self, email, password):
        self.email.fill(email); self.password.fill(password); self.submit.click()
```

## Log in once

`storage_state` persists cookies, local storage and IndexedDB (not session
storage). Log in once per run, save the state, load it into every context:

```python
@pytest.fixture(scope="session")
def auth_state(browser, base_url, tmp_path_factory):
    path = tmp_path_factory.mktemp("auth") / "state.json"
    ctx = browser.new_context(base_url=base_url)
    LoginPage(ctx.new_page()).login(os.environ["USER"], os.environ["PASS"])
    ctx.storage_state(path=str(path)); ctx.close()
    return str(path)

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, auth_state):
    return {**browser_context_args, "storage_state": auth_state}
```

Tests that must start logged out use `new_context(storage_state=None)`.

## Network

`page.route("**/api/prices", lambda r: r.fulfill(json={"A1": 9.99}))` mocks a
response; `route.abort()` blocks (analytics, images); `route.fetch()` then
`route.fulfill(response=..., body=...)` rewrites a real response. Mock at the
edge of the system under test, not inside it: a UI test with every API
mocked tests the mocks.

## Failure evidence and speed

- `--tracing retain-on-failure` always. A trace has the DOM, network and
  console per step; a screenshot has a moment.
- `PWDEBUG=1 pytest -s tests/ui -k name` opens the inspector; `playwright
  show-trace test-results/.../trace.zip` replays a CI failure.
- `pytest -n auto` runs one browser per worker. Tests must not share
  server-side state by name; suffix created records with a uuid.
- Keep `tests/ui` to what only a browser can verify. Business rules belong in
  `tests/api` or `tests/unit` where they cost milliseconds.

Fuller fixture code, the CLI table and sources in
`references/browser-fixtures.md`.

## Sources

playwright.dev/python/docs: test-runners, locators, test-assertions, auth,
network, pom, debug; pytest-xdist how-to; fetched 2026-09-02. The `auth_state`
fixture above composes documented pieces (`storage_state`,
`browser_context_args`); the docs show the pieces, not this exact fixture.
