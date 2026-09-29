---
type: regex
target: last_message
pattern: "(?m)^(?:const|let|var)\\s[^\\n]*=\\s*(?:await\\s+)?vi\\.hoisted\\("
---
The answer declares the shared mock function with vi.hoisted so the factory can reference it, with vi.hoisted and vi.mock both at file top level (v5 throws if either is nested).
