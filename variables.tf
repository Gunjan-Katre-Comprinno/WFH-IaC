variable "aws_region" {
  description = "Primary AWS region"
  type        = string
  default     = "ap-south-1"
}

variable "project" {
  description = "Project name used in resource naming and tagging"
  type        = string
  default     = "wfh-management"
}

variable "environment" {
  description = "Deployment environment (prod, staging, dev)"
  type        = string
  default     = "prod"
}

variable "domain_name" {
  description = "Custom domain for CloudFront (e.g. wfh.comprinno.net)"
  type        = string
}

variable "owner" {
  description = "Owner tag value"
  type        = string
  default     = "akash.satpute@comprinno.net"
}

variable "ses_sender" {
  description = "Verified SES sender email address (e.g. noreply@yourdomain.com)"
  type        = string
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN in us-east-1 for CloudFront HTTPS"
  type        = string
}
