# Networking

Contents: security groups by rule · the load balancer chain · target
groups for Fargate · endpoints versus NAT · data sources · sources.

## Security groups by rule

The `aws_security_group` page says to avoid its inline `ingress` and
`egress` blocks. Use one resource per rule, reference security groups
rather than CIDRs wherever the peer is an AWS resource, and never mix the
inline and resource styles on one group (perpetual diffs).

```hcl
resource "aws_security_group" "alb" {
  name        = "app-alb-${var.environment}"
  description = "Application load balancer"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from the internet"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_security_group" "service" {
  name        = "app-service-${var.environment}"
  description = "Fargate tasks"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "service_from_alb" {
  security_group_id            = aws_security_group.service.id
  description                  = "Container port from the load balancer only"
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_egress_rule" "service_all" {
  security_group_id = aws_security_group.service.id
  description       = "Outbound to endpoints and NAT"
  ip_protocol       = "-1"              # all protocols; omit ports
  cidr_ipv4         = "0.0.0.0/0"
}
```

The provider removes AWS's default allow-all egress when it manages a
group, so declare egress explicitly. Exactly one of `cidr_ipv4`,
`cidr_ipv6`, `prefix_list_id`, `referenced_security_group_id` per rule.
A rule for port 22 on a Fargate task group is a rule for nothing that
exists; the review treats it as a future exposure.

## The load balancer chain

Trace it in this order when writing and when reviewing:

1. **Placement.** Internet-facing load balancer in public subnets (a
   route to an internet gateway). `internal = true` in private subnets.
   An internet-facing ALB in private subnets applies and answers nothing.
2. **Listener.** Port and protocol; HTTPS with an ACM certificate and an
   HTTP listener that redirects. The load balancer's security group admits
   the listener port.
3. **Target group.** `target_type = "ip"` for Fargate, port equals the
   container port, health check path the application actually serves,
   `deregistration_delay` shorter than the default 300 s for fast rollouts.
4. **Service.** Security group admits the container port from the load
   balancer's group by reference; `health_check_grace_period_seconds`
   covers start-up.

```hcl
resource "aws_lb" "app" {
  name               = "app-${var.environment}"
  load_balancer_type = "application"
  internal           = false
  subnets            = var.public_subnet_ids
  security_groups    = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "app" {
  name                 = "app-${var.environment}"
  port                 = 8080
  protocol             = "HTTP"
  vpc_id               = var.vpc_id
  target_type          = "ip"
  deregistration_delay = 30
  health_check {
    path                = "/healthz"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }
}
```

## Endpoints versus NAT

A private subnet with no NAT route needs interface endpoints for every
AWS API the workload calls, each with its own security group admitting
443 from the subnets, plus the S3 gateway endpoint (free) for image layers
and S3 access. For a Fargate service that pulls from ECR, logs, and reads
secrets: `ecr.api`, `ecr.dkr`, `s3` (gateway), `logs`, `ssm` or
`secretsmanager`. Interface endpoints cost per hour per AZ; a NAT gateway
costs per hour plus per GB. The docs fetched give no cost comparison;
decide on data volume and the number of APIs, and record it.

```hcl
resource "aws_vpc_endpoint" "ecr_dkr" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.ecr.dkr"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true             # required for ECR
}
```

## Data sources instead of literals

`data.aws_availability_zones.available` (`state = "available"`) for AZ
names; `data.aws_region.current.region` for the region string; VPC and
subnet ids as typed variables or `data.aws_vpc` / `data.aws_subnets` with
tag filters, never pasted ids. A `count` over a list of subnets recreates
resources when the list reorders; use `for_each` over a map keyed by AZ.

## Sources

hashicorp/terraform-provider-aws docs `r/security_group` (inline rules
discouraged, default egress removed), `r/vpc_security_group_ingress_rule`,
`r/lb`, `r/lb_target_group` (`target_type`, health check ranges,
`deregistration_delay`), `r/vpc_endpoint`, `d/availability_zones`,
`d/region`; AWS ECS developer guide: load balancer target type for
`awsvpc`, VPC endpoints for Fargate; AWS ECR VPC endpoints page. Fetched
2026-09-29 (first 2026-09-08).
