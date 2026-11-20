import pytest

from lakehouse.landing import (
    EXPECTED_COLUMNS,
    LandingContractError,
    LandingFile,
    check_header,
    check_tenant,
    parse_landing_path,
)

# ---------- Paths ----------


@pytest.mark.parametrize(
    "path",
    [
        "/Volumes/ecommerce/bronze/landing/orders/shopify_orders_bronze.csv",
        "abfss://landing@acct.dfs.core.windows.net/shopify/orders/shopify_orders_bronze.csv",
        "orders/shopify_orders_bronze.csv",
        "C:\\data\\landing\\orders\\shopify_orders_bronze.csv",
    ],
)
def test_valid_paths_give_entity_and_file(path):
    assert parse_landing_path(path) == LandingFile(
        entity="orders", file_name="shopify_orders_bronze.csv"
    )


@pytest.mark.parametrize("entity", ["customers", "orders", "products", "refunds"])
def test_every_entity_is_accepted(entity):
    assert parse_landing_path(f"landing/{entity}/x.csv").entity == entity


@pytest.mark.parametrize(
    "path",
    [
        "",
        "   ",
        "x.csv",  # no entity folder
        "landing/ad_spend/x.csv",  # not an entity this data has
        "landing/Orders/x.csv",  # case matters
        "landing/orders/x.json",  # wrong format
        "landing/orders/../x.csv",  # traversal changes the shape
    ],
)
def test_anything_off_layout_is_rejected(path):
    with pytest.raises(LandingContractError):
        parse_landing_path(path)


# ---------- Headers ----------


@pytest.mark.parametrize("entity", sorted(EXPECTED_COLUMNS))
def test_the_generators_real_headers_are_accepted(entity):
    check_header(entity, sorted(EXPECTED_COLUMNS[entity]))


def test_column_order_doesnt_matter_and_spaces_are_ignored():
    check_header(
        "refunds",
        [" reason", "refund_amount", "refund_date", "order_id", "tenant_id", "refund_id "],
    )


def test_a_missing_tenant_column_is_rejected():
    header = sorted(EXPECTED_COLUMNS["orders"] - {"tenant_id"})
    with pytest.raises(LandingContractError, match="tenant_id"):
        check_header("orders", header)


def test_an_unexpected_column_is_rejected():
    with pytest.raises(LandingContractError, match="unexpected"):
        check_header("products", [*EXPECTED_COLUMNS["products"], "cost_price"])


def test_a_duplicate_column_is_rejected():
    with pytest.raises(LandingContractError, match="duplicate"):
        check_header("products", [*EXPECTED_COLUMNS["products"], "price"])


def test_an_unknown_entity_header_is_rejected():
    with pytest.raises(LandingContractError):
        check_header("ad_spend", ["date", "spend"])


# ---------- Tenants ----------


@pytest.mark.parametrize("tenant", ["brand_a", "brand_b"])
def test_known_tenants_pass(tenant):
    assert check_tenant(tenant) == tenant


@pytest.mark.parametrize(
    "tenant",
    ["", None, "Brand_A", "brand-a", "1brand", "brand_a; DROP TABLE orders", "brand_c"],
)
def test_malformed_or_unknown_tenants_are_rejected(tenant):
    with pytest.raises(LandingContractError):
        check_tenant(tenant)
