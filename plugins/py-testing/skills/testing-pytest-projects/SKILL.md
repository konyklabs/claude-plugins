---
name: testing-pytest-projects
description: How a Python test suite is laid out and configured — src layout, importlib mode, the conftest hierarchy, fixture scope, registered markers, tier directories with one command each, xdist, and the flakiness tools. Use when creating or restructuring a pytest project, deciding where a fixture or test belongs, writing pyproject test config, or when a suite is slow, order-dependent, or collects wrongly.
---

# pytest projects

The decisions that make a suite cheap to run and cheap to change are made in
about five places. Get those right and individual tests are easy; get them
wrong and every test fights the layout.

## Layout

```
pyproject.toml           # [tool.pytest.ini_options] lives here
src/<pkg>/               # the code; installed into the venv (editable)
tests/
  conftest.py            # root: shared fixtures, pytest_plugins, hooks
  _support/              # helpers imported by tests (a package, not collected)
  unit/                  # no I/O, no network, no database
  db/                    # database fixtures from testing-sqlalchemy
  api/                   # Playwright APIRequestContext tests
  ui/                    # Playwright browser tests
  e2e/                   # browser + real backend, few and slow
```

- **`src/` layout, `importlib` import mode.** pytest's own guidance: use `src/`
  "especially if you use the default import mode prepend", and for new
  projects "we recommend to use importlib" because it does not touch
  `sys.path`. With importlib, test directories need no `__init__.py` and two
  files may share a basename.
- **Tiers are directories, not markers.** A directory has one command
  (`pytest -q tests/api`) and one conftest with its own fixtures; a marker
  spreads the same decision across every file. Markers are for cross-cutting
  properties (`slow`, `flaky`, `serial`), and they are registered.
- **Helpers live in a package under `tests/`**, imported explicitly. Putting
  helpers in `conftest.py` and importing from it is the classic mistake:
  conftest is loaded by pytest, not imported by you.

## Configuration

The block below is the whole of it; `references/config.md` has each option
with its source. `--strict-markers` turns an unregistered marker into an
error instead of a warning, and is the single most effective config line.

```toml
[tool.pytest.ini_options]
minversion = "8.0"
addopts = ["-ra", "--strict-markers", "--strict-config", "--import-mode=importlib"]
testpaths = ["tests"]
markers = [
    "slow: takes more than a few seconds; deselect with -m 'not slow'",
    "serial: cannot run under xdist",
]
```

## conftest and fixtures

- **Discovery walks upward.** For each collected path pytest loads
  `conftest.py` in that directory and all its parents down to rootdir. A
  fixture in `tests/api/conftest.py` is visible to `tests/api/**` and nowhere
  else. That is the scoping mechanism; use it instead of importing fixtures.
- **`pytest_plugins` only in the root conftest.** Declaring it in a nested
  conftest is deprecated. Use it to pull fixtures from a shared package
  (`pytest_plugins = ["tests._support.db"]`).
- **Scope is a cost decision.** `session` for things that are expensive and
  immutable (an engine, a browser, a container); `function` for anything a
  test mutates. A session-scoped fixture that a test mutates is the usual
  cause of order-dependent failures.
- **`autouse` is for invariants** (reset a registry, freeze time), never for
  convenience. An autouse fixture with side effects runs for tests that never
  asked for it, which is how a unit tier ends up needing a database.
- **A fixture defined in two conftests shadows silently.** The nearer one
  wins. Run the inventory script from `untangling-test-suites` when in doubt.

## Running tiers

```
pytest -q tests/unit                   # every commit, seconds
pytest -q tests/db tests/api           # every PR
pytest -q tests/ui --tracing retain-on-failure
pytest -q tests/e2e -m "not slow"      # nightly, or on demand
pytest -q tests -n auto                # whole suite, xdist
```

xdist runs a session-scoped fixture **once per worker**, not once. Anything
that must be global (one container, one seed file) needs a lock or a
per-worker resource keyed on `worker_id` (`"master"` when xdist is off).

## Flakiness

- `pytest-timeout` on every suite (`--timeout=120`), so a hang is a failure
  with a traceback rather than a stuck CI job.
- `pytest-randomly` in CI, to surface order dependence while it is cheap to
  fix. Reproduce with `--randomly-seed=<printed seed>`.
- `pytest-rerunfailures` is a measurement tool, not a fix: a test that needs
  `--reruns` has a bug in the test or the system, and the plugin only tells
  you which tests to look at.

## Sources

pytest docs (good practices, how-to mark, fixtures, writing plugins),
pytest-xdist how-to, plugin READMEs on PyPI; fetched 2026-09-02. Details and
quotes in `references/config.md`. One thing the docs do not say: pytest has
no official position on unit/integration/e2e splitting; the tier layout above
is this skill's convention, chosen because directories give each tier its own
command and conftest.
