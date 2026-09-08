---
name: review-startup-chain
tags: [terrain,review]
runs: 1
max_turns: 10
timeout_seconds: 300
---
Review the module in tests/fixtures/planted-stack of the terrain plugin as if it were a PR adding the stack. The PR says: "Service and worker for the app; the deploy pipeline pushes new images and updates the service after CI." Return findings as JSON with a failure scenario each.
