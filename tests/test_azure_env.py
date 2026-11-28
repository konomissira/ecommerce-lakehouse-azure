import pytest

from lakehouse.azure_env import host_without_scheme, render
from lakehouse.setup_workspace import SetupError


def test_the_file_points_every_tool_at_the_azure_workspace():
    text = render("https://adb-1234567890123456.7.azuredatabricks.net", "abc123def456")
    lines = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    assert lines == {
        "DATABRICKS_CONFIG_PROFILE": "azure",
        "DBT_DATABRICKS_HOST": "adb-1234567890123456.7.azuredatabricks.net",
        "DBT_DATABRICKS_HTTP_PATH": "/sql/1.0/warehouses/abc123def456",
        "BUNDLE_VAR_warehouse_id": "abc123def456",
    }


def test_the_file_holds_no_token():
    assert "TOKEN" not in render("https://adb-1.2.azuredatabricks.net", "w1").upper()


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://adb-1.2.azuredatabricks.net", "adb-1.2.azuredatabricks.net"),
        ("https://adb-1.2.azuredatabricks.net/", "adb-1.2.azuredatabricks.net"),
        ("adb-1.2.azuredatabricks.net", "adb-1.2.azuredatabricks.net"),
    ],
)
def test_the_host_loses_its_scheme(url, host):
    assert host_without_scheme(url) == host


@pytest.mark.parametrize("url", ["", "https://", "https://adb-1.net/browse?o=1"])
def test_a_bad_url_is_refused(url):
    with pytest.raises(SetupError):
        host_without_scheme(url)


@pytest.mark.parametrize("warehouse", ["", "abc 123", "abc;rm -rf"])
def test_a_bad_warehouse_id_is_refused(warehouse):
    with pytest.raises(SetupError):
        render("https://adb-1.2.azuredatabricks.net", warehouse)
