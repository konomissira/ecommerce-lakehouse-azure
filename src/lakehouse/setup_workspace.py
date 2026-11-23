"""Set up the Databricks workspace and load the raw CSVs into the landing volume.

    python -m lakehouse.setup_workspace            # create objects, check and upload
    python -m lakehouse.setup_workspace --dry-run  # check the CSVs and print the plan only

What it creates (all idempotent, so running it twice is safe):

    <catalog>                     the project's catalog
    <catalog>.bronze              raw data, exactly as it arrived
    <catalog>.silver              cleaned and typed
    <catalog>.gold                business-ready marts
    <catalog>.bronze.landing      a volume holding the raw CSV files

Every local CSV is checked against the landing contract (path, header, every
tenant id) before anything is uploaded. One bad file stops the whole upload, so
the landing volume never holds a half-good batch.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lakehouse.landing import (
    LandingContractError,
    check_header,
    check_tenant,
    parse_landing_path,
)

SCHEMAS = {
    "bronze": "Raw data exactly as it arrived, plus load metadata",
    "silver": "Cleaned, typed and deduplicated",
    "gold": "Business-ready marts for reporting and governed questions",
}
LANDING_VOLUME = "landing"

# Free Edition creates a catalog called "workspace". It already exists, so we never
# try to create it.
PREEXISTING_CATALOGS = frozenset({"workspace"})

IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
GENERATED_FILE = re.compile(r"^shopify_(customers|orders|products|refunds)_bronze\.csv$")


class SetupError(RuntimeError):
    """Something in the workspace setup failed."""


# ---------------------------------------------------------------- SQL


def check_identifier(name: str) -> str:
    """Catalog names go into SQL, so only plain identifiers are allowed."""
    if not IDENTIFIER.fullmatch(name or ""):
        raise SetupError(f"invalid catalog name {name!r}: use lower-case letters, digits and _")
    return name


def setup_statements(catalog: str) -> list[str]:
    catalog = check_identifier(catalog)
    statements = []
    if catalog not in PREEXISTING_CATALOGS:
        statements.append(
            f"CREATE CATALOG IF NOT EXISTS {catalog} COMMENT 'Multi-tenant ecommerce KPI lakehouse'"
        )
    for schema, comment in SCHEMAS.items():
        statements.append(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema} COMMENT '{comment}'")
    statements.append(
        f"CREATE VOLUME IF NOT EXISTS {catalog}.bronze.{LANDING_VOLUME} "
        "COMMENT 'Raw CSV files, checked against the landing contract before upload'"
    )
    return statements


# ---------------------------------------------------------------- local files


@dataclass(frozen=True)
class CheckedFile:
    local_path: Path
    entity: str
    target_path: str
    rows: int
    rows_per_tenant: dict[str, int]


def landing_target(catalog: str, entity: str, file_name: str) -> str:
    return f"/Volumes/{catalog}/bronze/{LANDING_VOLUME}/{entity}/{file_name}"


def check_local_file(path: Path, catalog: str) -> CheckedFile:
    """Apply the full landing contract to one generated CSV."""
    match = GENERATED_FILE.fullmatch(path.name)
    if not match:
        raise LandingContractError(f"unexpected file name {path.name!r}")
    entity = match.group(1)

    target = landing_target(catalog, entity, path.name)
    parse_landing_path(target)  # the target must satisfy the same contract

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        check_header(entity, reader.fieldnames or [])
        tenants: Counter[str] = Counter()
        for line_number, row in enumerate(reader, start=2):
            try:
                tenants[check_tenant(row["tenant_id"])] += 1
            except LandingContractError as exc:
                raise LandingContractError(f"{path.name}, line {line_number}: {exc}") from exc

    rows = sum(tenants.values())
    if rows == 0:
        raise LandingContractError(f"{path.name}: no data rows")
    return CheckedFile(path, entity, target, rows, dict(sorted(tenants.items())))


def check_all(data_dir: Path, catalog: str) -> list[CheckedFile]:
    """Check every generated CSV; fail if any entity is missing or duplicated."""
    files = sorted(p for p in data_dir.glob("*.csv") if GENERATED_FILE.fullmatch(p.name))
    checked = [check_local_file(p, catalog) for p in files]
    found = Counter(c.entity for c in checked)
    missing = sorted({"customers", "orders", "products", "refunds"} - set(found))
    if missing:
        raise LandingContractError(f"no file for {missing} in {data_dir}; run `make data` first")
    return checked


# ---------------------------------------------------------------- Databricks


def _state(response: Any) -> str:
    state = response.status.state
    return getattr(state, "value", str(state))


def run_sql(
    client: Any,
    warehouse_id: str,
    statement: str,
    *,
    poll_seconds: float = 2.0,
    timeout_seconds: float = 300.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Run one statement on a SQL warehouse and wait for it, raising on failure.

    A stopped serverless warehouse starts on the first statement, which can take a
    little while, hence the polling.
    """
    response = client.statement_execution.execute_statement(
        statement=statement, warehouse_id=warehouse_id, wait_timeout="30s"
    )
    waited = 0.0
    while _state(response) in {"PENDING", "RUNNING"}:
        if waited >= timeout_seconds:
            raise SetupError(f"timed out after {timeout_seconds:.0f}s: {statement}")
        sleep(poll_seconds)
        waited += poll_seconds
        response = client.statement_execution.get_statement(response.statement_id)

    if _state(response) != "SUCCEEDED":
        error = getattr(response.status, "error", None)
        message = getattr(error, "message", None) or _state(response)
        raise SetupError(f"{message}\n  while running: {statement}")


