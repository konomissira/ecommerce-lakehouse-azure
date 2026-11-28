"""Static checks on the Terraform in infra/: they run without Azure or Terraform.

CI also runs `terraform fmt -check` and `terraform validate`. These tests guard
the decisions that keep the project safe and cheap, so a later change can't undo
them quietly.
"""

import re
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[1] / "infra"
TF = {p.name: p.read_text() for p in sorted(INFRA.glob("*.tf"))}
ALL = "\n".join(TF.values())
GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def block(kind: str, name: str) -> str:
    """The text of one resource or data block."""
    start = ALL.index(f'{kind} "{name}"')
    depth = 0
    for i in range(ALL.index("{", start), len(ALL)):
        depth += {"{": 1, "}": -1}.get(ALL[i], 0)
        if depth == 0:
            return ALL[start : i + 1]
    raise AssertionError(f"unclosed block {kind} {name}")


def test_the_expected_files_exist():
    assert set(TF) == {"main.tf", "outputs.tf", "unity_catalog.tf", "variables.tf", "versions.tf"}


@pytest.mark.parametrize("name", sorted(TF))
def test_no_subscription_or_tenant_id_is_hardcoded(name):
    assert not GUID.search(TF[name]), f"{name} contains an ID: use ARM_SUBSCRIPTION_ID"


def test_terraform_and_providers_are_pinned_to_a_major_version():
    assert '">= 1.9, < 2.0"' in TF["versions.tf"]
    assert re.search(r'azurerm\s*=\s*\{[^}]*version\s*=\s*"~> 4\.0"', TF["versions.tf"])


def test_terraform_does_not_register_providers_on_its_own():
    assert 'resource_provider_registrations = "none"' in TF["versions.tf"]


def test_the_lake_is_adls_gen2_private_and_tls_1_2():
    lake = block("resource", "azurerm_storage_account")
    assert "is_hns_enabled                  = true" in lake
    assert '"TLS1_2"' in lake
    assert "allow_nested_items_to_be_public = false" in lake
    assert "https_traffic_only_enabled      = true" in lake
    assert 'container_access_type = "private"' in block("resource", "azurerm_storage_container")


def test_unity_catalog_reaches_the_lake_with_a_managed_identity_and_least_privilege():
    assert 'type = "SystemAssigned"' in block("resource", "azurerm_databricks_access_connector")
    role = block("resource", "azurerm_role_assignment")
    assert 'role_definition_name = "Storage Blob Data Contributor"' in role
    assert "scope                = azurerm_storage_account.lake.id" in role, "one account only"


def test_the_workspace_is_premium_for_unity_catalog_and_serverless():
    assert 'sku                         = "premium"' in block(
        "resource", "azurerm_databricks_workspace"
    )


def test_the_budget_covers_the_whole_subscription_and_alerts_early():
    budget = block("resource", "azurerm_consumption_budget_subscription")
    thresholds = sorted(int(t) for t in re.findall(r"threshold\s+=\s+(\d+)", budget))
    assert thresholds == [50, 80, 100]
    assert '"Forecasted"' in budget, "warn before the money is spent, not after"
    assert "contact_emails = [var.budget_email]" in budget
    assert "ignore_changes = [time_period]" in budget


def test_every_resource_is_tagged():
    taggable = [
        "azurerm_resource_group",
        "azurerm_storage_account",
        "azurerm_databricks_access_connector",
        "azurerm_databricks_workspace",
    ]
    for kind in taggable:
        assert "tags" in block("resource", kind), kind


def test_cleaning_up_is_one_command():
    makefile = (INFRA.parent / "Makefile").read_text()
    assert "tf-destroy:" in makefile
    assert "tf-plan:" in makefile and "-out=tfplan" in makefile
    assert "apply tfplan" in makefile, "apply exactly the plan that was reviewed"


def test_ci_checks_the_terraform():
    ci = (INFRA.parent / ".github" / "workflows" / "ci.yml").read_text()
    for step in ("fmt -check", "init -backend=false", "validate"):
        assert step in ci


# ---------------------------------------------------------------- Unity Catalog (feature/07)


def test_databricks_signs_in_through_az_login_not_a_token():
    provider = block("provider", "databricks")
    assert 'auth_type                   = "azure-cli"' in provider
    assert "token" not in provider
    assert re.search(r'databricks\s*=\s*\{[^}]*version\s*=\s*"~> 1\.\d+"', TF["versions.tf"])


def test_the_credential_uses_the_access_connector_after_rbac_has_propagated():
    credential = block("resource", "databricks_storage_credential")
    assert "access_connector_id = azurerm_databricks_access_connector.unity.id" in credential
    assert "depends_on = [time_sleep.rbac_propagation]" in credential
    wait = block("resource", "time_sleep")
    assert "depends_on      = [azurerm_role_assignment.unity_storage]" in wait


def test_the_external_location_is_our_lake_through_that_credential():
    location = block("resource", "databricks_external_location")
    assert "url             = local.lake_root" in location
    assert "credential_name = databricks_storage_credential.lake.name" in location
    assert ".dfs.core.windows.net/" in ALL, "ADLS Gen2 endpoint, abfss://"


def test_the_catalog_is_stored_in_our_lake():
    catalog = block("resource", "databricks_catalog")
    assert "name          = var.catalog" in catalog
    assert 'storage_root  = "${databricks_external_location.lake.url}${var.catalog}"' in catalog


def test_dev_objects_can_be_destroyed_with_the_workspace():
    for kind in (
        "databricks_storage_credential",
        "databricks_external_location",
        "databricks_catalog",
    ):
        assert "force_destroy" in block("resource", kind), kind


def test_the_catalog_name_matches_the_rest_of_the_project():
    assert 'default     = "ecommerce"' in block("variable", "catalog")
