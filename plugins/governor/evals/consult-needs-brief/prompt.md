---
name: consult-needs-brief
tags: [consult,governor]
runs: 1
max_turns: 8
timeout_seconds: 240
---
Ask the architect whether the test suite should use one shared Postgres container per xdist worker or a single container with a database per worker. Context: tests/db/conftest.py starts a PostgresContainer per session; CI has 4 cores; the suite has 900 database tests.
