---
name: fake-timers-cleanup
tags: [vitest,timers,ts-testing]
runs: 1
max_turns: 8
timeout_seconds: 240
---
Write a Vitest test for a debounce(fn, 300) utility: calling the debounced function three times within 100ms should invoke fn only once, after the 300ms delay.
