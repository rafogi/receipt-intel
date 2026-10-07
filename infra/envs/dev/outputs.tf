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

output "api_url" {
  value = module.api.api_url
}

output "user_pool_id" {
  value = module.api.user_pool_id
}

output "web_client_id" {
  value = module.api.web_client_id
}

output "login_domain" {
  value = module.api.login_domain
}
