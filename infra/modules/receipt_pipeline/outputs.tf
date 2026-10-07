output "bucket_name" {
  value = aws_s3_bucket.receipts.id
}

output "table_name" {
  value = aws_dynamodb_table.receipts.name
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
