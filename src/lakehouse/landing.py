"""The landing-zone contract: what a raw file must look like before Bronze accepts it.

Raw Shopify-shaped CSV exports land in one fixed layout:

    <landing root>/<entity>/<file>.csv

for example

    /Volumes/ecommerce/bronze/landing/orders/shopify_orders_2026-11-20.csv

Each file can hold rows for several tenants. The tenant is the `tenant_id` column,
exactly as in the original BigQuery project, so this module checks three things:

1. the path: a known entity folder and a .csv file;
2. the header: exactly the expected columns for that entity, in any order;
3. each tenant id: a safe identifier from the list of known tenants.

Anything that fails is rejected, never guessed. A file in the wrong folder or a row
with an unknown tenant can't end up counted under the wrong brand.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

# The columns each Bronze entity must have, exactly as the generator writes them.
EXPECTED_COLUMNS: dict[str, frozenset[str]] = {
    "customers": frozenset(
        {"customer_id", "tenant_id", "first_name", "last_name", "email", "city", "created_at"}
    ),
    "orders": frozenset(
        {
            "order_id",
            "tenant_id",
            "customer_id",
            "product_id",
            "order_date",
            "quantity",
            "gross_sales",
            "discount",
            "net_sales",
            "utm_source",
        }
    ),
    "products": frozenset({"product_id", "tenant_id", "sku", "product_name", "category", "price"}),
    "refunds": frozenset(
        {"refund_id", "tenant_id", "order_id", "refund_date", "refund_amount", "reason"}
    ),
}

ENTITIES = frozenset(EXPECTED_COLUMNS)

KNOWN_TENANTS = frozenset({"brand_a", "brand_b"})

# Lower-case, starts with a letter, letters/digits/underscores, at most 63 characters.
# Safe for Unity Catalog identifiers and row filters.
TENANT_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,62}$")


class LandingContractError(ValueError):
    """A file, header or tenant id breaks the landing-zone contract."""


@dataclass(frozen=True)
class LandingFile:
    entity: str
    file_name: str


def parse_landing_path(path: str) -> LandingFile:
    """Return the entity and file name for a landing path (local, /Volumes/... or abfss://)."""
    if not path or not path.strip():
        raise LandingContractError("empty path")

    raw = urlparse(path).path if "://" in path else path
    parts = PurePosixPath(raw.replace("\\", "/")).parts
    if len(parts) < 2:
        raise LandingContractError(f"expected <entity>/<file>.csv, got {path!r}")

    entity, file_name = parts[-2], parts[-1]
    if entity not in ENTITIES:
        raise LandingContractError(
            f"unknown entity folder {entity!r} in {path!r}; expected one of {sorted(ENTITIES)}"
        )
    if not file_name.endswith(".csv"):
        raise LandingContractError(f"expected a .csv file, got {file_name!r}")
    return LandingFile(entity=entity, file_name=file_name)


def check_header(entity: str, header: Iterable[str]) -> None:
    """Raise unless the header has exactly the expected columns for the entity."""
    if entity not in ENTITIES:
        raise LandingContractError(f"unknown entity {entity!r}")

    columns = [c.strip() for c in header]
    duplicates = sorted({c for c in columns if columns.count(c) > 1})
    if duplicates:
        raise LandingContractError(f"{entity}: duplicate columns {duplicates}")

    found, expected = set(columns), EXPECTED_COLUMNS[entity]
    missing, unexpected = sorted(expected - found), sorted(found - expected)
    if missing or unexpected:
        raise LandingContractError(f"{entity}: missing {missing}, unexpected {unexpected}")


def check_tenant(tenant_id: str, known: frozenset[str] = KNOWN_TENANTS) -> str:
    """Return the tenant id if it's well formed and known; raise otherwise."""
    if not TENANT_PATTERN.fullmatch(tenant_id or ""):
        raise LandingContractError(f"malformed tenant id {tenant_id!r}")
    if tenant_id not in known:
        raise LandingContractError(f"unknown tenant {tenant_id!r}; known: {sorted(known)}")
    return tenant_id
