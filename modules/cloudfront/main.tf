terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      configuration_aliases = [aws.us_east_1]
    }
  }
}

# ── Origin Access Control ─────────────────────────────────────────────────────

resource "aws_cloudfront_origin_access_control" "main" {
  name                              = "${var.environment}-wfh-frontend-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# ── CloudFront Distribution ───────────────────────────────────────────────────

resource "aws_cloudfront_distribution" "main" {
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  comment             = "wfh-frontend-cdn"
  price_class         = "PriceClass_All"
  aliases             = [var.domain_name]
  web_acl_id          = var.waf_web_acl_arn != "" ? var.waf_web_acl_arn : null
  tags                = merge(var.tags, { tool_name = "${var.environment}-wfh-cloudfront-distribution" })

  origin {
    domain_name              = var.frontend_bucket_regional_domain
    origin_id                = "${var.frontend_bucket_id}.s3.${data.aws_region.current.name}.amazonaws.com"
    origin_access_control_id = aws_cloudfront_origin_access_control.main.id
  }

  default_cache_behavior {
    target_origin_id       = "${var.frontend_bucket_id}.s3.${data.aws_region.current.name}.amazonaws.com"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    # Managed CachingOptimized policy
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = var.acm_certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
}

data "aws_region" "current" {}
