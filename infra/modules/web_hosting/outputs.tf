output "bucket_name" {
  value = aws_s3_bucket.web.id
}

output "distribution_id" {
  value = aws_cloudfront_distribution.web.id
}

output "url" {
  description = "https://xxxx.cloudfront.net (no trailing slash)."
  value       = "https://${aws_cloudfront_distribution.web.domain_name}"
}
