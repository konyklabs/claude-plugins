---
name: on
description: Arms the plugin for this session in enforce mode without a brief; use when asked to turn it on, arm it, or enable it.
disable-model-invocation: true
allowed-tools: Bash(python3 *)
---
<!-- supervisor:arm enforce -->

# On

The hook armed the session the instant this prompt arrived; nothing below
does the arming, it only shows what happened. Run this and print its
session line:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" mode show
```

From here, the policy text already placed in context applies: workers are
pinned, forks are denied, spend is tracked, and the budget gate is live.
