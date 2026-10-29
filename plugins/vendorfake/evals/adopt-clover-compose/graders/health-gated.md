---
type: regex
target: { source: file, path: compose.yaml }
pattern: "__unit/health"
---
The fake's service carries a health check on /__unit/health (200 only once the
seed is hydrated), which is what `condition: service_healthy` waits on.
