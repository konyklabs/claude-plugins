---
type: regex
target: last_message
pattern: "target_type\\s*=\\s*\"ip\"[\\s\\S]*network_mode\\s*=\\s*\"awsvpc\"|network_mode\\s*=\\s*\"awsvpc\"[\\s\\S]*target_type\\s*=\\s*\"ip\""
---
The Fargate shape from references/ecs-fargate.md: awsvpc network mode and an ip target group. Both must be present; either order.
