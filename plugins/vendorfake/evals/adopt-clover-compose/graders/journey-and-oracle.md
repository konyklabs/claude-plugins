---
type: llm
focus: last_message
---
Score 1 if the answer names a first e2e test that drives the real authorize → callback → stored token → merchant read path through the service and the fake (not a mocked one); names two ways to make it fail (the fake unreachable, for example stopped; a code the vendor never issued); and names `GET /__unit/requests` (the fake's request log) as the way to see what the service actually called. Score 0 if the test only checks `/__unit/health`, or if it proposes stubbing the vendor inside the app.
