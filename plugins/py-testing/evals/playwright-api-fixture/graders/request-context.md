---
type: regex
target: last_message
pattern: "playwright\\.request\\.new_context\\([\\s\\S]*dispose\\(\\)"
---
The answer builds a session-scoped APIRequestContext via playwright.request.new_context(...) and disposes it.
