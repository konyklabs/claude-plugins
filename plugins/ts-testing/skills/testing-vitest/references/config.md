# Vitest configuration reference

Fetched 2026-09-29 from vitest.dev, the vitest-dev/vitest repository's
`docs/config/` pages, the v4 and v5 migration guides, the 4.0/4.1/5.0
release posts, and the npm registry.

## Contents

1. Versions and what a v3-era config gets wrong
2. Config file precedence and the shared Vite config
3. environment, include/exclude, setupFiles vs globalSetup
4. projects (the workspace replacement)
5. pool and isolate
6. typecheck config keys
7. coverage provider
8. testTimeout, retry, clearMocks, passWithNoTests
9. reporters

## 1. Versions and what a v3-era config gets wrong

v5.0.0 shipped 2026-09-03, needs Node ≥22.12.0 and Vite ≥6.4.0; npm's
current release is 5.0.2 — https://vitest.dev/blog/vitest-5.html,
https://registry.npmjs.org/vitest. v3→v4 (2025-10-22) removed the
`workspace` key and the `vitest.workspace.ts` file (deprecated since 3.2)
in favor of `projects`; flattened `poolOptions.*` to top level;
renamed `maxThreads`/`maxForks` to `maxWorkers` and
`singleThread`/`singleFork` to `maxWorkers: 1, isolate: false`; moved
`deps.external`/`deps.inline`/`deps.fallbackCJS` to `server.deps.*`;
removed `coverage.all`, `coverage.extensions`, `coverage.ignoreEmptyLines`;
narrowed the default `exclude` to just `node_modules` and `.git` (`dist`
and `cypress` are no longer auto-excluded); raised the floor to Node ≥20,
Vite ≥6 — https://v4.vitest.dev/guide/migration. v4→v5 flipped `clearMocks`
from `false` to `true` and requires hoisted `vi.mock`/`vi.unmock`/
`vi.hoisted` calls to sit at file top level (a nested one now throws) —
https://vitest.dev/guide/migration.html. A config copied from a v3 project
that still declares `workspace` or `poolOptions.threads.singleThread` is
the first thing to fix on an upgrade.

## 2. Config file precedence and the shared Vite config

Vitest reads `vite.config.*` by default, so Vite plugins and aliases are
already in effect for tests; `vitest.config.*` overrides `vite.config.*`
when both exist — https://vitest.dev/guide/. One file is enough for most
projects: put `test` inside the same `defineConfig` Vite already uses, and
add a second `vitest.config.ts` only when the two need to diverge.

## 3. environment, include/exclude, setupFiles vs globalSetup

- `environment`: `'node' | 'jsdom' | 'happy-dom' | 'edge-runtime' | string`,
  default `'node'`; a single file can override it with a
  `// @vitest-environment jsdom` docblock (also accepts `@jest-environment`)
  — https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/environment.md.
- `globals` defaults to `false` — tests import `describe`/`it`/`expect`
  explicitly unless this is turned on —
  https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/globals.md.
- `include` defaults to `['**/*.{test,spec}.?(c|m)[jt]s?(x)']`; `exclude`
  defaults to `['**/node_modules/**', '**/.git/**']` and does not affect
  coverage, which has its own `coverage.exclude` —
  https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/exclude.md.
- `setupFiles` run inside the same worker, before each test file; put
  matcher registration and per-test globals there. `globalSetup` runs once
  in the main thread before workers start, sees no test globals, and hands
  data to tests through `project.provide()` / `inject()` — a database
  container or a server started once belongs here, not in `setupFiles` —
  https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/setupfiles.md,
  https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/globalsetup.md.

## 4. projects (the workspace replacement)

`projects` is a `TestProjectConfiguration[]`, default `[]`; a root config
that declares `projects` is a container and runs no tests of its own —
https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/projects.md.
This is the direct replacement for the removed `vitest.workspace.ts` file:
list each package's config (or a glob to its config file) under `projects`
in the root config instead of a separate workspace file.

## 5. pool and isolate

`pool` is one of `'threads' | 'forks' | 'vmThreads' | 'vmForks'`, default
`'forks'`. `threads` uses `worker_threads`: cannot call `process.chdir`,
and native modules (Prisma, bcrypt, canvas are named) can segfault. `forks`
uses `child_process`: slower IPC, full process API, the best native-module
compatibility, and the default for that reason. `vmThreads` sandboxes each
test in a VM inside worker threads: fast, but has known memory-leak and
ESM-instability issues, and recycling a worker is expensive because GC runs
on shared background threads. `vmForks` sandboxes in child processes, so
recycling is cheap (it just exits) —
https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/pool.md.
`isolate` defaults to `true`; turning it off speeds up side-effect-free
suites but shares module state between test files in the same worker, and
has no effect at all under the `vm*` pools —
https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/isolate.md.

## 6. typecheck config keys

`typecheck.enabled` defaults to `false`; `typecheck.checker` defaults to
`'tsc'` (or `'vue-tsc'`); `typecheck.include` defaults to
`['**/*.{test,spec}-d.?(c|m)[jt]s?(x)']`; `typecheck.spawnTimeout` defaults
to 10000 ms —
https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/typecheck.md.
These files are never executed; see `fixtures-and-structure.md` for how
type tests are written and run.

## 7. coverage provider

Default provider is `v8`; it needs no pre-transpile step and, since 3.2.0,
matches istanbul's accuracy through AST remapping, but only runs on V8
engines (not Firefox, not Bun). `istanbul` is instrumentation-based, runs
anywhere, and is slower for a full run —
https://vitest.dev/guide/coverage.html. The exact shape of
`coverage.thresholds` was not confirmed this fetch (NOT FOUND); check the
current coverage guide before writing one.

## 8. testTimeout, retry, clearMocks, passWithNoTests

- `testTimeout` defaults to 5000 ms, or 15000 ms when `browser.enabled` —
  https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/testtimeout.md.
- `retry` defaults to `0`; a number or `{ count, delay, condition }`. A
  `condition` function must live in the test file, not the config, because
  the config is serialized to workers —
  https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/retry.md.
- `clearMocks` defaults to `true` as of v5 (it was `false` before); the
  docs warn this can break concurrent async tests, since one test finishing
  clears mock history another concurrent test still needs —
  https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/clearmocks.md.
- `passWithNoTests` defaults to `false` — a project with zero matching test
  files fails the run rather than passing silently —
  https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/passwithnotests.md.

## 9. reporters

Built-in reporters: `default`, `verbose`, `tree`, `dot`, `junit`, `json`,
`html`, `tap`, `tap-flat`, `hanging-process`, `github-actions`, `minimal`
(alias `agent`), `blob`; default is `'default'` —
https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/reporters.md.
`github-actions` is additive (it emits annotations alongside another
reporter); see `fixtures-and-structure.md` for how CI combines these with
`--shard` and `--merge-reports`.
