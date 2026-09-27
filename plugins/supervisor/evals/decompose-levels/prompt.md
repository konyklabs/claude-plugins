---
name: decompose-levels
tags: [decompose,supervisor]
runs: 1
max_turns: 8
timeout_seconds: 240
---
<!-- supervisor:arm enforce -->
Decompose this work into slices and levels, from the inventory below; the repository itself is not available in this workspace, so plan from the inventory and say which facts a scout would have to confirm. Do not create or edit files.

Work: migrate 60 test files under tests/ from a shared module-level SQLAlchemy session to a per-test savepoint session fixture.

Inventory (from a scout):
- tests/_support/db.py: creates the module-level `session` and the helpers `make_user`, `make_order`, `seed_catalog`, all of which close over that session; imported by every test module below.
- tests/conftest.py: engine fixture only, session scope; no session fixture yet.
- tests/api/: 24 files, 310 tests; import `make_user`, `make_order`; 3 files call `session.commit()` directly.
- tests/services/: 21 files, 260 tests; import all three helpers; tests/services/test_billing.py opens a second session by hand.
- tests/reports/: 15 files, 190 tests; import `seed_catalog` only; read-heavy, no commits.
- No directory imports another; CI runs `pytest -q tests -n 4`.

