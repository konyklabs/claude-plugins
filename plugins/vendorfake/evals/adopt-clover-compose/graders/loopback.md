---
type: regex
target: { source: file, path: compose.yaml }
pattern: "127\\.0\\.0\\.1:[^\\n]*:8080"
---
The fake's published port is bound to loopback: the control plane is
unauthenticated and will POST webhooks at any URL it is given. The pattern
looks for a loopback mapping onto the fake's container port 8080, because the
scaffold's own app port (8000) is already on loopback and a bare
`127.0.0.1:` would pass a run that publishes the fake on every interface.
