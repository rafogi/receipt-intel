output "account_id" {
  value = local.account_id
}

output "state_bucket" {
  description = "Set as the TF_STATE_BUCKET repository variable in GitHub."
  value       = aws_s3_bucket.state.bucket
}

output "plan_role_arn" {
  description = "Set as the AWS_PLAN_ROLE_ARN repository variable in GitHub."
  value       = aws_iam_role.plan.arn
}

output "apply_role_arn" {
  description = "Set as the AWS_APPLY_ROLE_ARN repository variable in GitHub."
  value       = aws_iam_role.apply.arn
}
