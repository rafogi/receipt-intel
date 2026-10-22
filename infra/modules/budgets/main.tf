# Phase 5: budgets, budget alerts and the weekly summary.
#
#   receipts table stream (processed only) -> alerts Lambda -> SNS
#   EventBridge Scheduler (Mondays)        -> summary Lambda -> Bedrock -> SNS
#
# Both functions ship the shared package (services/pipeline/src) and compute
# totals with spending.py, the same code behind GET /spending.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  # "us.amazon.nova-lite-v1:0" -> "amazon.nova-lite-v1:0": the profile routes
  # to the foundation model in any US region.
  summary_base_model = replace(var.summary_model_id, "/^(us|eu|apac|global)\\./", "")
}

# ---------------------------------------------------------------- budgets table

# One item per user and budget key (a category, or "total" for all of them):
# monthlyLimit, updatedAt, and alerts = { "YYYY-MM": highest threshold sent }.
resource "aws_dynamodb_table" "budgets" {
  name                        = "${var.name_prefix}-budgets"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "userId"
  range_key                   = "category"
  deletion_protection_enabled = var.deletion_protection

  attribute {
    name = "userId"
    type = "S"
  }

  attribute {
    name = "category"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
}

# ---------------------------------------------------------------- notifications

resource "aws_sns_topic" "notifications" {
  name              = "${var.name_prefix}-notifications"
  kms_master_key_id = "alias/aws/sns"
}

resource "aws_sns_topic_subscription" "email" {
  count = var.notification_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.notifications.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

# ---------------------------------------------------------------- budget alerts

data "aws_iam_policy_document" "alerts" {
  statement {
    sid       = "ReadReceipts"
    actions   = ["dynamodb:Query"]
    resources = ["${var.receipts_table_arn}/index/byDate"]
  }

  statement {
    sid       = "Budgets"
    actions   = ["dynamodb:Query", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.budgets.arn]
  }

  statement {
    sid       = "ReadStream"
    actions   = ["dynamodb:DescribeStream", "dynamodb:GetRecords", "dynamodb:GetShardIterator", "dynamodb:ListStreams"]
    resources = [var.receipts_stream_arn]
  }

  statement {
    sid       = "Notify"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.notifications.arn]
  }

  statement {
    sid       = "FailedBatches"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.alerts_dlq.arn]
  }
}

module "alerts" {
  source = "../python_lambda"

  name               = "${var.name_prefix}-budget-alerts"
  source_dir         = var.lambda_source_dir
  handler            = "alerts.handler"
  timeout            = 30
  memory_size        = 256
  log_retention_days = var.log_retention_days
  inline_policies    = { "budget-alerts" = data.aws_iam_policy_document.alerts.json }

  environment_variables = {
    TABLE         = var.receipts_table_name
    BUDGETS_TABLE = aws_dynamodb_table.budgets.name
    TOPIC_ARN     = aws_sns_topic.notifications.arn
    APP_URL       = var.app_url
  }
}

# Batches that still fail after retries are recorded here (stream position
# only, no receipt data), for the Phase 6 alarms.
resource "aws_sqs_queue" "alerts_dlq" {
  name                      = "${var.name_prefix}-budget-alerts-dlq"
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled   = true
}

resource "aws_lambda_event_source_mapping" "alerts" {
  event_source_arn  = var.receipts_stream_arn
  function_name     = module.alerts.function_arn
  starting_position = "LATEST"

  batch_size                         = 25
  maximum_batching_window_in_seconds = 5
  maximum_retry_attempts             = 3
  maximum_record_age_in_seconds      = 86400
  bisect_batch_on_function_error     = true

  # Only receipts that are (now) processed can move a budget.
  filter_criteria {
    filter {
      pattern = jsonencode({ dynamodb = { NewImage = { status = { S = ["processed"] } } } })
    }
  }

  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.alerts_dlq.arn
    }
  }
}

# ---------------------------------------------------------------- weekly summary

data "aws_iam_policy_document" "summary" {
  statement {
    sid       = "ReadReceipts"
    actions   = ["dynamodb:Query"]
    resources = ["${var.receipts_table_arn}/index/byDate", "${var.receipts_table_arn}/index/byStatus"]
  }

  statement {
    sid       = "ReadBudgets"
    actions   = ["dynamodb:Query"]
    resources = [aws_dynamodb_table.budgets.arn]
  }

  statement {
    sid       = "ListUsers"
    actions   = ["cognito-idp:ListUsers"]
    resources = [var.user_pool_arn]
  }

  # The inference profile, plus the model in each region it may route to.
  statement {
    sid     = "WordTheSummary"
    actions = ["bedrock:InvokeModel"]
    resources = [
      "arn:aws:bedrock:${local.region}:${local.account_id}:inference-profile/${var.summary_model_id}",
      "arn:aws:bedrock:*::foundation-model/${local.summary_base_model}",
    ]
  }

  statement {
    sid       = "Notify"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.notifications.arn]
  }
}

module "summary" {
  source = "../python_lambda"

  name               = "${var.name_prefix}-weekly-summary"
  source_dir         = var.lambda_source_dir
  handler            = "summary.handler"
  timeout            = 120
  memory_size        = 256
  log_retention_days = var.log_retention_days
  inline_policies    = { "weekly-summary" = data.aws_iam_policy_document.summary.json }

  environment_variables = {
    TABLE         = var.receipts_table_name
    BUDGETS_TABLE = aws_dynamodb_table.budgets.name
    TOPIC_ARN     = aws_sns_topic.notifications.arn
    USER_POOL_ID  = var.user_pool_id
    MODEL_ID      = var.summary_model_id
    APP_URL       = var.app_url
    TIMEZONE      = var.summary_timezone
  }
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name_prefix}-weekly-summary-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler_invoke" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [module.summary.function_arn]
  }
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name   = "invoke-weekly-summary"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler_invoke.json
}

resource "aws_scheduler_schedule" "weekly_summary" {
  name                         = "${var.name_prefix}-weekly-summary"
  schedule_expression          = var.summary_schedule
  schedule_expression_timezone = var.summary_timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = module.summary.function_arn
    role_arn = aws_iam_role.scheduler.arn

    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 3600
    }
  }
}
