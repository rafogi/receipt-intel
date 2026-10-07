# S3 "Object Created" under uploads/ starts one workflow execution per photo.
# The raw Textract output written under textract/ doesn't match, so no loop.

resource "aws_cloudwatch_event_rule" "receipt_uploaded" {
  name        = "${var.name_prefix}-receipt-uploaded"
  description = "Start the ingestion workflow for each uploaded receipt."

  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.receipts.id] }
      object = { key = [{ prefix = local.upload_prefix }] }
    }
  })
}

data "aws_iam_policy_document" "trigger_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "trigger" {
  name               = "${var.name_prefix}-pipeline-trigger"
  assume_role_policy = data.aws_iam_policy_document.trigger_assume.json
}

data "aws_iam_policy_document" "trigger" {
  statement {
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.pipeline.arn]
  }
}

resource "aws_iam_role_policy" "trigger" {
  name   = "start-pipeline"
  role   = aws_iam_role.trigger.id
  policy = data.aws_iam_policy_document.trigger.json
}

resource "aws_cloudwatch_event_target" "start_workflow" {
  rule     = aws_cloudwatch_event_rule.receipt_uploaded.name
  arn      = aws_sfn_state_machine.pipeline.arn
  role_arn = aws_iam_role.trigger.arn

  # If the workflow can't be started for a day, park the event rather than drop it.
  retry_policy {
    maximum_event_age_in_seconds = 86400
    maximum_retry_attempts       = 185
  }

  dead_letter_config {
    arn = aws_sqs_queue.pipeline_dlq.arn
  }
}
