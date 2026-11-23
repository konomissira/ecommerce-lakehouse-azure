"""Load the landing CSVs into Bronze Delta tables, then reconcile the row counts.

    python -m lakehouse.bronze             # create tables, COPY INTO, reconcile
    python -m lakehouse.bronze --plan      # print the SQL only

Bronze keeps the data exactly as it arrived:

- every source column is a STRING. Typing happens in Silver, where a bad value can be
  flagged instead of silently lost on the way in;
- three load-metadata columns say where each row came from and when:
  `_source_file`, `_source_modified_at` and `_ingested_at`.

COPY INTO remembers which files it has already loaded, so running this twice does
not duplicate rows. A malformed CSV row fails the load (FAILFAST) rather than being
loaded as nulls.

After loading, Bronze's row count per tenant must equal the count the landing
contract measured on the local files. A mismatch exits with an error.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from lakehouse.landing import COLUMN_ORDER, LandingContractError
from lakehouse.setup_workspace import (
    LANDING_VOLUME,
    CheckedFile,
    SetupError,
    check_all,
    check_identifier,
    fetch_rows,
    make_client,
    pick_warehouse,
    run_sql,
)

METADATA_COLUMNS = (
    ("_source_file", "STRING", "_metadata.file_path"),
    ("_source_modified_at", "TIMESTAMP", "_metadata.file_modification_time"),
    ("_ingested_at", "TIMESTAMP", "current_timestamp()"),
)


def table_name(catalog: str, entity: str) -> str:
    return f"{check_identifier(catalog)}.bronze.{entity}"


def create_table_sql(catalog: str, entity: str) -> str:
    columns = [f"{c} STRING" for c in COLUMN_ORDER[entity]]
    columns += [f"{name} {kind}" for name, kind, _ in METADATA_COLUMNS]
    body = ",\n  ".join(columns)
    return (
        f"CREATE TABLE IF NOT EXISTS {table_name(catalog, entity)} (\n  {body}\n)\n"
        f"COMMENT 'Raw {entity} exactly as landed: all strings, plus load metadata'"
    )


def copy_into_sql(catalog: str, entity: str) -> str:
    source = f"/Volumes/{check_identifier(catalog)}/bronze/{LANDING_VOLUME}/{entity}/"
    selected = [*COLUMN_ORDER[entity]]
    selected += [f"{expression} AS {name}" for name, _, expression in METADATA_COLUMNS]
    body = ",\n    ".join(selected)
    return (
        f"COPY INTO {table_name(catalog, entity)}\n"
        f"FROM (\n  SELECT\n    {body}\n  FROM '{source}'\n)\n"
        "FILEFORMAT = CSV\n"
        "PATTERN = '*.csv'\n"
        "FORMAT_OPTIONS ('header' = 'true', 'inferSchema' = 'false', 'mode' = 'FAILFAST', "
        "'escape' = '\"')\n"
        "COPY_OPTIONS ('mergeSchema' = 'false')"
    )


def count_sql(catalog: str, entity: str) -> str:
    return (
        f"SELECT tenant_id, count(*) AS row_count FROM {table_name(catalog, entity)} "
        "GROUP BY tenant_id ORDER BY tenant_id"
    )


def reconcile(expected: dict[str, int], rows: list[dict[str, str | None]]) -> dict[str, int]:
    """Return {tenant: Bronze count - local count} for every tenant that differs."""
    actual = {r["tenant_id"]: int(r["row_count"] or 0) for r in rows}
    tenants = set(expected) | {t for t in actual if t is not None}
    return {
        t: actual.get(t, 0) - expected.get(t, 0)
        for t in sorted(tenants)
        if actual.get(t, 0) != expected.get(t, 0)
    }


def load(client: Any, warehouse_id: str, catalog: str, checked: list[CheckedFile]) -> bool:
    """Create, load and reconcile every entity. Return True if every count matches."""
    all_match = True
    for item in checked:
        run_sql(client, warehouse_id, create_table_sql(catalog, item.entity))
        copied = fetch_rows(run_sql(client, warehouse_id, copy_into_sql(catalog, item.entity)))
        inserted = (copied[0].get("num_inserted_rows") if copied else None) or "0"

        counts = fetch_rows(run_sql(client, warehouse_id, count_sql(catalog, item.entity)))
        differences = reconcile(item.rows_per_tenant, counts)
        status = "OK" if not differences else f"MISMATCH {differences}"
        print(f"  {item.entity:<10} inserted {int(inserted):>7,}  reconcile: {status}")
        all_match = all_match and not differences
    return all_match


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--catalog", default=os.environ.get("LAKEHOUSE_CATALOG", "ecommerce"))
    parser.add_argument("--data-dir", type=Path, default=Path("data_generator"))
    parser.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE"))
    parser.add_argument("--warehouse-id", default=os.environ.get("DATABRICKS_WAREHOUSE_ID"))
    parser.add_argument("--plan", action="store_true", help="print the SQL and stop")
    args = parser.parse_args(argv)

    try:
        checked = check_all(args.data_dir, args.catalog)
    except (SetupError, LandingContractError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1

    if args.plan:
        for item in checked:
            print(f"-- {item.entity}")
            print(create_table_sql(args.catalog, item.entity) + ";\n")
            print(copy_into_sql(args.catalog, item.entity) + ";\n")
        return 0

    try:
        client = make_client(args.profile)
        warehouse_id = pick_warehouse(client, args.warehouse_id)
        print(f"Loading Bronze with SQL warehouse {warehouse_id}")
        ok = load(client, warehouse_id, args.catalog, checked)
    except SetupError as exc:
        print(f"\nBronze load failed: {exc}", file=sys.stderr)
        return 1

    if not ok:
        print(
            "\nBronze does not match the local files. If you regenerated the data under the "
            "same file names, COPY INTO skipped them as already loaded: see the handbook.",
            file=sys.stderr,
        )
        return 1
    print("\nDone: every Bronze table matches the landing files, tenant by tenant.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
