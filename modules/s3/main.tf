# ── Frontend bucket ───────────────────────────────────────────────────────────

resource "aws_s3_bucket" "frontend" {
  bucket = "${var.environment}-wfh-frontend-${data.aws_caller_identity.current.account_id}"
  tags   = merge(var.tags, { tool_name = "${var.environment}-wfh-frontend" })
}

resource "aws_s3_bucket_website_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  index_document { suffix = "index.html" }
  error_document { key    = "error.html" }
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket     = aws_s3_bucket.frontend.id
  depends_on = [aws_s3_bucket_public_access_block.frontend]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCloudFrontServicePrincipal"
        Effect = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.frontend.arn}/*"
        Condition = {
          ArnLike = {
            "AWS:SourceArn" = var.cloudfront_distribution_arn
          }
        }
      }
    ]
  })
}

# ── Backup bucket ─────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "backup" {
  bucket = "${var.environment}-wfh-dynamodb-backups-${data.aws_caller_identity.current.account_id}"
  tags   = merge(var.tags, { tool_name = "${var.environment}-wfh-dynamodb-backups" })
}

resource "aws_s3_bucket_public_access_block" "backup" {
  bucket = aws_s3_bucket.backup.id

  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}
