---
name: playwright-auth-once
tags: [playwright,browser,py-testing]
runs: 1
max_turns: 8
timeout_seconds: 240
---
Our Playwright browser tests log in through the UI at the start of every test and the suite takes 40 minutes. Fix the approach.
