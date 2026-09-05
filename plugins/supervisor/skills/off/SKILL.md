---
name: off
description: Disarms the plugin for this session; use when asked to turn it off, stand down, or stop enforcing.
disable-model-invocation: true
allowed-tools: Bash(python3 *)
---
<!-- supervisor:disarm -->

# Off

The hook disarmed the session the instant this prompt arrived; nothing below
does the disarming, it only shows what happened. Run this and print its
session line:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/supervisor.py" mode show
```

From here nothing is pinned, denied or injected for the rest of this
session; spend is still tracked in the ledger.
