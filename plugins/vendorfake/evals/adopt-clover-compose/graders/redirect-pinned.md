---
type: regex
target: { source: file, path: compose.yaml }
pattern: "VENDORFAKE_VENDOR_REDIRECT_URI"
---
The fake is told the consumer's own callback; Clover's authorize answers 400 to
any redirect_uri other than the configured one, so without this the first
journey cannot pass.
