---
type: regex
target: last_message
pattern: "useFakeTimers\\(\\)[\\s\\S]*useRealTimers\\(\\)"
---
The answer installs fake timers in beforeEach and restores real ones in afterEach, and advances time with advanceTimersByTime or runAllTimersAsync rather than a real sleep or a longer test timeout.
