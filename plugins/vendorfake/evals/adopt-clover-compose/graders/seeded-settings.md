---
type: regex
target: { source: file, path: compose.yaml }
pattern: "UNITCLOVERAPP"
---
The app's Clover settings carry the fake's seeded app id, so the authorize and
token calls name an app the fake knows.
