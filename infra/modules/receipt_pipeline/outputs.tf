output "bucket_name" {
  value = aws_s3_bucket.receipts.id
}

output "bucket_arn" {
  value = aws_s3_bucket.receipts.arn
}

output "upload_prefix" {
  value = local.upload_prefix
}

output "raw_prefix" {
  value = local.raw_prefix
}

output "table_name" {
  value = aws_dynamodb_table.receipts.name
}

output "table_arn" {
  value = aws_dynamodb_table.receipts.arn
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.pipeline.arn
}

output "review_queue_url" {
  value = aws_sqs_queue.review.url
}

output "pipeline_dlq_url" {
  value = aws_sqs_queue.pipeline_dlq.url
}
