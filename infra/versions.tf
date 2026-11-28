terraform {
  # Pinned like dbt and ruff: a new major version can change behaviour.
  required_version = ">= 1.9, < 2.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.12"
    }
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.50"
    }
  }
}

provider "azurerm" {
  features {}

  # The subscription comes from ARM_SUBSCRIPTION_ID (set in .env), never from code.
  # Providers were registered once by hand (see the handbook), so Terraform
  # doesn't try to register dozens more on every run.
  resource_provider_registrations = "none"
}

# Talks to the workspace Terraform creates, signed in through `az login`:
# no Databricks token is created or stored.
provider "databricks" {
  host                        = "https://${azurerm_databricks_workspace.main.workspace_url}"
  azure_workspace_resource_id = azurerm_databricks_workspace.main.id
  auth_type                   = "azure-cli"
}
