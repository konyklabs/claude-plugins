# pytest configuration reference

Fetched 2026-09-02 from docs.pytest.org (stable), pytest-xdist docs, and PyPI.

## Contents

1. Layout and import mode
2. `[tool.pytest.ini_options]` keys
3. Markers
4. conftest discovery and `pytest_plugins`
5. Fixture scope and autouse
6. xdist
7. Flakiness plugins

## 1. Layout and import mode

- "Generally, but especially if you use the default import mode `prepend`, it
  is strongly suggested to use a `src` layout."
  https://docs.pytest.org/en/stable/explanation/goodpractices.html
- "For new projects, we recommend to use `importlib`" — `sys.path` is not
  changed when importing test modules; test files may share basenames without
  `__init__.py`. Same page.
- rootdir for prepend/append: "the first upward directory not containing an
  `__init__.py`". Adding `__init__.py` to test dirs "introduces side effects
  with sys.path manipulation".

## 2. `[tool.pytest.ini_options]` keys

https://docs.pytest.org/en/stable/reference/customize.html (TOML form needs
pytest >= 6.0).

| key | use |
|---|---|
| `minversion` | fail early on an old pytest |
| `addopts` | default CLI flags; keep `-ra` (summary of non-passing), `--strict-markers`, `--strict-config` |
| `testpaths` | directories collected when none are given |
| `markers` | registered markers, `name: description` |
| `filterwarnings` | `error::DeprecationWarning` turns warnings into failures |
| `xfail_strict = true` | an XPASS fails, so xfails cannot rot |

Default collection patterns (well known, not re-quoted this fetch):
`test_*.py` / `*_test.py`, classes `Test*`, functions `test_*`.

## 3. Markers

https://docs.pytest.org/en/stable/how-to/mark.html

```toml
[tool.pytest.ini_options]
addopts = ["--strict-markers"]
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "serial",
]
```

`--strict-markers`: unregistered `@pytest.mark.<name>` is an error. `-m
"expr"` selects by marker expression (`-m "not slow and not serial"`).

## 4. conftest discovery and `pytest_plugins`

https://docs.pytest.org/en/stable/how-to/writing_plugins.html

- "For each test path, load `conftest.py` and `test*/conftest.py` relative to
  the directory part of the test path, if exist. Before a `conftest.py` file is
  loaded, load `conftest.py` files in all of its parent directories."
- "Requiring plugins using `pytest_plugins` variable in non-root `conftest.py`
  files is deprecated."

## 5. Fixture scope and autouse

https://docs.pytest.org/en/stable/how-to/fixtures.html

Scopes: `function` (default) < `class` < `module` < `package` < `session`,
torn down at the end of their boundary. `autouse=True`: every test in scope
requests it implicitly; "that doesn't mean they can't be requested though;
just that it isn't necessary". `@pytest.mark.usefixtures` cannot be applied to
fixture functions.

## 6. xdist

https://pytest-xdist.readthedocs.io/en/stable/how-to.html

- `pip install pytest-xdist`; `pytest -n auto`.
- "each worker process will perform its own collection and execute a subset
  of all tests ... tests in different processes requesting a high-level scoped
  fixture (for example session) will execute the fixture code more than
  once." Coordinate with a file lock.
- `worker_id` fixture: `"gw0"`.. under xdist, `"master"` without. Env:
  `PYTEST_XDIST_WORKER`, `PYTEST_XDIST_WORKER_COUNT`.

## 7. Flakiness plugins

- pytest-rerunfailures — `--reruns 5 --reruns-delay 1`;
  `@pytest.mark.flaky(reruns=5, reruns_delay=2)`; `only_rerun` / `rerun_except`
  regex filters. https://pypi.org/project/pytest-rerunfailures/
- pytest-randomly — shuffles module, class, function order; reseeds
  `random` before each test; prints the seed; `--randomly-seed=1234` or
  `=last`; works with xdist. https://pypi.org/project/pytest-randomly/
- pytest-timeout — `--timeout=300`; `@pytest.mark.timeout(60)`; method
  `signal` (teardown runs, may interfere with SIGALRM users) or `thread`
  (kills the process, no teardown). https://pypi.org/project/pytest-timeout/
- Hypothesis — `@given(st.integers(0, 200))` on a plain test function; pytest
  collects it normally. https://hypothesis.readthedocs.io/en/latest/quickstart.html
