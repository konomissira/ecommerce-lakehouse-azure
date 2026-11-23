from types import SimpleNamespace

import pytest
from test_setup_workspace import HEADERS, write_csv

from lakehouse.bronze import (
    copy_into_sql,
    count_sql,
    create_table_sql,
    load,
    main,
    reconcile,
)
from lakehouse.landing import COLUMN_ORDER
from lakehouse.setup_workspace import SetupError, check_all, fetch_rows

# ---------- SQL ----------


@pytest.mark.parametrize("entity", sorted(COLUMN_ORDER))
def test_bronze_tables_keep_every_source_column_as_a_string(entity):
    sql = create_table_sql("ecommerce", entity)
    assert sql.startswith(f"CREATE TABLE IF NOT EXISTS ecommerce.bronze.{entity} (")
    for column in COLUMN_ORDER[entity]:
        assert f"  {column} STRING" in sql
    for column in ("_source_file STRING", "_source_modified_at TIMESTAMP", "_ingested_at"):
        assert column in sql


def test_source_columns_keep_the_generators_order():
    sql = create_table_sql("ecommerce", "orders")
    positions = [sql.index(f"  {c} STRING") for c in COLUMN_ORDER["orders"]]
    assert positions == sorted(positions)


def test_copy_into_reads_the_entitys_landing_folder_strictly():
    sql = copy_into_sql("ecommerce", "refunds")
    assert sql.startswith("COPY INTO ecommerce.bronze.refunds")
    assert "FROM '/Volumes/ecommerce/bronze/landing/refunds/'" in sql
    assert "'inferSchema' = 'false'" in sql, "Bronze never guesses types"
    assert "'mode' = 'FAILFAST'" in sql, "a malformed row fails the load"
    assert "'mergeSchema' = 'false'" in sql, "an unexpected column never widens the table"
    assert "'escape' = '\"'" in sql, "quotes are escaped the way pandas writes them"
    assert "_metadata.file_path AS _source_file" in sql


def test_counts_are_grouped_by_tenant():
    assert "GROUP BY tenant_id" in count_sql("ecommerce", "orders")


@pytest.mark.parametrize("build", [create_table_sql, copy_into_sql, count_sql])
def test_an_unsafe_catalog_never_reaches_the_sql(build):
    with pytest.raises(SetupError):
        build("x; DROP CATALOG main --", "orders")


# ---------- Reconciliation ----------


def test_matching_counts_reconcile():
    rows = [{"tenant_id": "brand_a", "row_count": "2"}, {"tenant_id": "brand_b", "row_count": "1"}]
    assert reconcile({"brand_a": 2, "brand_b": 1}, rows) == {}


def test_extra_rows_in_bronze_are_reported():
    rows = [{"tenant_id": "brand_a", "row_count": "4"}, {"tenant_id": "brand_b", "row_count": "1"}]
    assert reconcile({"brand_a": 2, "brand_b": 1}, rows) == {"brand_a": 2}


def test_a_tenant_missing_from_bronze_is_reported():
    rows = [{"tenant_id": "brand_a", "row_count": "2"}]
    assert reconcile({"brand_a": 2, "brand_b": 1}, rows) == {"brand_b": -1}


def test_a_tenant_bronze_has_but_the_files_dont_is_reported():
    rows = [{"tenant_id": "brand_a", "row_count": "2"}, {"tenant_id": "brand_x", "row_count": "5"}]
    assert reconcile({"brand_a": 2}, rows) == {"brand_x": 5}


# ---------- Reading results ----------


def finished(columns=(), rows=()):
    return SimpleNamespace(
        statement_id="s",
        status=SimpleNamespace(state=SimpleNamespace(value="SUCCEEDED"), error=None),
        manifest=SimpleNamespace(
            schema=SimpleNamespace(columns=[SimpleNamespace(name=c) for c in columns])
        ),
        result=SimpleNamespace(data_array=[list(r) for r in rows]),
    )


def test_fetch_rows_pairs_values_with_column_names():
    response = finished(("tenant_id", "row_count"), [("brand_a", "3"), ("brand_b", None)])
    assert fetch_rows(response) == [
        {"tenant_id": "brand_a", "row_count": "3"},
        {"tenant_id": "brand_b", "row_count": None},
    ]


def test_fetch_rows_with_no_result_is_empty():
    assert fetch_rows(SimpleNamespace(manifest=None, result=None)) == []


# ---------- The load, with a fake warehouse ----------


class FakeWarehouse:
    """Answers each statement like Databricks would, from a table of counts."""

    def __init__(self, counts):
        self.counts = counts  # {entity: [(tenant, count), ...]}
        self.statements = []

    def execute_statement(self, statement, warehouse_id, wait_timeout):
        self.statements.append(statement)
        if statement.startswith("COPY INTO"):
            return finished(("num_affected_rows", "num_inserted_rows"), [("3", "3")])
        if statement.startswith("SELECT tenant_id"):
            entity = statement.split("bronze.")[1].split()[0]
            return finished(("tenant_id", "row_count"), self.counts[entity])
        return finished()


@pytest.fixture
def checked(tmp_path):
    for entity in HEADERS:
        write_csv(tmp_path, entity)  # brand_a 2, brand_b 1 in every file
    return check_all(tmp_path, "ecommerce")


def test_a_clean_load_creates_copies_and_reconciles(checked, capsys):
    warehouse = FakeWarehouse({e: [("brand_a", "2"), ("brand_b", "1")] for e in HEADERS})
    client = SimpleNamespace(statement_execution=warehouse)
    assert load(client, "wh", "ecommerce", checked) is True
    kinds = [s.split()[0] for s in warehouse.statements]
    assert kinds == ["CREATE", "COPY", "SELECT"] * 4
    assert capsys.readouterr().out.count("reconcile: OK") == 4


def test_a_count_mismatch_fails_the_load(checked, capsys):
    counts = {e: [("brand_a", "2"), ("brand_b", "1")] for e in HEADERS}
    counts["orders"] = [("brand_a", "4"), ("brand_b", "1")]  # loaded twice?
    client = SimpleNamespace(statement_execution=FakeWarehouse(counts))
    assert load(client, "wh", "ecommerce", checked) is False
    assert "MISMATCH {'brand_a': 2}" in capsys.readouterr().out


def test_plan_prints_the_sql_without_databricks(tmp_path, capsys):
    for entity in HEADERS:
        write_csv(tmp_path, entity)
    assert main(["--plan", "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.count("COPY INTO ecommerce.bronze.") == 4
