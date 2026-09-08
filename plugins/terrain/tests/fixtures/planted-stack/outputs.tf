output "alb_dns" {
  value = aws_lb.app.dns_name
}

output "db_password" {
  value = var.db_password
}
