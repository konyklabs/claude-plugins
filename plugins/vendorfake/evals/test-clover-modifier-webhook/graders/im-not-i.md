---
type: regex
target: trace
pattern: "IM:"
---
The test subscribes to Clover's modifier event family (`IM:`), not `I:*`, which
does not match modifier events.
