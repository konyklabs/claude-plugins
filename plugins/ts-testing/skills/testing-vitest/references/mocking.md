# Vitest mocking reference

Fetched 2026-10-29 from the vitest.dev mocking guide, the modules and `vi`
API pages in the vitest-dev/vitest repository, and the v4/v5 migration
guides (docs at 5.0.x; the main branch is 5.1.0-beta.1).

## Contents

1. Hoisting: what runs before what
2. Spying on ESM named exports
3. Two documented limits
4. Manual mocks
5. Mock lifecycle: clear, reset, restore
6. Fake timers
7. stubEnv and stubGlobal

## 1. Hoisting: what runs before what

`vi.mock("./mod.js", factory)` is hoisted to the top of the file, above
every import, and Vitest transforms every static import in that file into
a dynamic one so the mock is in place before anything loads —
https://vitest.dev/guide/mocking.html,
https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/guide/mocking/modules.md.
Writing `vi.mock` after the import it targets still works, because of this
hoist, but it reads backwards and is the first thing to distrust when a
mock is not taking effect. The hoist is a transform, not a runtime
feature: with Vite's module runner disabled (`experimental.viteModuleRunner`)
`vi.mock` and `vi.hoisted` are not hoisted at all, and modules a setup file
already imported are cached before any test file's `vi.mock` runs. A factory
that must read file-scope variables can use `vi.doMock` instead, which is
not hoisted and only affects later dynamic imports — vi.md.

A factory that needs a value declared elsewhere in the file cannot just
reference it — hoisting runs before that declaration exists. `vi.hoisted`
solves this: it runs side-effecting code before imports load and returns a
value the factory can use, synchronously or asynchronously —
https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/api/vi.md.

```ts
const { sendMock } = vi.hoisted(() => ({ sendMock: vi.fn() }));
vi.mock("./mailer.js", () => ({ send: sendMock }));
import { register } from "./register.js"; // sees the mocked mailer
```

Since v5, `vi.mock`, `vi.unmock` and `vi.hoisted` must sit at file top
level; nesting one inside a function, block or `describe`/`test` callback now
throws (v4 only warned) — the v5 migration guide,
https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/guide/migration/index.md.
`vi.doMock` and `vi.doUnmock` are exempt.

## 2. Spying on ESM named exports

`import * as mod from "./mod.js"` then `vi.spyOn(mod, "fn")` is the
pattern for a named export. It does not work in Browser Mode, because
native ESM module namespaces cannot be reassigned there; the replacement is
`vi.mock(path, { spy: true })` — modules.md. The same applies when the
module runner is disabled and Vitest falls back to the Node loader hook.
`spy: true` patches objects and class prototypes in place rather than
copying them, so mocking a Node built-in or externalized dependency this way
makes every user of it in the worker see the spies. A browser automock with
no factory returns `undefined` from every export as of v5; add `spy: true`
to keep the real implementation.

## 3. Two documented limits

- A function that calls a sibling function defined in the *same* module
  bypasses any external mock of that sibling — this is a fact about how
  modules work, not a Vitest gap, so the fix is to inject the dependency or
  split the module — modules.md.
- `vi.spyOn` only records calls made *after* it is installed; a call that
  already happened at module top level, before the spy was set up, is
  invisible to it — modules.md.

## 4. Manual mocks

A file at `./__mocks__/example.js` next to the module it mocks is loaded
automatically in place of the real module, instead of Vitest's own
auto-mock — modules.md.

## 5. Mock lifecycle: clear, reset, restore

`vi.fn()` returns a `Mock<T>`; `mockReturnValue`/`mockResolvedValue` are
typed to the wrapped function's signature —
https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/api/mock.md.

- `mockClear()` — drops call history only.
- `mockReset()` — also drops the implementation, including any
  once-implementations queued with `mockImplementationOnce`: a `vi.fn()`
  goes back to returning `undefined`, a `vi.fn(impl)` back to `impl`.
- `mockRestore()` — for a `vi.spyOn` spy, additionally restores the
  original property descriptor; for a plain `vi.fn()` it behaves like
  `mockReset()` — mock.md.
- `vi.restoreAllMocks()` restores `vi.spyOn` spies only: it neither clears
  history nor resets implementations, and it skips automocked exports —
  vi.md.

Since v5, a class mock's `prototype` chains to the implementation class
(`vi.fn(Dog)`, `vi.spyOn(obj, "Dog")`), so instances keep their prototype
methods and pass `instanceof Dog`; `mockReset()` reverts that chain with the
implementation — the v5 migration guide, mock.md.

`clearMocks` is `true` by default as of v5, which clears
call history between tests automatically. The documented warning: this can
break a suite that runs async tests concurrently, because one test
finishing clears history a still-running concurrent test needs to assert
against — https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/clearmocks.md.

## 6. Fake timers

`vi.useFakeTimers({ toFake, toNotFake, shouldAdvanceTime })` — `toFake` and
`toNotFake` are mutually exclusive ways to choose which timer APIs are
faked — vi.md.

- `vi.advanceTimersByTime(ms)` runs every timer due within that window.
- `vi.runAllTimersAsync()` drains all pending timers, including async
  callbacks, with a 10,000-iteration cap (`fakeTimers.loopLimit`) to catch a
  timer that keeps rescheduling itself.
- `vi.setSystemTime()` moves the clock (`Date.now`, `performance.now`,
  `process.hrtime`) without firing any timers; without fake timers active
  it only stubs `Date` and `Temporal.Now`. Since v5 `Temporal` is in the
  default faked set, so `Temporal.Now` follows `useFakeTimers` too; keep it
  native with `toNotFake: ["Temporal"]` — the v5 migration guide.
- `vi.waitFor(cb, { timeout, interval })` polls until `cb` stops throwing,
  and auto-advances fake timers while it polls — vi.md.

Always pair `vi.useFakeTimers()` in `beforeEach` with `vi.useRealTimers()`
in `afterEach`: nothing resets fake timers automatically between tests.

## 7. stubEnv and stubGlobal

`vi.stubEnv` sets `process.env` and `import.meta.env`; `vi.stubGlobal` sets
`globalThis` (and, under jsdom/happy-dom, `window`/`top`/`self`/`parent`
too). Neither resets on its own between tests — a stub persists until
`vi.unstubAllEnvs()` / `vi.unstubAllGlobals()` is called, or the config's
`unstubEnvs` / `unstubGlobals` option is set — vi.md. Like `clearMocks`,
those options restore globally between tests and can disturb concurrent
tests still running —
https://raw.githubusercontent.com/vitest-dev/vitest/main/docs/config/unstubenvs.md.
