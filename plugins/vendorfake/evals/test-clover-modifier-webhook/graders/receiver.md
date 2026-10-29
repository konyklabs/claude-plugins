---
type: regex
target: { source: file, path: tests/test_clover_modifier_webhook.py }
pattern: "webhook_receiver\\(|vendorfake_webhook_receiver\\b"
---
The test file receives the delivery on a real loopback endpoint (a
`webhook_receiver()` call or the `vendorfake_webhook_receiver` fixture), not a
mock. It reads the written file, not the trace, which carries the skill's text.
