# Ingestion workflow: one execution per uploaded photo (docs/PLAN.md).
#
#   ValidateKey -> ParseKey -> Claim (idempotency) -> CheckFileType -> Analyze
#     -> Extract (placeholder) -> Validate -> Save -> NeedsReview? -> Publish
#
# Any step that exhausts its retries goes to RecordFailure -> SendToDLQ -> Fail,
# so a receipt is never dropped silently: it ends as needs_manual_entry and an
# engineer-facing message lands in the pipeline DLQ.

locals {
  # Retry transient errors only. DynamoDB's list deliberately excludes
  # ConditionalCheckFailedException: that one is a decision, not a fault.
  retry_lambda = [{
    ErrorEquals = [
      "Lambda.ServiceException",
      "Lambda.AWSLambdaException",
      "Lambda.SdkClientException",
      "Lambda.TooManyRequestsException",
      "RetryableError",
    ]
    IntervalSeconds = 2
    MaxAttempts     = 4
    BackoffRate     = 2
    JitterStrategy  = "FULL"
  }]
  retry_dynamodb = [{
    ErrorEquals = [
      "DynamoDB.ProvisionedThroughputExceededException",
      "DynamoDB.ThrottlingException",
      "DynamoDB.RequestLimitExceeded",
      "DynamoDB.InternalServerErrorException",
    ]
    IntervalSeconds = 1
    MaxAttempts     = 4
    BackoffRate     = 2
    JitterStrategy  = "FULL"
  }]
  retry_any = [{
    ErrorEquals     = ["States.TaskFailed"]
    IntervalSeconds = 1
    MaxAttempts     = 3
    BackoffRate     = 2
    JitterStrategy  = "FULL"
  }]
  catch_failure = [{
    ErrorEquals = ["States.ALL"]
    Assign      = { failure = "{% $states.errorOutput %}" }
    Next        = "RecordFailure"
  }]

  receipt_key = {
    userId    = { S = "{% $userId %}" }
    receiptId = { S = "{% $receiptId %}" }
  }

  definition = {
    Comment        = "Receipt ingestion: Textract, extraction, rule-based validation, routing."
    QueryLanguage  = "JSONata"
    StartAt        = "ValidateKey"
    TimeoutSeconds = 600

    States = {
      ValidateKey = {
        Type = "Choice"
        Choices = [{
          Condition = "{% $contains($states.input.detail.object.key, /^uploads\\/[A-Za-z0-9_-]+\\/[A-Za-z0-9_-]+\\.[A-Za-z0-9]+$/) %}"
          Next      = "ParseKey"
        }]
        Default = "QuarantineEvent"
      }

      QuarantineEvent = {
        Type     = "Task"
        Resource = "arn:aws:states:::sqs:sendMessage"
        Arguments = {
          QueueUrl    = aws_sqs_queue.pipeline_dlq.url
          MessageBody = "{% $string({'reason': 'invalid_object_key', 'event': $states.input}) %}"
        }
        Retry = local.retry_any
        Catch = [{ ErrorEquals = ["States.ALL"], Next = "InvalidKey" }]
        Next  = "InvalidKey"
      }

      InvalidKey = {
        Type  = "Fail"
        Error = "InvalidObjectKey"
        Cause = "Object key doesn't match uploads/{userId}/{receiptId}.{ext}"
      }

      ParseKey = {
        Type = "Pass"
        Assign = {
          bucket     = "{% $states.input.detail.bucket.name %}"
          key        = "{% $states.input.detail.object.key %}"
          etag       = "{% $states.input.detail.object.etag %}"
          uploadedAt = "{% $states.input.time %}"
          userId     = "{% $split($states.input.detail.object.key, '/')[1] %}"
          receiptId  = "{% $substringBefore($split($states.input.detail.object.key, '/')[2], '.') %}"
          fileType   = "{% $lowercase($substringAfter($split($states.input.detail.object.key, '/')[2], '.')) %}"
        }
        Next = "Claim"
      }

      # Idempotency (ADR-0006): the first event for this object version creates
      # the item. A repeat delivery (same ETag) fails the condition and stops.
      # A re-upload with new content reprocesses, unless the user entered the
      # receipt by hand.
      Claim = {
        Type     = "Task"
        Resource = "arn:aws:states:::dynamodb:putItem"
        Arguments = {
          TableName = aws_dynamodb_table.receipts.name
          Item = merge(local.receipt_key, {
            status     = { S = "processing" }
            statusKey  = { S = "{% 'processing#' & $uploadedAt %}" }
            objectKey  = { S = "{% $key %}" }
            etag       = { S = "{% $etag %}" }
            uploadedAt = { S = "{% $uploadedAt %}" }
            source     = { S = "pipeline" }
          })
          ConditionExpression      = "attribute_not_exists(receiptId) OR (#etag <> :etag AND (attribute_not_exists(#src) OR #src <> :manual))"
          ExpressionAttributeNames = { "#etag" = "etag", "#src" = "source" }
          ExpressionAttributeValues = {
            ":etag"   = { S = "{% $etag %}" }
            ":manual" = { S = "manual" }
          }
        }
        Retry = local.retry_dynamodb
        Catch = concat(
          [{ ErrorEquals = ["DynamoDB.ConditionalCheckFailedException"], Next = "AlreadyProcessed" }],
          local.catch_failure,
        )
        Next = "CheckFileType"
      }

      AlreadyProcessed = {
        Type    = "Succeed"
        Comment = "Duplicate event for an object version already claimed, or a manual entry."
      }

      CheckFileType = {
        Type = "Choice"
        Choices = [{
          Condition = "{% $fileType in ['jpg', 'jpeg', 'png'] %}"
          Next      = "Analyze"
        }]
        Default = "UnsupportedFileType"
      }

      # PDFs and emails arrive in Phase 4; until then they wait for the user.
      UnsupportedFileType = {
        Type     = "Task"
        Resource = "arn:aws:states:::dynamodb:updateItem"
        Arguments = {
          TableName        = aws_dynamodb_table.receipts.name
          Key              = local.receipt_key
          UpdateExpression = "SET #status = :status, #statusKey = :statusKey, #reasons = :reasons, #processedAt = :now"
          ExpressionAttributeNames = {
            "#status"      = "status"
            "#statusKey"   = "statusKey"
            "#reasons"     = "reasons"
            "#processedAt" = "processedAt"
          }
          ExpressionAttributeValues = {
            ":status"    = { S = "needs_manual_entry" }
            ":statusKey" = { S = "{% 'needs_manual_entry#' & $now() %}" }
            ":reasons"   = { L = [{ S = "unsupported_file_type" }] }
            ":now"       = { S = "{% $now() %}" }
          }
        }
        Assign = { status = "needs_manual_entry" }
        Retry  = local.retry_dynamodb
        Catch  = local.catch_failure
        Next   = "Publish"
      }

      Analyze = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Arguments = {
          FunctionName = module.analyze.function_arn
          Payload = {
            bucket    = "{% $bucket %}"
            key       = "{% $key %}"
            userId    = "{% $userId %}"
            receiptId = "{% $receiptId %}"
          }
        }
        TimeoutSeconds = 90
        Assign         = { analysis = "{% $states.result.Payload %}" }
        Retry          = local.retry_lambda
        Catch          = local.catch_failure
        Next           = "Extract"
      }

      # Placeholder until ADR-0003 picks the extraction approach: Textract's own
      # fields, no category, so every receipt lands in review. Replaced by a
      # Bedrock step (direct integration) once Bedrock is available.
      Extract = {
        Type   = "Pass"
        Assign = { extraction = "{% {'source': 'textract_only', 'fields': $analysis.fields} %}" }
        Next   = "Validate"
      }

      Validate = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Arguments = {
          FunctionName = module.validate.function_arn
          Payload = {
            userId     = "{% $userId %}"
            receiptId  = "{% $receiptId %}"
            analysis   = "{% $analysis %}"
            extraction = "{% $extraction %}"
          }
        }
        TimeoutSeconds = 30
        Assign = {
          validation = "{% $states.result.Payload %}"
          status     = "{% $states.result.Payload.status %}"
        }
        Retry = local.retry_lambda
        Catch = local.catch_failure
        Next  = "Save"
      }

      # The Validate Lambda builds the update; a manual entry made while the
      # receipt was processing is never overwritten.
      Save = {
        Type     = "Task"
        Resource = "arn:aws:states:::dynamodb:updateItem"
        Arguments = {
          TableName                 = aws_dynamodb_table.receipts.name
          Key                       = local.receipt_key
          UpdateExpression          = "{% $validation.update.UpdateExpression %}"
          ConditionExpression       = "attribute_not_exists(#src) OR #src <> :manual"
          ExpressionAttributeNames  = "{% $merge([$validation.update.ExpressionAttributeNames, {'#src': 'source'}]) %}"
          ExpressionAttributeValues = "{% $merge([$validation.update.ExpressionAttributeValues, {':manual': {'S': 'manual'}}]) %}"
        }
        Retry = local.retry_dynamodb
        Catch = concat(
          [{ ErrorEquals = ["DynamoDB.ConditionalCheckFailedException"], Next = "ManualEntryKept" }],
          local.catch_failure,
        )
        Next = "NeedsReview"
      }

      ManualEntryKept = {
        Type    = "Succeed"
        Comment = "The user entered this receipt by hand while it was processing."
      }

      NeedsReview = {
        Type = "Choice"
        Choices = [{
          Condition = "{% $status = 'needs_review' %}"
          Next      = "QueueForReview"
        }]
        Default = "Publish"
      }

      QueueForReview = {
        Type     = "Task"
        Resource = "arn:aws:states:::sqs:sendMessage"
        Arguments = {
          QueueUrl    = aws_sqs_queue.review.url
          MessageBody = "{% $string({'userId': $userId, 'receiptId': $receiptId, 'objectKey': $key, 'reasons': $validation.reasons}) %}"
        }
        Retry = local.retry_any
        Catch = local.catch_failure
        Next  = "Publish"
      }

      # Ids and status only: consumers read details from DynamoDB, so no
      # receipt contents travel on the event bus.
      Publish = {
        Type     = "Task"
        Resource = "arn:aws:states:::events:putEvents"
        Arguments = {
          Entries = [{
            Source     = "${var.name_prefix}.pipeline"
            DetailType = "ReceiptProcessed"
            Detail = {
              userId    = "{% $userId %}"
              receiptId = "{% $receiptId %}"
              status    = "{% $status %}"
            }
          }]
        }
        Retry = local.retry_any
        # The receipt is already saved; don't overwrite its status, just alert.
        Catch = [{
          ErrorEquals = ["States.ALL"]
          Assign      = { failure = "{% $states.errorOutput %}" }
          Next        = "SendToDLQ"
        }]
        Next = "CheckPublished"
      }

      # PutEvents can succeed overall while rejecting an entry.
      CheckPublished = {
        Type = "Choice"
        Choices = [{
          Condition = "{% $states.input.FailedEntryCount = 0 %}"
          Next      = "Done"
        }]
        Default = "PublishRejected"
      }

      PublishRejected = {
        Type   = "Pass"
        Assign = { failure = "{% {'Error': 'EventPublishRejected', 'Cause': $string($states.input.Entries)} %}" }
        Next   = "SendToDLQ"
      }

      Done = {
        Type = "Succeed"
      }

      RecordFailure = {
        Type     = "Task"
        Resource = "arn:aws:states:::dynamodb:updateItem"
        Arguments = {
          TableName           = aws_dynamodb_table.receipts.name
          Key                 = local.receipt_key
          UpdateExpression    = "SET #status = :status, #statusKey = :statusKey, #failure = :failure, #processedAt = :now"
          ConditionExpression = "attribute_not_exists(#src) OR #src <> :manual"
          ExpressionAttributeNames = {
            "#status"      = "status"
            "#statusKey"   = "statusKey"
            "#failure"     = "failure"
            "#processedAt" = "processedAt"
            "#src"         = "source"
          }
          ExpressionAttributeValues = {
            ":status"    = { S = "needs_manual_entry" }
            ":statusKey" = { S = "{% 'needs_manual_entry#' & $now() %}" }
            ":now"       = { S = "{% $now() %}" }
            ":manual"    = { S = "manual" }
            ":failure" = { M = {
              error = { S = "{% $exists($failure.Error) ? $failure.Error : 'unknown' %}" }
              cause = { S = "{% $exists($failure.Cause) ? $substring($failure.Cause, 0, 1000) : 'none' %}" }
            } }
          }
        }
        Retry = local.retry_dynamodb
        Catch = [{ ErrorEquals = ["States.ALL"], Next = "SendToDLQ" }]
        Next  = "SendToDLQ"
      }

      SendToDLQ = {
        Type     = "Task"
        Resource = "arn:aws:states:::sqs:sendMessage"
        Arguments = {
          QueueUrl    = aws_sqs_queue.pipeline_dlq.url
          MessageBody = "{% $string({'execution': $states.context.Execution.Id, 'objectKey': $key, 'userId': $userId, 'receiptId': $receiptId, 'failure': $failure}) %}"
        }
        Retry = local.retry_any
        Catch = [{ ErrorEquals = ["States.ALL"], Next = "PipelineFailed" }]
        Next  = "PipelineFailed"
      }

      PipelineFailed = {
        Type  = "Fail"
        Error = "ReceiptProcessingFailed"
        Cause = "{% $exists($failure.Error) ? $failure.Error : 'unknown' %}"
      }
    }
  }
}

