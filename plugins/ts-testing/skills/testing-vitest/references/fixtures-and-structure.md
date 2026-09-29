# Vitest fixtures, tables, types, and CLI reference

Fetched 2026-09-29 from the vitest.dev test-context and testing-types
guides, the `test`/`expect` API pages in the vitest-dev/vitest repository,
the 4.1 release post, and the CLI guide.

## Contents

1. test.extend fixtures and scope
2. test.each and test.for
3. test.concurrent and the context expect
4. expect.soft and expect.assertions
5. Type testing with expectTypeOf and assertType
6. Typecheck mode: what actually runs
7. Path aliases in tests
8. CLI for CI: run, shard, merge, changed

## 1. test.extend fixtures and scope

```ts
import { test as base } from "vitest";
export const test = base.extend<{ db: Db }>({
  db: [
    async ({}, use) => {
      const db = await openTestDb();
      await use(db);
      await db.close();
    },
    { scope: "file" },
  ],
});
test("saves an order", async ({ db, expect }) => {
  /* ... */
});
```

A fixture is per-test by default; `{ auto: true }` makes it run for every
test whether or not a test asks for it; `scope` can widen it to `"file"` or
`"worker"`. A fixture may only depend on other fixtures of its own scope or
a longer one — https://vitest.dev/guide/test-context.html. v4.1 added
`onCleanup()` for fixture teardown and gave hooks access to the fixture
context — https://vitest.dev/blog/vitest-4-1.html.

## 2. test.each and test.for

`test.each([...])('name %i', fn)` supports printf-style placeholders `%s
%d %i %f %j %o`, object tables, and template-literal tables. `test.for`
exposes the `TestContext` to the callback and, unlike `test.each`, does not
spread an array's elements as separate arguments —
https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/api/test.md.

## 3. test.concurrent and the context expect

`test.concurrent` tests must use the `expect` handed to them on the test
context, not the module-level `expect` import — otherwise assertions and
snapshots can land on the wrong test. Synchronous concurrent tests still
run sequentially regardless. `test.sequential`/`describe.sequential` were
removed in v5; use `{ concurrent: false }` instead — test.md.

## 4. expect.soft and expect.assertions

`expect.soft` keeps the test running after a failed assertion and reports
every failure at the end, but only works inside `test`; a hard `expect`
failure after it still halts immediately. `expect.assertions(n)` /
`expect.hasAssertions()` guard callback-based code that could return early
without asserting. `toMatchInlineSnapshot` writes its snapshot directly
into the test file; update it with `-u`, or `u` in watch mode —
https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/api/expect.md.

## 5. Type testing with expectTypeOf and assertType

`expectTypeOf(x).toBeFunction()` and
`expectTypeOf(x).parameter(0).toExtend<T>()` give detailed failure
messages; `assertType<T>(x)` paired with `@ts-expect-error` gives a
simpler pass/fail for the negative case —
https://vitest.dev/guide/testing-types.html.

## 6. Typecheck mode: what actually runs

Files matching `typecheck.include` (see `config.md`) are analysed by `tsc`
or `vue-tsc --noEmit` — they are never executed as JavaScript. A
dynamically constructed test name is displayed literally rather than
evaluated. Since Vitest 2.1, a file matched by both the ordinary `include`
and `typecheck.include` is reported separately for each. Enable typecheck
mode with `vitest --typecheck` on the CLI or `typecheck.enabled` in config
— https://vitest.dev/guide/testing-types.html,
https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/typecheck.md.

## 7. Path aliases in tests

`vite-tsconfig-paths` is the recommended plugin so a tsconfig `paths` map
is honoured inside tests; a hand-written `resolve.alias` has to be kept in
sync by hand when the plugin is not used. Vite 8's built-in
`resolve.tsconfigPaths: true` is not picked up by Vitest as of this fetch —
open issue, unverified — https://github.com/vitest-dev/vitest/issues/10054.

## 8. CLI for CI: run, shard, merge, changed

The bare `vitest` command is watch mode; CI runs `vitest run` for a single
pass — https://vitest.dev/guide/cli.html.

```bash
vitest run --reporter=default --reporter=github-actions   # one pass, PR annotations
vitest run --shard=1/4 --reporter=blob                     # one job per shard
vitest run --merge-reports --reporter=junit                 # after every shard finishes
vitest run --changed origin/main                             # only what a PR touched
```

- `--changed` runs the tests covering changed source files, walked through
  the import graph — cli.html.
- `--shard=<index>/<count>` refuses to run under watch mode.
  `--merge-reports[=dir]` defaults to `.vitest/blob/` and works with any
  reporter except `blob` itself —
  https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/guide/cli.md.
- `--reporter=github-actions` emits PR annotations and can be repeated
  alongside another reporter — cli.html.
- `--bail` stops the run after failures; the exact count semantics were not
  confirmed this fetch (unverified).
- `--coverage.include`, `--ui`, `--typecheck` are also CLI flags, not only
  config keys — cli.html.
