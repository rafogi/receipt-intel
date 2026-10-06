variable "name" {
  description = "Function name; also used to name its role and log group."
  type        = string
}

variable "source_dir" {
  description = "Directory zipped as the deployment package."
  type        = string
}

variable "handler" {
  description = "Module and function, e.g. analyze.handler."
  type        = string
}

variable "timeout" {
  type    = number
  default = 10
}

variable "memory_size" {
  type    = number
  default = 128
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "environment_variables" {
  type    = map(string)
  default = {}
}

variable "inline_policies" {
  description = "Extra IAM policies for the function, as name => policy JSON."
  type        = map(string)
  default     = {}
}
