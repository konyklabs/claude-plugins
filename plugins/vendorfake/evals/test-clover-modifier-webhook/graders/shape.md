---
type: llm
focus: last_message
---
Score 1 if the test uses the in-process `unit("clover")` (or the `vendorfake` marker fixtures) rather than a container; subscribes with a signature key; triggers the modifier change through the fake (the modifier update route `POST /v3/merchants/{mId}/modifier_groups/{modGroupId}/modifiers/{modId}`, or `POST /__unit/state/update`); calls `drain()` before asserting; verifies the header with `verify_clover_auth`; and names at least one flakiness cause among: subscribing before a reset, `I:*` not matching `IM:`, a shared served unit without `reset()`. Score 0 if the test mocks the webhook POST instead of receiving one.
