---
name: "off"
description: Disarms the plugin for this session; use when asked to turn it off, stand down, or stop enforcing.
disable-model-invocation: true
---

# Off

The hook disarmed the session the instant this typed command arrived; nothing
here does the disarming, and a quoted marker never does. Reply with the one
line the hook added to this turn's context (it starts with `supervisor`),
and stop. Do not run the script to confirm: from a skill it cannot know which
session it is in.

Spend is still tracked. `/supervisor:on` or `/supervisor:start <task>` arms
it again.
