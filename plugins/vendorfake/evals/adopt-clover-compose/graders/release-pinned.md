---
type: regex
target: { source: file, path: compose.yaml }
pattern: "VENDORFAKE_REF[^\\n]*v[0-9]+\\.[0-9]+\\.[0-9]+"
---
compose.yaml pins the fake's image to a release tag through the
`VENDORFAKE_REF` build arg (installed from the release archive), the one place
to bump, rather than building from the repository URL, which tracks main. It
reads the written compose file, not the trace, because the loaded skill's own
text carries `VENDORFAKE_REF` and a trace grader would pass whenever it fired.
