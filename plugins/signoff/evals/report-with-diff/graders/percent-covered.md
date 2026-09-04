---
type: regex
target: last_message
pattern: "percent|%"
---
`report.py`'s summary line states coverage as a whole percent (`plugins/signoff/formats.md`); the answer reports back that figure, not just a raw tile count.
