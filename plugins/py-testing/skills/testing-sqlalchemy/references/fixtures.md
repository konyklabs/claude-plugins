# SQLAlchemy test fixtures reference

Fetched 2026-10-29 (first 2026-09-02; re-verified: SQLAlchemy 2.1.6 is the current release and the joined-session recipe is unchanged in its docs; SQLite pooling and pytest-alembic enabling text corrected below). Sources per section.

## Contents

1. Official external-transaction recipe (quoted)
2. Async variant (maintainer-endorsed, not in docs)
3. Postgres via testcontainers
4. SQLite pooling
5. Alembic in fixtures and pytest-alembic
6. Factories
7. Injecting the session into the app

## 1. Official recipe

https://docs.sqlalchemy.org/en/21/orm/session_transaction.html#joining-a-session-into-an-external-transaction-such-as-for-test-suites

```python
Session = sessionmaker()
engine = create_engine("postgresql+psycopg2://...")

class SomeTest(TestCase):
    def setUp(self):
        self.connection = engine.connect()
        self.trans = self.connection.begin()
        self.session = Session(bind=self.connection, join_transaction_mode="create_savepoint")
    def test_something(self):
        self.session.add(Foo())
        self.session.commit()
    def tearDown(self):
        self.session.close()
        self.trans.rollback()
        self.connection.close()
```

"create_savepoint" makes `commit()` release a SAVEPOINT only; the outer
transaction's rollback discards everything. The docs state SQLAlchemy's own
CI uses this.

## 2. Async variant

https://github.com/sqlalchemy/sqlalchemy/discussions/10857 (maintainer
confirmed; said it should be added to the docs; not there as of the fetch).

```python
engine = create_async_engine(url)

@pytest.fixture
async def session():
    async with engine.connect() as connection:
        async with connection.begin() as transaction:
            s = AsyncSession(bind=connection, join_transaction_mode="create_savepoint")
            yield s
            await transaction.rollback()
```

Needs `pytest-asyncio` (or anyio) with the fixture loop scope matching the
engine's; dispose the engine at session end, or build it with `NullPool`,
which the maintainer suggested in the same thread (no pooled connection
outlives its loop, so no "attached to a different loop" error).

## 3. Postgres via testcontainers

https://testcontainers-python.readthedocs.io/en/latest/modules/postgres/README.html

```python
from testcontainers.postgres import PostgresContainer

@pytest.fixture(scope="session")
def db_url():
    with PostgresContainer("postgres:16") as pg:
        yield pg.get_connection_url()      # driver=None for a driverless URL
```

Docker must be available; on CI without Docker-in-Docker, a Postgres service
container and `DATABASE_URL` env var replace this fixture (not sourced this
fetch; standard CI practice).

## 4. SQLite pooling

https://docs.sqlalchemy.org/en/21/dialects/sqlite.html#threading-pooling-behavior

- `:memory:` exists only within one DBAPI connection; "not suitable for use
  with multiple concurrent threads or coroutines" without serialisation. Its
  default pool is `SingletonThreadPool` (one connection per thread).
- `StaticPool`: one connection shared by all sessions, "only appropriate
  when access to the engine is fully serialized, such as in single-threaded
  test suites".
- File databases use `QueuePool` (since 2.0; `NullPool` before) and SQLAlchemy
  sets `check_same_thread=False` for them itself.
- `mode=memory` URLs (SQLAlchemy 2.1): the implicit single-connection pool
  is deprecated; pick a pool explicitly.

```python
engine = create_engine("sqlite://", poolclass=StaticPool,
                       connect_args={"check_same_thread": False})
```

A file database (`sqlite:///{tmp_path}/t.db`) avoids all of this.

## 5. Alembic in fixtures and pytest-alembic

https://alembic.sqlalchemy.org/en/latest/cookbook.html

```python
from alembic.config import Config
from alembic import command

cfg = Config("/path/to/alembic.ini")
command.upgrade(cfg, "head")
```

Sharing the test connection:

```python
with engine.begin() as connection:
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")
```

with `env.py` checking `config.attributes.get("connection")` before creating
its own engine.

pytest-alembic (https://github.com/schireson/pytest-alembic,
https://pytest-alembic.readthedocs.io/en/latest/running.html) built-in tests:
`test_single_head_revision`, `test_upgrade`,
`test_model_definitions_match_ddl`, `test_up_down_consistency`; experimental
opt-ins `test_all_models_register_on_metadata`,
`test_downgrade_leaves_no_trace`, enabled with the ini option
`pytest_alembic_include_experimental` (e.g. `'downgrade_leaves_no_trace'`).
Run the built-ins with `pytest --test-alembic`, or import the four names from
`pytest_alembic.tests` into a test module; do not do both (they run twice).
Both need an `alembic_engine` fixture: an engine on an empty database, not
inside a manually created transaction. Alembic commits internally, so these
tests cannot ride the savepoint-rollback session fixture; give them their own
database.

## 6. Factories

factory_boy https://factoryboy.readthedocs.io/en/stable/orms.html:
`SQLAlchemyModelFactory`; `Meta.sqlalchemy_session` or
`sqlalchemy_session_factory` (3.3.0+, mutually exclusive);
`sqlalchemy_session_persistence` in `None`, `"flush"`, `"commit"`; docs
recommend `scoped_session` for tests.

```python
class OrderFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = Order
        sqlalchemy_session_factory = lambda: current_session()
        sqlalchemy_session_persistence = "flush"
    sku = factory.Sequence(lambda n: f"SKU{n}")
```

polyfactory https://polyfactory.litestar.dev/latest/reference/factories/sqlalchemy_factory.html:
`SQLAlchemyFactory[Model]`, flags `__set_primary_key__`,
`__set_foreign_keys__`, `__set_relationships__`, `__set_association_proxy__`,
`__persistence_method__`; `create_sync()` / `create_async()`.

## 7. Injecting the session into the app

The savepoint recipe only works if the code under test uses the test's
connection. Patterns, in order of preference:

1. The app takes a `sessionmaker` (or session) as a dependency; the test
   passes `sessionmaker(bind=connection, join_transaction_mode="create_savepoint")`.
2. A framework dependency override (FastAPI `app.dependency_overrides`,
   Flask app factory argument).
3. Monkeypatching a module-level `SessionLocal`. Works; hides the coupling.
