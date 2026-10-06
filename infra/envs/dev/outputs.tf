output "receipts_bucket" {
  value = module.pipeline.bucket_name
}

output "receipts_table" {
  value = module.pipeline.table_name
}

output "pipeline_state_machine_arn" {
  value = module.pipeline.state_machine_arn
}

output "review_queue_url" {
  value = module.pipeline.review_queue_url
}

output "pipeline_dlq_url" {
  value = module.pipeline.pipeline_dlq_url
}
