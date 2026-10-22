variable "name_prefix" {
  description = "Prefix for every resource name, e.g. receipt-intel-dev."
  type        = string
}

variable "lambda_source_dir" {
  description = "services/pipeline/src: alerts.py and summary.py share spending.py with the API."
  type        = string
}

variable "receipts_table_name" {
  type = string
}

variable "receipts_table_arn" {
  type = string
}

variable "receipts_stream_arn" {
  description = "Stream of the receipts table (NEW_IMAGE)."
  type        = string
}

variable "user_pool_id" {
  description = "The weekly summary lists users here (their sub is the receipts userId)."
  type        = string
}

variable "user_pool_arn" {
  type = string
}

variable "notification_email" {
  description = "Address subscribed to budget alerts and weekly summaries. Empty: no subscription (topic only). AWS emails a confirmation link first."
  type        = string
  default     = ""
}

variable "app_url" {
  description = "Web app URL for links in notifications (no trailing slash)."
  type        = string
  default     = ""
}

variable "summary_model_id" {
  description = "Bedrock model (inference profile) that words the weekly summary."
  type        = string
  default     = "us.amazon.nova-lite-v1:0"
}

variable "summary_schedule" {
  description = "When the weekly summary runs, in summary_timezone."
  type        = string
  default     = "cron(0 8 ? * MON *)"
}

variable "summary_timezone" {
  type    = string
  default = "America/Vancouver"
}

variable "deletion_protection" {
  description = "Protect the budgets table from deletion (turn off only to tear an environment down)."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  type    = number
  default = 14
}
