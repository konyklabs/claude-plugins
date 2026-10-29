---
name: test-clover-modifier-webhook
tags: [vendorfake, testing]
runs: 1
max_turns: 12
timeout_seconds: 300
allowed_tools: [Read, Glob, Grep, Skill, Write]
---
vendorfake is installed in this pytest suite. Our service keeps a Clover item-availability cache and updates it from Clover's modifier webhooks; the handler is `app.webhooks.handle_clover_event(headers, body)` and it must reject a bad X-Clover-Auth. Write the test that proves a modifier update on the fake reaches the handler with a valid auth header, and say what makes this test flaky if it is written wrong.
