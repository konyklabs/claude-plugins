---
name: testing-sqlalchemy
description: Database tests for SQLAlchemy 2.x — the savepoint-rollback session fixture (sync and async), engine scope, SQLite pooling caveats, running Alembic migrations in tests and checking them with pytest-alembic, Postgres via testcontainers, and factories with factory_boy or polyfactory. Use when writing or fixing tests that touch a database, when tests leak state into each other, when a migration needs a test, or when a suite creates the schema per test.
---

# SQLAlchemy tests

Two facts decide the design. A test that commits must not leave anything
behind, and creating the schema is the slowest thing a database test does.
So the schema is built once per session, and every test runs inside a
transaction that is rolled back, with `commit()` inside the test turned into
a savepoint release.

## The fixture (official recipe, as pytest fixtures)

```python
# tests/db/conftest.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

@pytest.fixture(scope="session")
def engine(db_url):                       # db_url: see Postgres / SQLite below
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)      # or run migrations, see below
    yield engine
    engine.dispose()

@pytest.fixture
def session(engine):
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    transaction.rollback()
    connection.close()
```

`join_transaction_mode="create_savepoint"` is the whole trick: the docs say
`session.commit()` then only releases a SAVEPOINT and the outer transaction,
which the fixture rolls back, stays in control. SQLAlchemy runs its own
suite this way. Code under test that opens its own session must get this
one: inject the session (or a `sessionmaker` bound to `connection`) instead
of importing a global.

Async variant with `AsyncSession(bind=connection, join_transaction_mode=
"create_savepoint")` inside `async with engine.connect()` and
`connection.begin()`: maintainer-endorsed, not yet in the official docs; code
in `references/fixtures.md`.

## Which database

- **Postgres in a container** for anything that will run on Postgres.
  `testcontainers` gives a session-scoped `PostgresContainer("postgres:16")`
  and `get_connection_url()`. Dialect-specific SQL, constraints and JSON
  behaviour do not survive a SQLite stand-in.
- **SQLite file** (`tmp_path / "test.db"`) for pure-ORM code with no
  dialect-specific features. Fast, and the savepoint recipe works.
- **SQLite `:memory:`** only with `StaticPool` and a single thread: the
  database lives in one DBAPI connection, and the docs call it "not suitable
  for use with multiple concurrent threads or coroutines". A second
  connection sees an empty database.

## Migrations

If the app has Alembic, the test schema comes from the migrations, never
from `create_all`, or the tests pass on a schema production will not have.

```python
from alembic import command
from alembic.config import Config

@pytest.fixture(scope="session")
def engine(db_url):
    engine = create_engine(db_url)
    cfg = Config("alembic.ini"); cfg.set_main_option("sqlalchemy.url", db_url)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn   # env.py must honour this
        command.upgrade(cfg, "head")
    yield engine
    engine.dispose()
```

Add `pytest-alembic` and its four built-in tests: single head, upgrade from
empty, models match DDL (autogenerate produces nothing), up/down
consistency. They catch the migration bugs a feature test never exercises.

## Test data

- **factory_boy** `SQLAlchemyModelFactory` with `sqlalchemy_session_factory`
  bound to the test session and `sqlalchemy_session_persistence = "flush"`.
  The docs recommend a `scoped_session` so factories share the session
  without per-factory wiring.
- **polyfactory** `SQLAlchemyFactory` when models are typed and random
  valid data is wanted (`__set_relationships__`, `create_sync` /
  `create_async`).
- Either way: factories build, tests assert. A test that reads a factory's
  default value is asserting the factory.

## Habits that keep it green

- `expire_on_commit=False` on test sessions if code reads attributes after
  a commit; otherwise every attribute access after commit is a query, and a
  detached-instance error is the usual symptom. (Practice, not a docs quote.)
- One session per test, never a module-level `Session()`. Order dependence
  in a database suite is almost always a shared session or `:memory:` SQLite.
- Under xdist, one database per worker (container per worker or a database
  name suffixed with `worker_id`), or the session fixtures collide.

Full code, the async fixture, container fixture and factory setup in
`references/fixtures.md`.

## Sources

SQLAlchemy 2.0 docs (session_transaction "Joining a Session into an External
Transaction", sqlite dialect "Threading/Pooling Behavior"), sqlalchemy
discussion #10857 (async recipe, maintainer-endorsed), Alembic cookbook,
pytest-alembic README, testcontainers-python Postgres module, factory_boy ORM
docs, polyfactory SQLAlchemyFactory docs; fetched 2026-09-02.
