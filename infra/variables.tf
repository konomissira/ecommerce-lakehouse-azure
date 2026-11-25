variable "location" {
  description = "Azure region for every resource."
  type        = string
  default     = "uksouth"
}

variable "environment" {
  description = "Short environment name, used in resource names and tags."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z]{2,8}$", var.environment))
    error_message = "environment must be 2 to 8 lower-case letters, such as dev."
  }
}

variable "monthly_budget" {
  description = "Monthly budget for the whole subscription, in your billing currency."
  type        = number
  default     = 20

  validation {
    condition     = var.monthly_budget > 0
    error_message = "monthly_budget must be above zero."
  }
}

variable "budget_email" {
  description = "Where budget alerts are sent. Set TF_VAR_budget_email in .env."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.budget_email))
    error_message = "budget_email must be an email address."
  }
}
