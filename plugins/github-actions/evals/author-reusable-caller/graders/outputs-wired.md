---
type: regex
target: last_message
pattern: "needs\\.[\\w-]+\\.outputs\\.deployed_url"
---
The follow-up job must read the reusable workflow's output through
`needs.<job>.outputs.deployed_url` — the standard job-to-job outputs
pattern the call sits on top of — not restate the URL literally or
invent another way to surface it.
