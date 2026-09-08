---
type: regex
target: last_message
pattern: "^(?![\\s\\S]*(WAF|X-Ray|cross-region replication))"
---
Trivy and checkov noise (WAF, X-Ray, replication) is excluded by the skill. The findings must not contain it.
