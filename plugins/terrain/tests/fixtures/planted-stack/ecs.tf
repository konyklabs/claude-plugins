resource "aws_ecs_cluster" "app" {
  name = "app-${var.environment}"
}

resource "aws_ecs_task_definition" "app" {
  family                   = "app-${var.environment}"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "4096"
  execution_role_arn       = aws_iam_role.ecs.arn
  task_role_arn            = aws_iam_role.ecs.arn

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = "123456789012.dkr.ecr.us-east-1.amazonaws.com/app:${var.image_tag}"
      essential = true
      portMappings = [{ containerPort = 8080 }]
      environment = [
        { name = "ENV", value = var.environment },
        { name = "DB_PASSWORD", value = var.db_password }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = "/ecs/app-${var.environment}"
          "awslogs-region"        = "us-east-1"
          "awslogs-stream-prefix" = "app"
        }
      }
    }
  ])
}

# The deploy pipeline updates the service with each new image after CI.
resource "aws_ecs_service" "app" {
  name            = "app-${var.environment}"
  cluster         = aws_ecs_cluster.app.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.service.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "app"
    container_port   = 8080
  }

  depends_on = [aws_lb_listener.http]
}
