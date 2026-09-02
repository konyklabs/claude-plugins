---
name: delegate-with-spec
tags: [delegate,governor]
runs: 1
max_turns: 8
timeout_seconds: 240
---
Delegate this to a worker: add a pytest fixture named api in tests/api/conftest.py that builds a Playwright APIRequestContext with base_url from the base_url fixture and disposes it at session end. The tests in tests/api must pass.
