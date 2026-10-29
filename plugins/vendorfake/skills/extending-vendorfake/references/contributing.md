# Contributing a route to vendorfake

Fetched 2026-10-29 (first 2026-10-29) from vendorfake at tag v0.8.0 (0145227): AGENTS.md, docs/testing.md, tools/self-test.sh, tools/boundary.toml, docs/start/install.md, CHANGELOG.md.

## Contents

1. Layout
2. The one command
3. Boundary rules
4. Provenance labels
5. Conformance ids and the manifest
6. The two testing rules
7. The self-test rows a one-route change touches
8. Consuming a branch
9. Sources

## 1. Layout

AGENTS.md (https://github.com/konyklabs/vendorfake/blob/v0.8.0/AGENTS.md#layout): `core/` is "the framework-free kernel: state
store, state machine, capability registry, chaos engine, webhook dispatcher, retry,
clock, rng. Imports no web framework, ever."; `asgi/` is "the only place FastAPI is
imported"; `square/ clover/ toast/ lightspeed/` are "one vendor surface each:
routes, error vocabulary, signature scheme, retry schedule, seed." Tests:
`tests/unit/` "fast, no server, no vendor-specific fixtures required."; vendor
behaviour tests live in `tests/unit/<vendor>/` (docs/testing.md).

## 2. The one command

AGENTS.md (https://github.com/konyklabs/vendorfake/blob/v0.8.0/AGENTS.md#the-one-command):

```sh
uv sync --all-groups
tools/self-test.sh
```

"Every step is printed in a summary table at the end; a failure anywhere is a red
row, not a stack trace to scroll back to. Paste the command and its output as
evidence for any change -- "tests pass" is not evidence." self-test.sh itself:
"The summary table is the evidence a reviewer reads; the exit code is what the gate
reads." — https://github.com/konyklabs/vendorfake/blob/v0.8.0/tools/self-test.sh

## 3. Boundary rules

AGENTS.md (https://github.com/konyklabs/vendorfake/blob/v0.8.0/AGENTS.md#boundary-rules), with `tools/boundary.toml` "the whole
policy":

- "`vendorfake.core` imports no web framework (`fastapi`, `starlette`, `uvicorn`,
  `multipart`) -- enforced by import-linter, with `vendorfake.asgi` the sole named
  exception."
- "No vendor slug (`square`, `clover`, `toast`, ...) appears as a literal string
  or token under `core/` or `conformance/`"
- "The process environment is read in one function, `registry.ambient_env()`"

## 4. Provenance labels

AGENTS.md (https://github.com/konyklabs/vendorfake/blob/v0.8.0/AGENTS.md#provenance-labels): "Every behaviour this project
reproduces carries a label at the site".

- "**`DOCUMENTED`** (in comments and docstrings) / **`"documented"`** (in the
  `Vendorfake-Status-Provenance` header and `GET /__unit/errors`' `provenance`
  field) -- the vendor's own published documentation says so, cited at the site."
- "**`JUDGMENT`** / **`"judgment"`** -- this project chose the behaviour because
  no vendor page settles it."
- "**`"transport"`** -- `core/chaos/rules.py`'s `FaultProvenance`, for a fault
  that is about the HTTP transport rather than any vendor's documented behaviour"

"A citation with no working URL is worse than no citation -- check it before
committing it."

## 5. Conformance ids and the manifest

AGENTS.md (https://github.com/konyklabs/vendorfake/blob/v0.8.0/AGENTS.md#how-streams-and-conformance-ids-are-allocated):
"Conformance checks are C-numbered (`C01`, `C02`, ...) and registered with
`@check(id=...)` in whichever module under `src/vendorfake/conformance/checks/`
matches their subsystem"; "**Next free id: C38.**"; "`manifest.json` is the
committed id-to-name-and-expected-skips record; `tests/conformance/test_manifest.py`
fails if the live registry and the file disagree ... update the manifest in the
same change." The file is `src/vendorfake/conformance/manifest.json`. A route
that only extends one vendor's surface usually needs no new contract; a new
vendor-independent rule does.

## 6. The two testing rules

docs/testing.md (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/testing.md):

- "A test names the behaviour it protects and fails when that behaviour is removed."
- A test is deleted when "it asserts shape or prose only", when "it would pass
  against a stub that echoes its own fixture", or when "it duplicates a branch
  another test already fails on".

And: "A behaviour that differs between bindings is a parity case in
`tests/parity/`, marked `xfail(strict=True)` with the finding it tracks until the
contract holds".

## 7. The self-test rows a one-route change touches

From tools/self-test.sh at 0.8.0 (https://github.com/konyklabs/vendorfake/blob/v0.8.0/tools/self-test.sh), by the names the
summary table prints:

| Row | What makes it red |
| --- | --- |
| `prose ratio` | `tools/prose_ratio.py src --max-total 15`: the total prose share above 15%. Measured at 0.8.0 on 2026-10-29: `TOTAL lines=60280 code=42509 prose=9017 prose%=15.0`, so a growing docstring needs a trim elsewhere. |
| `docs` | `uv run python tools/gen_reference.py` changed `docs/reference` ("docs/reference is stale"), or `mkdocs build --strict` failed. A new route changes `routes-<vendor>.md`. |
| `pytest` / `coverage floor` | a failing test; coverage under the floor (`--fail-under=89`). |
| `matrix (<vendor>)` | the conformance run over every profile and binding for that vendor disagrees with `manifest.json`. |
| `fidelity fetch (toast)`, `fidelity pin (toast)` | Toast's extract is fetched, not committed: with no network they SKIP (exit 3); with network they can go red because upstream moved, unrelated to the change. |

The changelog form, from CHANGELOG.md's `## Unreleased` at 0.8.0: a bullet
`* **clover:** ... (konyklabs/roadmap#175).` — https://github.com/konyklabs/vendorfake/blob/v0.8.0/CHANGELOG.md

## 8. Consuming a branch

install.md (https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/install.md#pinning-a-commit-instead-of-a-tag): "a commit
on `main` between two tags still reports the *previous* release from
`importlib.metadata.version("vendorfake")` and `vendorfake.__version__`" and "a
runtime branch on `__version__` ... will take the wrong arm." The archive URL takes a
branch (`.../archive/refs/heads/<branch>.tar.gz`) or a sha as well as a tag
(verified by install 2026-10-29, not documented); a consumer image built from an
`ARG VENDORFAKE_REF` needs `docker compose build --no-cache` after the branch
moves, because the layer is cached on the ARG string, not on the branch head.

## 9. Sources

- https://github.com/konyklabs/vendorfake/blob/v0.8.0/AGENTS.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/testing.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/tools/self-test.sh
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/tools/boundary.toml
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/docs/start/install.md
- https://github.com/konyklabs/vendorfake/blob/v0.8.0/CHANGELOG.md

The docs are silent on consuming an unreleased branch from a container build;
section 8's last sentence is verified by install, not documented. Fetched 2026-10-29.
