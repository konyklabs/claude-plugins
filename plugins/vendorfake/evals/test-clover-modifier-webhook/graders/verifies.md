---
type: regex
target: { source: file, path: tests/test_clover_modifier_webhook.py }
pattern: "verify_clover_auth\\("
---
The test file calls vendorfake's own `verify_clover_auth(...)` on the delivery's
X-Clover-Auth header rather than comparing a string it made up. A call, not a
bare import, and read from the written file, not the trace, which carries the
skill's text.
