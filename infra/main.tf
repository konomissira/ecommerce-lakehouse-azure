# The Azure side of the lakehouse: one resource group holding the data lake
# storage, the identity Unity Catalog uses to reach it, the Databricks workspace,
# and a budget that emails you before costs surprise you.
#
#   make tf-plan      show what would change; changes nothing
#   make tf-apply     create or update it
#   make tf-destroy   delete it all at the end of a session (costs stop)

locals {
  name = "ecommerce-lakehouse-${var.environment}"
  tags = {
    project     = "ecommerce-lakehouse"
    environment = var.environment
    managed_by  = "terraform"
  }
}

data "azurerm_subscription" "current" {}

# Storage account names are global across Azure, so they get a random suffix.
resource "random_string" "suffix" {
  length  = 6
  lower   = true
  upper   = false
  numeric = true
  special = false
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

# ---------------------------------------------------------------- data lake

resource "azurerm_storage_account" "lake" {
  name                            = "stlake${var.environment}${random_string.suffix.result}"
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_kind                    = "StorageV2"
  account_tier                    = "Standard"
  account_replication_type        = "LRS" # one region is enough for a dev lake
  is_hns_enabled                  = true  # hierarchical namespace = ADLS Gen2
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  tags                            = local.tags
}

resource "azurerm_storage_container" "lakehouse" {
  name                  = "lakehouse"
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

# ---------------------------------------------------------------- identity for Unity Catalog

# A managed identity that Unity Catalog uses to read and write the lake.
# No keys or secrets: Azure issues its tokens.
resource "azurerm_databricks_access_connector" "unity" {
  name                = "dac-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags

  identity {
    type = "SystemAssigned"
  }
}

# Least privilege: data access on this one storage account, nothing else.
resource "azurerm_role_assignment" "unity_storage" {
  scope                = azurerm_storage_account.lake.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_databricks_access_connector.unity.identity[0].principal_id
}

# ---------------------------------------------------------------- workspace

# Premium is required for Unity Catalog and serverless compute.
# The workspace itself costs nothing while idle, but its default secure cluster
# connectivity adds a NAT gateway billed by the hour: destroy after each session.
resource "azurerm_databricks_workspace" "main" {
  name                        = "dbw-${local.name}"
  resource_group_name         = azurerm_resource_group.main.name
  location                    = azurerm_resource_group.main.location
  sku                         = "premium"
  managed_resource_group_name = "rg-${local.name}-databricks-managed"
  tags                        = local.tags
}

# ---------------------------------------------------------------- budget

# Subscription-wide, so it also covers what Databricks creates in its own
# managed resource group (such as the NAT gateway). A budget only alerts: it
# never stops spending. `make tf-destroy` is what stops it.
resource "azurerm_consumption_budget_subscription" "main" {
  name            = "budget-${local.name}"
  subscription_id = data.azurerm_subscription.current.id
  amount          = var.monthly_budget
  time_grain      = "Monthly"

  time_period {
    start_date = formatdate("YYYY-MM-01'T'00:00:00'Z'", timestamp())
  }

  notification {
    enabled        = true
    threshold      = 50
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_emails = [var.budget_email]
  }

  notification {
    enabled        = true
    threshold      = 80
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_emails = [var.budget_email]
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Forecasted"
    contact_emails = [var.budget_email]
  }

  lifecycle {
    # The start date is set once; without this, every plan would want to move it.
    ignore_changes = [time_period]
  }
}
