# pytest configuration reference

Fetched 2026-10-29 (first 2026-09-02) from docs.pytest.org (stable, pytest 9.2.0), pytest-xdist docs, and PyPI.

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
- `prepend` (the default, and "for the foreseeable future": the plan to make
  `importlib` the default was dropped) searches upward for "the last directory
  which still contains an `__init__.py`" and puts its parent on `sys.path`.
  With `__init__.py` in test dirs, pytest prepends the repository root to
  `sys.path`, so the package under test also imports from the checkout.
  https://docs.pytest.org/en/stable/explanation/pythonpath.html
- `importlib` mode: "pytest will not change `sys.path` at all" and module
  names need not be unique, but test modules cannot import each other; share
  helpers through `conftest.py` fixtures or an installed package.

## 2. `[tool.pytest.ini_options]` keys

https://docs.pytest.org/en/stable/reference/customize.html and
https://docs.pytest.org/en/stable/reference/reference.html. Two tables:
`[tool.pytest.ini_options]` (INI-style, values are strings, since 6.0) and
the native `[tool.pytest]` (pytest >= 9.0, real TOML arrays, e.g.
`addopts = ["-ra", "-q"]`). Use one, not both; a floor below 9 needs
`ini_options`.

| key | use |
|---|---|
| `minversion` | fail early on an old pytest |
| `addopts` | default CLI flags; keep `-ra` (summary of non-passing) |
| `strict` | 9.0+: one switch for `strict_config`, `strict_markers`, `strict_parametrization_ids`, `strict_xfail`; an explicit individual option wins; it also enables strictness options added later, so use it only with a pinned pytest |
| `strict_config` / `strict_markers` | warnings parsing the config / unregistered markers become errors (the `--strict-config` / `--strict-markers` flags do the same) |
| `testpaths` | directories collected when none are given |
| `markers` | registered markers, `name: description` |
| `filterwarnings` | `error::DeprecationWarning` turns warnings into failures |
| `strict_xfail = true` | an XPASS fails, so xfails cannot rot; renamed in 9.0, `xfail_strict` still works as an alias |

Default collection patterns: `python_files` `["test_*.py", "*_test.py"]`,
`python_classes` `["Test"]` (a name prefix), functions `test_*`.

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

`--strict-markers` (or `strict_markers = true`): an unregistered
`@pytest.mark.<name>` is an error, and so is an unregistered name inside a `-m`
expression. `-m "expr"` selects by marker expression (`-m "not slow and not
serial"`).

## 4. conftest discovery and `pytest_plugins`

https://docs.pytest.org/en/stable/how-to/writing_plugins.html

- "For each test path, load `conftest.py` and `test*/conftest.py` relative to
  the directory part of the test path, if exist. Before a `conftest.py` file is
  loaded, load `conftest.py` files in all of its parent directories."
- "Requiring plugins using `pytest_plugins` variable in non-root `conftest.py`
  files is deprecated." A `pytest_plugins` outside the tests root raises a
  warning; keep it in the root `conftest.py`.

## 5. Fixture scope and autouse

https://docs.pytest.org/en/stable/how-to/fixtures.html

Scopes: `function` (default) < `class` < `module` < `package` < `session`,
torn down at the end of their boundary. `autouse=True`: every test in scope
requests it implicitly; "that doesn't mean they can't be requested though;
just that it isn't necessary". `@pytest.mark.usefixtures` cannot be applied to
fixture functions.

## 6. xdist

https://pytest-xdist.readthedocs.io/en/stable/how-to.html

- `pip install pytest-xdist`; `pytest -n auto` (one worker per available CPU).
- "each worker process will perform its own collection and execute a subset
  of all tests ... tests in different processes requesting a high-level scoped
  fixture (for example session) will execute the fixture code more than
  once." Coordinate with a file lock.
- `worker_id` fixture: `"gw0"`.. under xdist, `"master"` without. Env:
  `PYTEST_XDIST_WORKER`, `PYTEST_XDIST_WORKER_COUNT`.

## 7. Flakiness plugins

- pytest-rerunfailures — `--reruns 5 --reruns-delay 1`;
  `@pytest.mark.flaky(reruns=5, reruns_delay=2)`; `--only-rerun REGEX` /
  `--rerun-except REGEX` filter which failures rerun (marker args `only_rerun=`
  / `rerun_except=` override the flags). Precedence: marker, then CLI, then
  ini. https://pypi.org/project/pytest-rerunfailures/
- pytest-randomly — shuffles module, class, function order; reseeds
  `random` before each test; prints the seed; `--randomly-seed=1234` or
  `=last` (needs the cacheprovider); works with xdist; `-p no:randomly`
  disables it. To chase pollution, rerun the failing module with the printed
  seed. https://pypi.org/project/pytest-randomly/
- pytest-timeout — `--timeout=300` (also the `timeout` ini key and
  `PYTEST_TIMEOUT`; `0` disables); `@pytest.mark.timeout(60)`; method
  `signal` (the default where SIGALRM exists; teardown runs, may interfere
  with SIGALRM users) or `thread` (kills the process, no teardown).
  2.5.0 was yanked as a breaking change; 2.4.0 was the latest usable release.
  https://pypi.org/project/pytest-timeout/
- Hypothesis — `@given(st.integers(0, 200))` on a plain test function; pytest
  collects it normally. https://hypothesis.readthedocs.io/en/latest/quickstart.html
