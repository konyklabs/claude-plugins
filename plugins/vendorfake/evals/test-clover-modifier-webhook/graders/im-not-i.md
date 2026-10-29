---
type: regex
target: { source: file, path: tests/test_clover_modifier_webhook.py }
pattern: "\\[\\s*[\"']IM:"
---
The test file subscribes with Clover's modifier event family (an `["IM:..."]`
glob list), not `I:*`, which does not match modifier events. It reads the file
the model wrote, not the trace, because the loaded skill's own text carries
`IM:` and a trace grader would pass whenever the skill fired.
