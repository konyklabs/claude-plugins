---
tags: [vendorfake, adopting]
runs: 1
max_turns: 20
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---
This service integrates Clover. Add vendorfake to compose.yaml as the Clover fake for our e2e tier: pinned to a release so the workplace machine needs no git access, healthy before the app starts, its port only on loopback, with our own OAuth callback accepted, and the profile right for CI. Fill in the app's Clover settings so it talks to the fake. Then tell me the first e2e test to write and how I prove it can fail.
