---
type: regex
target: last_message
pattern: "(?i)\"module\"\\s*:\\s*\"nodenext\""
---
The answer sets module to nodenext, uses .js extensions on relative imports (or rewriteRelativeImportExtensions), enables erasableSyntaxOnly, does not set baseUrl, and names types explicitly rather than relying on the pre-6.0 implicit default.