# ---------------------------------------------------------------- state machine

# /aws/vendedlogs/ is the recommended prefix for Step Functions log groups.
resource "aws_cloudwatch_log_group" "workflow" {
  name              = "/aws/vendedlogs/states/${var.name_prefix}-pipeline"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "workflow_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "workflow" {
  name               = "${var.name_prefix}-pipeline-workflow"
  assume_role_policy = data.aws_iam_policy_document.workflow_assume.json
}

data "aws_iam_policy_document" "workflow" {
  statement {
    sid     = "InvokeStepFunctions"
    actions = ["lambda:InvokeFunction"]
    resources = [
      module.analyze.function_arn,
      "${module.analyze.function_arn}:*",
      module.validate.function_arn,
      "${module.validate.function_arn}:*",
    ]
  }

  statement {
    sid       = "ReceiptsTable"
    actions   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.receipts.arn]
  }

  statement {
    sid       = "Queues"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.review.arn, aws_sqs_queue.pipeline_dlq.arn]
  }

  statement {
    sid       = "PublishEvents"
    actions   = ["events:PutEvents"]
    resources = ["arn:aws:events:${local.region}:${local.account_id}:event-bus/default"]
  }

  # Step Functions log delivery uses these account-level APIs.
  statement {
    sid = "LogDelivery"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "workflow" {
  name   = "pipeline-workflow"
  role   = aws_iam_role.workflow.id
  policy = data.aws_iam_policy_document.workflow.json
}

resource "aws_sfn_state_machine" "pipeline" {
  name       = "${var.name_prefix}-pipeline"
  role_arn   = aws_iam_role.workflow.arn
  type       = "STANDARD"
  definition = jsonencode(local.definition)

  # Errors only, and no execution data: payloads contain receipt contents.
  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.workflow.arn}:*"
    include_execution_data = false
    level                  = "ERROR"
  }

  depends_on = [aws_iam_role_policy.workflow]
}
