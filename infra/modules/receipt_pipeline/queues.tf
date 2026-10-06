# Two kinds of failure, two queues (docs/PLAN.md, "Failure handling"):
#   review: the receipt is bad (validation failed) -> review agent / user
#   dlq:    the system broke (retries exhausted)   -> engineer, auto-redrive

resource "aws_sqs_queue" "review_dlq" {
  name                      = "${var.name_prefix}-review-dlq"
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "review" {
  name                      = "${var.name_prefix}-review"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.review_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue" "pipeline_dlq" {
  name                      = "${var.name_prefix}-pipeline-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

# EventBridge sends events it couldn't deliver to the workflow here too.
data "aws_iam_policy_document" "pipeline_dlq" {
  statement {
    sid       = "AllowTriggerRuleDeadLetters"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.pipeline_dlq.arn]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.receipt_uploaded.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "pipeline_dlq" {
  queue_url = aws_sqs_queue.pipeline_dlq.id
  policy    = data.aws_iam_policy_document.pipeline_dlq.json
}
