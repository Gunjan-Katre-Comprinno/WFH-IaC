variable "project" { type = string }
variable "environment" { type = string }
variable "tags" { type = map(string) }
variable "domain_name" { type = string }
variable "acm_certificate_arn" { type = string }
variable "frontend_bucket_id" { type = string }
variable "frontend_bucket_regional_domain" { type = string }

variable "waf_web_acl_arn" {
  description = "WAF Web ACL ARN (us-east-1) to attach to CloudFront. Leave empty to skip."
  type        = string
  default     = ""
}
