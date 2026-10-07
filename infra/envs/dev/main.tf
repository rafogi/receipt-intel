locals {
  name_prefix = "${var.project}-${var.environment}"
}

# Phase 2: S3 upload -> EventBridge -> Step Functions -> DynamoDB / review queue.
module "pipeline" {
  source = "../../modules/receipt_pipeline"

  name_prefix        = local.name_prefix
  lambda_source_dir  = "${path.root}/../../../services/pipeline/src"
  log_retention_days = 14
}
