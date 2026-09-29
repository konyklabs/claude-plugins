# Vitest mocking reference

Fetched 2026-09-29 from the vitest.dev mocking guide, the modules and `vi`
API pages in the vitest-dev/vitest repository, and the v4/v5 migration
guides.

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
mock is not taking effect.

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
level; nesting one inside a function or a conditional now throws instead
of silently doing nothing — modules.md.

## 2. Spying on ESM named exports

`import * as mod from "./mod.js"` then `vi.spyOn(mod, "fn")` is the
pattern for a named export. It does not work in Browser Mode, because
native ESM module namespaces cannot be reassigned there; the replacement is
`vi.mock(path, { spy: true })` — modules.md.

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
  once-implementations queued with `mockImplementationOnce`.
- `mockRestore()` — for a `vi.spyOn` spy, additionally restores the
  original property descriptor; for a plain `vi.fn()` it behaves like
  `mockReset()` — mock.md.

`clearMocks` is `true` by default as of v5 (see `config.md`), which clears
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
  callbacks, with a 10,000-iteration cap to catch a timer that keeps
  rescheduling itself.
- `vi.setSystemTime()` moves the clock (`Date.now`, `performance.now`,
  `process.hrtime`) without firing any timers; without fake timers active
  it only stubs `Date` and `Temporal.Now`.
- `vi.waitFor(cb, { timeout, interval })` polls until `cb` stops throwing,
  and auto-advances fake timers while it polls — vi.md.

Always pair `vi.useFakeTimers()` in `beforeEach` with `vi.useRealTimers()`
in `afterEach`: nothing resets fake timers automatically between tests.

## 7. stubEnv and stubGlobal

`vi.stubEnv` sets `process.env` and `import.meta.env`; `vi.stubGlobal` sets
`globalThis` (and, under jsdom/happy-dom, `window`/`top`/`self`/`parent`
too). Neither resets on its own between tests — a stub persists until
`vi.unstubAllEnvs()` / `vi.unstubAllGlobals()` is called, or the config's
`unstubEnvs` / `unstubGlobals` option is set — vi.md.
