output "table_name" {
  value = aws_dynamodb_table.budgets.name
}

output "table_arn" {
  value = aws_dynamodb_table.budgets.arn
}

output "topic_arn" {
  value = aws_sns_topic.notifications.arn
}

output "alerts_dlq_url" {
  value = aws_sqs_queue.alerts_dlq.url
}

output "summary_function_name" {
  value = module.summary.function_name
}
