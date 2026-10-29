---
name: testing-vitest
description: Testing TypeScript projects with Vitest — the config that shares vite.config.ts, the v4 and v5 changes a suite written from memory gets wrong (projects not workspace, clearMocks on by default, hoisted vi.mock at top level, unawaited async assertions failing), vi.mock hoisting and the ESM spy limits, fake timers, test.extend fixtures with scopes, typecheck mode for *.test-d.ts, coverage providers, CI reporters and sharding, and the traps that make suites flaky or slow. Use when writing or fixing Vitest tests, setting up vitest.config.ts, when a mock is never hit, a timer test hangs, or a suite is slow or order-dependent.
---

# Vitest for TypeScript projects

Vitest 5 is current (5.0.5 on 2026-10-15, 5.1 in beta; Node 22.12+, Vite 6.4+). A
suite written from memory of Vitest 2 or 3 fails on three things first:
the `workspace` file is gone, mocks are cleared between tests by default,
and an `expect(...).resolves` you forgot to `await` is now a failing test.

## 1. Options come from the docs, not from memory

Vitest ships a minor about monthly and renamed or removed a dozen config
keys between 3 and 5. Before writing a key you have not written this month:

- **Context7 when the MCP server is present**: resolve the library id for
  `vitest` once, then query the option by name (`vitest config pool`).
- **Otherwise the raw markdown the docs site renders**, one file per option:
  `https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/<option>.md`
  (`guide/`, `api/` for the rest; `guide/migration/index.md` for the
  version you are leaving).

`references/config.md` carries the defaults and renames; `mocking.md` the
mocking rules; `fixtures-and-structure.md` fixtures, tables and the CLI.

## 2. One config, shared with Vite

```ts
// vite.config.ts — Vitest reads it by default, so plugins and aliases are
// shared. A vitest.config.ts REPLACES it rather than extending it: add one
// only when test config must diverge, and repeat the plugins there
/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import tsconfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  plugins: [tsconfigPaths()],        // tsconfig `paths` resolved in tests too
  test: {
    environment: "node",             // the default; jsdom/happy-dom per file with a docblock
    setupFiles: ["test/setup.ts"],   // runs in each worker before each test file
    coverage: { provider: "v8", include: ["src/**"] },
  },
});
```

- `projects` replaces the `vitest.workspace.ts` file (removed in 4): one
  root config with a `projects` array, which runs no tests itself. Since 5
  an inline project inherits the root config (arrays like `setupFiles` are
  appended); `extends: false` opts out. Config-file projects inherit nothing.
- `setupFiles` run in the worker per test file (a matcher registration);
  `globalSetup` runs once in the main thread, no test globals, data via
  `provide()` / `inject()` (a database container).
- `pool` is `forks` by default: full process API, native modules safe.
  `threads` spawns faster, cannot `process.chdir`, can segfault on native
  modules. The `vm*` pools ignore `isolate`.
- `isolate: false` shares module state between files in one worker, so a
  module-level singleton leaks. Per project only, never where state lives.
- `vite-tsconfig-paths` reads `paths` from tsconfig; a hand-written
  `resolve.alias` drifts. Vitest's common-errors page still names the
  plugin; Vite 8's `resolve.tsconfigPaths` was reported as not honoured in
  tests (unverified), so the plugin stays.

## 3. Mocking: what hoists, what cannot be spied

`vi.mock("./mod.js", factory)` is hoisted above every import and the
static imports become dynamic, so it runs first whatever the source order.
Values a factory needs come from `vi.hoisted(() => ...)`; since 5, both
must sit at file top level or they throw.

```ts
const { sendMock } = vi.hoisted(() => ({ sendMock: vi.fn() }));
vi.mock("./mailer.js", () => ({ send: sendMock }));
import { register } from "./register.js";   // sees the mocked mailer
```

- ESM named export: `import * as mod from "./mod.js"` then
  `vi.spyOn(mod, "fn")`. Browser mode (native ESM) needs
  `vi.mock("./mod.js", { spy: true })` instead.
- A function calling a sibling in its own module bypasses any external mock
  of that sibling: inject the dependency or split the module.
- `vi.spyOn` sees calls made after it was installed; a call at module top
  level is invisible.
- `mockClear` drops history; `mockReset` also drops the implementation;
  `mockRestore` also puts the original back for spies. `clearMocks` is on by
  default since 5 and can strip history from a mock a concurrent test holds.
- Fake timers: `vi.useFakeTimers()` in `beforeEach`, `vi.useRealTimers()` in
  `afterEach`, always. Since 5 they fake `Temporal` too (`toNotFake` opts
  out). `advanceTimersByTime(ms)` runs what is due; `runAllTimersAsync()`
  drains async callbacks too (10,000-iteration cap); `vi.setSystemTime`
  moves the clock without firing; `vi.waitFor` advances them while polling.

## 4. Fixtures, tables, types

```ts
import { test as base } from "vitest";
// builder form (4.1+): types inferred; the object form with `use` still works
export const test = base.extend("db", { scope: "file" }, async ({}, { onCleanup }) => {
  const db = await openTestDb();
  onCleanup(() => db.close());
  return db;
});
test("saves an order", async ({ db, expect }) => { /* ... */ });
```

- Fixtures are per test by default; `scope: "file"` or `"worker"` share
  them; `auto: true` runs one no test asks for. A fixture may depend only
  on fixtures of its own or a longer scope.
- `test.each(table)` for parameter tables; `test.for` for the context object.
- `test.concurrent` tests take `expect` from the context argument, never the
  module import. `test.sequential` is gone in 5: `{ concurrent: false }`.
- Type tests live in `*.test-d.ts`, are checked by `tsc` (never executed)
  under `--typecheck`, with `expectTypeOf(...)` for detail or
  `assertType<T>(x)` plus `@ts-expect-error` for the negative case.

## 5. Running in CI

```
vitest run                                                 # one pass, annotations
vitest run --shard=1/4 --reporter=blob                     # per runner
vitest run --merge-reports --reporter=junit                # after the shards
vitest run --changed origin/main                           # what a PR touched
```

- The `github-actions` reporter joins on GitHub by itself; a `reporters` key
  replaces the default list. Bare `vitest` is watch mode, which `--shard`
  refuses. `retry` in config is a measurement tool, not a fix.
- Coverage: `v8` by default, no instrumentation; `istanbul` for Firefox or
  Bun. `test.exclude` does not affect coverage; `coverage.exclude` does.
- Default timeout is 5 s (15 s in browser mode): a test that needs more has
  a slow dependency to mock.

## 6. Flakiness, in the order to check

1. Fake timers left on by a test that failed before `afterEach`.
2. An unawaited `expect(p).resolves` or `rejects`; 5 fails these outright.
3. `isolate: false` with module-level state; `clearMocks` with
   `test.concurrent` sharing a mock.
4. A `vi.mock` factory reading a variable declared after it (`vi.hoisted`).

## Sources

vitest.dev guide (getting started, mocking, test context, testing types,
coverage, CLI, reporters), the `docs/config/` pages in the vitest-dev/vitest
repository, the migration guides for 4 and 5, the release posts for 4.0,
4.1 and 5.0, and the npm registry (5.0.5); fetched 2026-10-29. Left out as
unconfirmed: the exact `coverage.thresholds` shape, `--bail` count
semantics, `esbuild` target and decorator interplay. Quotes: `references/`.
