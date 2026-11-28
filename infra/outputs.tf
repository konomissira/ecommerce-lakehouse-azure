output "workspace_url" {
  description = "Open this in a browser, and use it for `databricks auth login --host`."
  value       = "https://${azurerm_databricks_workspace.main.workspace_url}"
}

output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "storage_account" {
  value = azurerm_storage_account.lake.name
}

output "lakehouse_root" {
  description = "Where Unity Catalog will keep the lakehouse's data (feature/07)."
  value       = "abfss://${azurerm_storage_container.lakehouse.name}@${azurerm_storage_account.lake.name}.dfs.core.windows.net/"
}

output "access_connector_id" {
  description = "The identity Unity Catalog uses to reach the lake (feature/07)."
  value       = azurerm_databricks_access_connector.unity.id
}

output "catalog" {
  value = databricks_catalog.lakehouse.name
}

output "catalog_storage_root" {
  description = "Where every table and volume of the catalog is stored: our own lake."
  value       = databricks_catalog.lakehouse.storage_root
}
