---
type: regex
target: { source: file, path: compose.yaml }
pattern: "no-faults"
---
The CI profile is no-faults: `full` carries chaos and `chaos-demo` preloads
faults that read as random red runs.
