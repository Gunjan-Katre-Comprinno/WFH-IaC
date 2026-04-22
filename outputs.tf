output "api_gateway_url" {
  description = "API Gateway invoke URL (prod stage)"
  value       = module.api_gateway.invoke_url
}

output "cloudfront_domain" {
  description = "CloudFront distribution domain name"
  value       = module.cloudfront.domain_name
}

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID"
  value       = module.cognito.user_pool_id
}

output "cognito_client_id" {
  description = "Cognito App Client ID"
  value       = module.cognito.client_id
}

output "frontend_bucket" {
  description = "S3 frontend bucket name"
  value       = module.s3.frontend_bucket_id
}

output "backup_bucket" {
  description = "S3 DynamoDB backup bucket name"
  value       = module.s3.backup_bucket_id
}
