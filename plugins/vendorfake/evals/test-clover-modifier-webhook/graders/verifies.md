---
type: regex
target: trace
pattern: "verify_clover_auth"
---
The test checks the X-Clover-Auth header with vendorfake's own helper rather
than comparing a string it made up.
