---
name: sqlalchemy-savepoint-fixture
tags: [sqlalchemy,py-testing]
runs: 1
max_turns: 8
timeout_seconds: 240
---
Write the pytest fixtures for a SQLAlchemy 2.0 project so that each test runs in a transaction that is rolled back afterwards, even when the code under test calls session.commit(). Postgres in CI.
