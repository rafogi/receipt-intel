variable "name" {
  description = "Function name; also used to name its role and log group."
  type        = string
}

variable "source_dir" {
  description = "Directory containing handler.py, zipped as the deployment package."
  type        = string
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "environment_variables" {
  type    = map(string)
  default = {}
}
