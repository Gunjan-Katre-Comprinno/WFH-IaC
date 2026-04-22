variable "project" { type = string }
variable "environment" { type = string }
variable "tags" { type = map(string) }
variable "cloudfront_distribution_arn" { type = string }

data "aws_caller_identity" "current" {}
