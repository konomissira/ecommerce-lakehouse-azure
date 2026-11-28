# Unity Catalog on our own data lake.
#
#   access connector (managed identity, main.tf)
#     -> storage credential   Unity Catalog's handle on that identity
#     -> external location    permission to use abfss://lakehouse@<lake>/
#     -> catalog `ecommerce`  its storage root is in that location, so every
#                             table and volume of the lakehouse lives in our lake
#
# force_destroy is on because this is a dev environment that `make tf-destroy`
# removes after every session. Never set it on production data.

locals {
  uc_name   = replace(local.name, "-", "_")
  lake_root = "abfss://${azurerm_storage_container.lakehouse.name}@${azurerm_storage_account.lake.name}.dfs.core.windows.net/"
}

# Azure role assignments take a minute or two to reach the storage service.
# Without this wait, the first apply can fail with a 403 when the external
# location checks its access.
resource "time_sleep" "rbac_propagation" {
  create_duration = "90s"
  depends_on      = [azurerm_role_assignment.unity_storage]
}

resource "databricks_storage_credential" "lake" {
  name          = "sc_${local.uc_name}"
  comment       = "Managed identity of ${azurerm_databricks_access_connector.unity.name}. Managed by Terraform."
  force_destroy = true

  azure_managed_identity {
    access_connector_id = azurerm_databricks_access_connector.unity.id
  }

  depends_on = [time_sleep.rbac_propagation]
}

resource "databricks_external_location" "lake" {
  name            = "el_${local.uc_name}"
  url             = local.lake_root
  credential_name = databricks_storage_credential.lake.name
  comment         = "The lakehouse container of ${azurerm_storage_account.lake.name}. Managed by Terraform."
  force_destroy   = true
}

resource "databricks_catalog" "lakehouse" {
  name          = var.catalog
  storage_root  = "${databricks_external_location.lake.url}${var.catalog}"
  comment       = "Multi-tenant ecommerce KPI lakehouse, stored in our own ADLS Gen2 lake."
  force_destroy = true
}
