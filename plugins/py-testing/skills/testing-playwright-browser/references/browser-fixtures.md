# pytest-playwright reference

Fetched 2026-10-29 (first 2026-09-02) from playwright.dev/python/docs (test-runners, auth,
locators, test-assertions, network, pom, debug) and pytest-xdist docs.
pytest-playwright 0.10.0 (2026-10-08) requires Python >= 3.10 (the docs intro
page still says 3.8; PyPI wins); asyncio users take `pytest-playwright-asyncio`
(`pytest-asyncio>=0.26.0`, `asyncio_default_test_loop_scope = session`,
`@pytest.mark.asyncio(loop_scope="session")`).

## Contents

1. Install
2. Fixtures and scopes
3. CLI options
4. Config in pyproject
5. Auth state, full fixture
6. Locators and assertions
7. Network
8. Debugging
9. xdist

## 1. Install

```
pip install pytest-playwright
playwright install chromium          # add --with-deps on CI images
```

(`--with-deps` is standard Playwright CLI; the Python intro page does not
mention it, so it is not quoted from the docs.)

## 2. Fixtures and scopes

Function: `page`, `context`, `new_context`. Session: `playwright`,
`browser_type`, `browser`, `browser_name`, `browser_channel`, `is_chromium`,
`is_firefox`, `is_webkit`. Session, override-only config dicts:
`browser_type_launch_args`, `browser_context_args`, `connect_options`.
`base_url` and `--base-url` come with the plugin itself since 0.10.0;
pytest-base-url became an optional extra (`pytest-playwright[base-url]`) that
only `--verify-base-url` needs. Markers: `browser_context_args(...)`
(per-test context options), `skip_browser("firefox")`, `only_browser("chromium")`.
Per-test launch options: parametrize `browser_type_launch_args` with
`indirect=True` (0.10.0; one browser launch per distinct value).

## 3. CLI options

`--headed`, `--browser {chromium,firefox,webkit}` (repeatable),
`--browser-channel`, `--slowmo`, `--device`, `--output` (default
`test-results`), `--tracing {on,off,retain-on-failure}`, `--video
{on,off,retain-on-failure}`, `--screenshot {on,off,only-on-failure}`,
`--full-page-screenshot` (needs `--screenshot`), `--base-url`. Defaults:
headless, chromium, tracing/video/screenshot all `off`. The options configure
only the built-in `browser`/`context`/`page`; a context made by hand with
`browser.new_context()` or `new_context` ignores `--tracing` and friends.

## 4. Config in pyproject

The docs show `pytest.ini` with `addopts = --headed --browser firefox`; the
same flags go in `[tool.pytest.ini_options] addopts` (general pytest
mechanism). Suggested:

```toml
[tool.pytest.ini_options]
addopts = ["-ra", "--strict-markers", "--tracing=retain-on-failure", "--screenshot=only-on-failure"]
base_url = "http://127.0.0.1:8000"
```

## 5. Auth state, full fixture

```python
import os, pytest

@pytest.fixture(scope="session")
def auth_state(browser, base_url, tmp_path_factory):
    path = tmp_path_factory.mktemp("auth") / "state.json"
    ctx = browser.new_context(base_url=base_url)
    page = ctx.new_page()
    page.goto("/login")
    page.get_by_role("textbox", name="Email").fill(os.environ["E2E_USER"])
    page.get_by_role("textbox", name="Password").fill(os.environ["E2E_PASS"])
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url("**/orders")
    ctx.storage_state(path=str(path))
    ctx.close()
    return str(path)

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, auth_state):
    return {**browser_context_args, "storage_state": auth_state}
```

Docs: `context.storage_state(path=...)` then `browser.new_context(storage_state=...)`;
persists cookies, local storage, IndexedDB, passkeys (WebAuthn); not
session storage (the docs give an `add_init_script` workaround). The docs
keep the file under `playwright/.auth`, gitignored, because it holds live
session cookies. Under xdist each worker logs in once: workers are separate
processes (the xdist page does not state this for fixtures; it follows).

## 6. Locators and assertions

Priority: `get_by_role()` > `get_by_text()` > `get_by_label()` >
`get_by_placeholder()` / `get_by_alt_text()` / `get_by_title()` >
`get_by_test_id()` (the docs list it last yet call it the most resilient) >
CSS/XPath (discouraged). Locators are strict: more than one match throws. Locators re-resolve on every action
(auto-wait). `expect(locator)` retries, default 5000 ms: `to_have_text`,
`to_be_visible`, `to_contain_text`, `to_have_attribute`, `to_have_count`;
`expect(page).to_have_url`. `expect.soft(...)` for non-fatal checks (needs
pytest-playwright >= 0.8.0); `expect.set_options(timeout=10_000)` globally in
`conftest.py`, or `timeout=` on one assertion.

## 7. Network

`page.route("**/api/fetch_data", lambda route: route.fulfill(status=200, body=data))`;
`route.continue_(headers=...)`; `route.fetch()` + `route.fulfill(response=response, body=body)`;
`route.abort()`. `continue_` ignores a `cookie` header and cannot override
`Host` or `Sec-Fetch-*`. A `3xx` fulfil does not re-trigger the handler.

## 8. Debugging

`PWDEBUG=1 pytest -s` (inspector, headed, no timeout, `page.pause()`);
`PWDEBUG=console`; `DEBUG=pw:api`; trace viewer for `--tracing` output
(`playwright show-trace <zip>`); `playwright codegen <url>` to draft locators
(standard CLI; not re-quoted from the debug page this fetch).

## 9. xdist

`pytest -n auto` (xdist: physical cores; `-n logical` needs psutil). The
Playwright docs suggest 2 up to the CPU count and warn that too many
processes "may notice unexpected behavior". Session fixtures (including
`browser`) run once per worker. `--dist loadscope|loadfile|loadgroup` keep
related tests on one worker when a module fixture is expensive.
