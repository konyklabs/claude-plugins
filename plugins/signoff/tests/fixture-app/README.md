# Fixture app: a generic developer portal

A small FastAPI application that exists to give the `signoff` plugin's
scripts something real to explore, mine, tile and report on. It has no
company, product or vertical identity: placeholder accounts, placeholder
secrets, generic screens.

## Run the app

```
cd plugins/signoff/tests/fixture-app
uv run --with fastapi --with uvicorn uvicorn app:app --reload
```

Open `http://127.0.0.1:8000/login`.

## Accounts

| role | email | password |
|---|---|---|
| member | `member@example.invalid` | `placeholder-member` |
| admin | `admin@example.invalid` | `placeholder-admin` |

Sessions are a random token in an in-memory dict; there is no real
persistence and restarting the app clears everything, including any
credential created through the UI.

## Run the suite

```
cd plugins/signoff/tests/fixture-app
uv run --with fastapi --with uvicorn --with pytest-playwright python -m pytest -q e2e
```

If Chromium is not yet installed for Playwright:

```
uv run --with playwright python -m playwright install chromium
```

`e2e/conftest.py` starts the app with uvicorn in a background thread on a
free port for the session and exposes it as the `base_url` fixture; it also
offers a `login(page, role)` helper used by both test files. Tests claim
signoff tiles with `@pytest.mark.tile("<tile id>")`, registered in
`pytest.ini` beside this suite. `plugins/signoff/formats.md` is the one home
of the id and file formats these fixtures follow.

## What the suite covers, and what it does not

The suite claims exactly seven tiles on purpose, so that later signoff
scripts have real gaps to report:

- `auth.login.render.anonymous`
- `auth.sign-in.valid-password` (together with `org.home.render.member`)
- `auth.sign-in.wrong-password`
- `auth.session.required-for-app`
- `credentials.create.name-validation`
- `docs.home.public`

Left deliberately uncovered: signing out, removing a member, revoking a
credential, creating a credential (only its name validation is claimed, not
the success path that shows a secret), the admin settings guard, and every
render or error-state tile not listed above — including every admin-role
render, most member-role renders, and all three error-state tiles
(`auth.login.error.invalid-credentials`, `credentials.new.error.invalid-name`,
`admin.settings.error.forbidden`). The full, ranked list is
`expected.json`'s `gaps` array.

## Ground truth

`.qa/map.json` and `.qa/rules.json` are hand-written to match what the app
above actually does, field for field against `plugins/signoff/formats.md`;
they stand in for what an `explore` and a `mine` step would otherwise
produce (those are agents, out of scope for this slice). `expected.json`
is the tile count, the covered list and the ranked gap list derived by hand
from `map.json`, `rules.json` and the suite's `@pytest.mark.tile` claims —
render and error-state tiles carry no risk in `formats.md`'s own schema, so
this fixture assigns render tiles `low` and error-state tiles `medium`,
consistently, to make the gap ranking well-defined; a later script may
choose differently, and `expected.json` would move with it.
