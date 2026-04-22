output "frontend_bucket_id" {
  value = aws_s3_bucket.frontend.id
}

output "frontend_bucket_arn" {
  value = aws_s3_bucket.frontend.arn
}

output "frontend_bucket_regional_domain" {
  value = aws_s3_bucket.frontend.bucket_regional_domain_name
}

output "backup_bucket_id" {
  value = aws_s3_bucket.backup.id
}

output "backup_bucket_arn" {
  value = aws_s3_bucket.backup.arn
}
