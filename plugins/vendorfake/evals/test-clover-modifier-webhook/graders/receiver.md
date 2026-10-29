---
type: regex
target: trace
pattern: "webhook_receiver"
---
The delivery is received by a real loopback endpoint (`webhook_receiver()` or the
`vendorfake_webhook_receiver` fixture), not mocked.
