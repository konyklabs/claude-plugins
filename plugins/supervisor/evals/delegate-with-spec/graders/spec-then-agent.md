---
type: llm
target: trace
---
Score 1 if, before spawning any agent, the assistant writes a spec that names the file to change, a checkable definition of done, and the exact test command, then spawns supervisor:implementer (or a Sonnet-pinned agent) without passing model: fable, and afterwards reads the returned Result and Evidence sections rather than re-implementing. Score 0 if it implements the fixture itself or spawns an agent with only a one-line prompt.
