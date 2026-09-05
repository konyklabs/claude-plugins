---
name: "on"
description: Arms the plugin for this session in enforce mode without a brief; use when asked to turn it on, arm it, or enable it.
disable-model-invocation: true
---
<!-- supervisor:arm enforce -->

# On

The hook armed the session the instant this prompt arrived; nothing here does
the arming. Reply with the one line the hook added to this turn's context (it
starts with `supervisor armed` or `supervisor:`), and stop. Do not run the
script to confirm: from a skill it cannot know which session it is in.

From here, the policy text placed in context applies: workers are pinned,
forks are denied, spend is tracked, and the budget gate is live.
