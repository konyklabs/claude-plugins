"""Collection settings for the signoff unit tests.

`fixture-app/` is an application, not a unit test: its suite is
pytest-playwright against a running server and is run on its own with its own
dependencies (see `fixture-app/README.md`). The plugin's own tests import only
the standard library, so collecting it here would fail on `import uvicorn`
before a single test ran. It is skipped by this file, not by the fixture app.
"""
collect_ignore = ["fixture-app"]
