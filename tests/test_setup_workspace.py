import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

from lakehouse.landing import EXPECTED_COLUMNS, LandingContractError
from lakehouse.setup_workspace import (
    SetupError,
    catalog_exists,
    check_all,
    check_local_file,
    main,
    pick_warehouse,
    run_sql,
    setup_statements,
    upload,
)

# ---------- Helpers: CSVs shaped exactly like the generator's ----------

HEADERS = {
    "customers": [
        "customer_id", "tenant_id", "first_name", "last_name", "email", "city", "created_at",
    ],
    "orders": [
        "order_id", "tenant_id", "customer_id", "product_id", "order_date",
        "quantity", "gross_sales", "discount", "net_sales", "utm_source",
    ],
    "products": ["product_id", "tenant_id", "sku", "product_name", "category", "price"],
    "refunds": ["refund_id", "tenant_id", "order_id", "refund_date", "refund_amount", "reason"],
}  # fmt: skip


def write_csv(directory: Path, entity: str, tenants=("brand_a", "brand_b", "brand_a")) -> Path:
    path = directory / f"shopify_{entity}_bronze.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS[entity])
        for i, tenant in enumerate(tenants):
            row = [f"v{i}"] * len(HEADERS[entity])
            row[1] = tenant
            writer.writerow(row)
    return path


@pytest.fixture
def data_dir(tmp_path):
    for entity in HEADERS:
        write_csv(tmp_path, entity)
    return tmp_path


def test_the_helper_headers_match_the_contract():
    for entity, header in HEADERS.items():
        assert set(header) == EXPECTED_COLUMNS[entity]


# ---------- SQL ----------


def test_statements_create_catalog_schemas_and_volume_in_order():
    statements = setup_statements("ecommerce")
    assert statements[0].startswith("CREATE CATALOG IF NOT EXISTS ecommerce")
    assert [s.split(" COMMENT")[0] for s in statements[1:]] == [
        "CREATE SCHEMA IF NOT EXISTS ecommerce.bronze",
        "CREATE SCHEMA IF NOT EXISTS ecommerce.silver",
        "CREATE SCHEMA IF NOT EXISTS ecommerce.gold",
        "CREATE VOLUME IF NOT EXISTS ecommerce.bronze.landing",
    ]


def test_the_free_edition_catalog_is_never_created():
    assert not any("CREATE CATALOG" in s for s in setup_statements("workspace"))


@pytest.mark.parametrize("name", ["", "Ecommerce", "ecom-merce", "x; DROP CATALOG main", "1cat"])
def test_unsafe_catalog_names_are_refused(name):
    with pytest.raises(SetupError):
        setup_statements(name)


# ---------- Local files ----------


def test_a_good_file_is_checked_and_counted(data_dir):
    checked = check_local_file(data_dir / "shopify_orders_bronze.csv", "ecommerce")
    assert checked.entity == "orders"
    assert checked.target_path == (
        "/Volumes/ecommerce/bronze/landing/orders/shopify_orders_bronze.csv"
    )
    assert checked.rows == 3
    assert checked.rows_per_tenant == {"brand_a": 2, "brand_b": 1}


def test_an_unknown_tenant_names_the_file_and_line(tmp_path):
    path = write_csv(tmp_path, "refunds", tenants=("brand_a", "brand_c"))
    with pytest.raises(LandingContractError, match=r"line 3.*brand_c"):
        check_local_file(path, "ecommerce")


def test_a_wrong_header_is_refused(tmp_path):
    path = tmp_path / "shopify_products_bronze.csv"
    path.write_text("product_id,sku,product_name,category,price\np1,s,n,c,1.0\n")
    with pytest.raises(LandingContractError, match="tenant_id"):
        check_local_file(path, "ecommerce")


def test_an_empty_file_is_refused(tmp_path):
    path = write_csv(tmp_path, "customers", tenants=())
    with pytest.raises(LandingContractError, match="no data rows"):
        check_local_file(path, "ecommerce")


def test_a_missing_entity_stops_everything(data_dir):
    (data_dir / "shopify_refunds_bronze.csv").unlink()
    with pytest.raises(LandingContractError, match="refunds"):
        check_all(data_dir, "ecommerce")


def test_unrelated_csvs_in_the_folder_are_ignored(data_dir):
    (data_dir / "notes.csv").write_text("a,b\n1,2\n")
    assert len(check_all(data_dir, "ecommerce")) == 4


# ---------- Databricks, with a fake client ----------


def response(state, statement_id="s1", message=None):
    return SimpleNamespace(
        statement_id=statement_id,
        status=SimpleNamespace(state=SimpleNamespace(value=state),
                               error=SimpleNamespace(message=message)),
    )  # fmt: skip


