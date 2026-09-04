---
name: exploring-app
description: Validates an application's explored map (.qa/map.json — its screens, roles, actions, forms, states and flows) against the signoff id and file formats with the explore step's mapcheck.py script; use after an app has been explored, by hand or by an agent, and before mining rules or tiling coverage.
---

# Exploring the app

The map's field-by-field format is `plugins/signoff/formats.md`, the only home of the id and file formats.
Run `python3 "${CLAUDE_PLUGIN_ROOT}/skills/exploring-app/scripts/mapcheck.py" --help` for the current options.
The full walkthrough comes in a later slice; this skill names the shape mapcheck.py validates.
