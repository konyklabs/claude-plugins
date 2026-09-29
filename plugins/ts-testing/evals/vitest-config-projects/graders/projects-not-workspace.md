---
type: regex
target: last_message
pattern: "projects\\s*:\\s*\\["
---
The answer configures the two packages with the root config's `projects` array, not the removed `workspace` key or a `vitest.workspace.ts` file, and CI runs `vitest run`.