class FakeStatements:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.executed = []

    def execute_statement(self, statement, warehouse_id, wait_timeout):
        self.executed.append(statement)
        return self.responses.pop(0)

    def get_statement(self, statement_id):
        return self.responses.pop(0)


def client_with(*responses):
    return SimpleNamespace(statement_execution=FakeStatements(*responses))


def test_a_statement_that_succeeds_at_once():
    run_sql(client_with(response("SUCCEEDED")), "wh", "SELECT 1")


def test_a_slow_statement_is_polled_until_it_succeeds():
    client = client_with(response("PENDING"), response("RUNNING"), response("SUCCEEDED"))
    run_sql(client, "wh", "SELECT 1", sleep=lambda s: None)
    assert client.statement_execution.responses == []


def test_a_failed_statement_raises_with_the_databricks_message():
    client = client_with(response("FAILED", message="PERMISSION_DENIED: no CREATE CATALOG"))
    with pytest.raises(SetupError, match="PERMISSION_DENIED"):
        run_sql(client, "wh", "CREATE CATALOG x")


def test_a_statement_that_never_finishes_times_out():
    client = client_with(*[response("RUNNING")] * 5)
    with pytest.raises(SetupError, match="timed out"):
        run_sql(client, "wh", "SELECT 1", sleep=lambda s: None,
                poll_seconds=1, timeout_seconds=2)  # fmt: skip


def test_the_starter_warehouse_is_preferred():
    warehouses = [SimpleNamespace(id="w1", name="Big one"),
                  SimpleNamespace(id="w2", name="Serverless Starter Warehouse")]  # fmt: skip
    client = SimpleNamespace(warehouses=SimpleNamespace(list=lambda: warehouses))
    assert pick_warehouse(client) == "w2"
    assert pick_warehouse(client, "explicit") == "explicit"


def test_no_warehouse_is_a_clear_error():
    client = SimpleNamespace(warehouses=SimpleNamespace(list=lambda: []))
    with pytest.raises(SetupError, match="no SQL warehouse"):
        pick_warehouse(client)


def test_upload_puts_each_file_in_its_entity_folder(data_dir):
    calls = []
    files = SimpleNamespace(
        create_directory=lambda path: calls.append(("mkdir", path)),
        upload=lambda path, contents, overwrite: calls.append(("put", path, overwrite)),
    )
    upload(SimpleNamespace(files=files), check_all(data_dir, "ecommerce"))
    puts = [c for c in calls if c[0] == "put"]
    assert len(puts) == 4
    assert ("put", "/Volumes/ecommerce/bronze/landing/refunds/shopify_refunds_bronze.csv",
            True) in puts  # fmt: skip


# ---------- CLI ----------


def test_dry_run_checks_files_and_touches_nothing(data_dir, capsys):
    assert main(["--dry-run", "--data-dir", str(data_dir), "--catalog", "ecommerce"]) == 0
    out = capsys.readouterr().out
    assert "All files pass the landing contract" in out
    assert "CREATE VOLUME IF NOT EXISTS ecommerce.bronze.landing" in out


def test_a_bad_file_stops_before_databricks(tmp_path, capsys):
    for entity in HEADERS:
        write_csv(tmp_path, entity, tenants=("brand_a", "brand_zz"))
    assert main(["--data-dir", str(tmp_path)]) == 1
    assert "Stopped before touching Databricks" in capsys.readouterr().err


# ---------- A catalog created elsewhere (Terraform on Azure) ----------


def test_an_existing_catalog_is_not_created_again():
    statements = setup_statements("ecommerce", create_catalog=False)
    assert not any("CREATE CATALOG" in s for s in statements)
    assert statements[0].startswith("CREATE SCHEMA IF NOT EXISTS ecommerce.bronze")


def shown(*catalogs):
    return SimpleNamespace(
        statement_id="s",
        status=SimpleNamespace(state=SimpleNamespace(value="SUCCEEDED"), error=None),
        manifest=SimpleNamespace(schema=SimpleNamespace(columns=[SimpleNamespace(name="catalog")])),
        result=SimpleNamespace(data_array=[[c] for c in catalogs]),
    )


def test_catalog_exists_reads_show_catalogs():
    client = client_with(shown("ecommerce"))
    assert catalog_exists(client, "wh", "ecommerce") is True
    assert client.statement_execution.executed == ["SHOW CATALOGS LIKE 'ecommerce'"]


def test_a_missing_or_merely_similar_catalog_does_not_count():
    assert catalog_exists(client_with(shown()), "wh", "ecommerce") is False
    assert catalog_exists(client_with(shown("ecommerce_old")), "wh", "ecommerce") is False


def test_catalog_exists_refuses_an_unsafe_name():
    with pytest.raises(SetupError):
        catalog_exists(client_with(), "wh", "x'; DROP CATALOG main --")