def pick_warehouse(client: Any, warehouse_id: str | None = None) -> str:
    if warehouse_id:
        return warehouse_id
    warehouses = list(client.warehouses.list())
    if not warehouses:
        raise SetupError("no SQL warehouse found in this workspace")
    starter = [w for w in warehouses if "starter" in (w.name or "").lower()]
    return (starter or warehouses)[0].id


def upload(client: Any, checked: list[CheckedFile]) -> None:
    for item in checked:
        client.files.create_directory(item.target_path.rsplit("/", 1)[0])
        with item.local_path.open("rb") as contents:
            client.files.upload(item.target_path, contents, overwrite=True)


def make_client(profile: str | None) -> Any:
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise SetupError("databricks-sdk is not installed: run `make setup`") from exc
    return WorkspaceClient(profile=profile) if profile else WorkspaceClient()


# ---------------------------------------------------------------- CLI


def describe(checked: list[CheckedFile]) -> str:
    lines = []
    for c in checked:
        per_tenant = ", ".join(f"{t} {n:,}" for t, n in c.rows_per_tenant.items())
        lines.append(f"  {c.entity:<10} {c.rows:>7,} rows ({per_tenant})  ->  {c.target_path}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--catalog", default=os.environ.get("LAKEHOUSE_CATALOG", "ecommerce"))
    parser.add_argument("--data-dir", type=Path, default=Path("data_generator"))
    parser.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE"))
    parser.add_argument("--warehouse-id", default=os.environ.get("DATABRICKS_WAREHOUSE_ID"))
    parser.add_argument("--dry-run", action="store_true", help="check files, print the plan")
    args = parser.parse_args(argv)

    try:
        statements = setup_statements(args.catalog)
        checked = check_all(args.data_dir, args.catalog)
    except (SetupError, LandingContractError) as exc:
        print(f"Stopped before touching Databricks: {exc}", file=sys.stderr)
        return 1

    print("All files pass the landing contract:")
    print(describe(checked))
    if args.dry_run:
        print("\nWould run:")
        print("\n".join(f"  {s};" for s in statements))
        return 0

    try:
        client = make_client(args.profile)
        warehouse_id = pick_warehouse(client, args.warehouse_id)
        print(f"\nUsing SQL warehouse {warehouse_id}")
        for statement in statements:
            print(f"  {statement.split(' COMMENT')[0]}")
            run_sql(client, warehouse_id, statement)
        upload(client, checked)
    except SetupError as exc:
        print(f"\nSetup failed: {exc}", file=sys.stderr)
        return 1

    print(f"\nDone: {len(checked)} files uploaded to /Volumes/{args.catalog}/bronze/landing/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
