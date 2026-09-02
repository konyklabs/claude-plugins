---
name: dos-timeouts
tags: [dos,prod-readiness]
runs: 1
max_turns: 8
timeout_seconds: 240
---
Our FastAPI backend calls the upstream gateway with httpx. What could take the service down under load?
