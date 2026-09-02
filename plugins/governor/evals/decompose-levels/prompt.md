---
name: decompose-levels
tags: [decompose,governor]
runs: 1
max_turns: 8
timeout_seconds: 240
---
Decompose this work into slices: migrate 60 test files under tests/ from a shared module-level SQLAlchemy session to a per-test savepoint session fixture, across tests/api, tests/services and tests/reports, which all import helpers from tests/_support/db.py.
