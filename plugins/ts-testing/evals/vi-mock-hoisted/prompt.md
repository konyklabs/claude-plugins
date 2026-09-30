---
name: vi-mock-hoisted
tags: [vitest,mocking,ts-testing]
runs: 1
max_turns: 8
timeout_seconds: 240
---
This test file's vi.mock factory for ./mailer.js needs to return a mock function that a later assertion in the same test also needs to reference. Show how to set that up.
