variable "environment" {}

variable "image_tag" {
  default = "latest"
}

variable "db_password" {
  description = "Database password"
  default     = "changeme123"
}

variable "private_subnet_ids" {
  description = "Private subnets for the service"
  type        = list(string)
}

variable "vpc_id" {
  type = string
}

variable "desired_count" {
  type    = number
  default = 2
}
