"""Static checks on the dbt project: they run without Databricks or dbt itself.

dbt only finds most mistakes when it talks to the warehouse. These tests catch
the ones we can see in the files: BigQuery syntax left over from the port,
models without tests, secrets in the profile.
"""

import re
from pathlib import Path

import pytest
import yaml

DBT = Path(__file__).resolve().parents[1] / "transform"
MODELS = DBT / "models"
# Only the project's own files: dbt writes target/ and dbt_packages/ when it runs,
# and those hold compiled copies and other people's code.
OWN_FOLDERS = ("models", "macros", "tests", "profiles")
SQL_FILES = sorted(p for d in OWN_FOLDERS for p in (DBT / d).glob("**/*.sql"))
YAML_FILES = sorted(
    [p for d in OWN_FOLDERS for p in (DBT / d).glob("**/*.yml") if p.is_file()]
    + [DBT / "dbt_project.yml", DBT / "packages.yml"]
)

# BigQuery-only syntax that would fail, or silently mean something else, on Databricks.
BIGQUERY_ONLY = {
    r"\bsafe_divide\s*\(": "use try_divide",
    r"\bsafe_cast\s*\(": "use try_cast",
    r"\bint64\b": "use int / bigint",
    r"\bfloat64\b": "use double, or decimal for money",
    r"\bnumeric\b": "use decimal(18, 2)",
    r"\bformat_date\s*\(": "use date_format",
    r"\bparse_(date|timestamp)\s*\(": "use to_date / to_timestamp",
    r"ref\(\s*'shopify_\w+_bronze'\s*\)": "Bronze is a source now, not a seed",
}


def yaml_of(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def documented_models() -> dict[str, dict]:
    found = {}
    for path in MODELS.glob("**/schema.yml"):
        for model in yaml_of(path).get("models", []):
            found[model["name"]] = model
    return found


def test_the_project_has_the_ported_models():
    names = sorted(p.stem for p in MODELS.glob("**/*.sql"))
    assert names == [
        "mart_customer_retention",
        "mart_daily_revenue",
        "mart_product_performance",
        "stg_shopify__customers",
        "stg_shopify__orders",
        "stg_shopify__products",
        "stg_shopify__refunds",
    ]


@pytest.mark.parametrize("path", YAML_FILES, ids=lambda p: str(p.relative_to(DBT)))
def test_every_yaml_file_parses(path):
    assert isinstance(yaml_of(path), dict)


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: str(p.relative_to(DBT)))
def test_no_bigquery_only_syntax_is_left(path):
    # Comments may mention the old names; only code counts.
    code = "\n".join(line.split("--")[0] for line in path.read_text().splitlines())
    code = re.sub(r"\{#.*?#\}", "", code, flags=re.S).lower()
    found = [hint for pattern, hint in BIGQUERY_ONLY.items() if re.search(pattern, code)]
    assert not found, f"{path.name}: {found}"


@pytest.mark.parametrize("path", sorted((MODELS / "silver").glob("*.sql")), ids=lambda p: p.stem)
def test_silver_reads_bronze_dedups_and_tracks_failed_casts(path):
    sql = path.read_text()
    assert "source('bronze'," in sql
    assert "row_number() over" in sql, "Silver keeps one row per key"
    assert "_cast_errors" in sql


def test_every_model_is_documented_and_tested():
    documented = documented_models()
    for path in MODELS.glob("**/*.sql"):
        model = documented.get(path.stem)
        assert model, f"{path.stem} is missing from schema.yml"
        assert model.get("description"), f"{path.stem} has no description"
        has_tests = model.get("tests") or any(c.get("tests") for c in model.get("columns", []))
        assert has_tests, f"{path.stem} has no tests"


@pytest.mark.parametrize(
    "name", [f"stg_shopify__{e}" for e in ("customers", "orders", "products", "refunds")]
)
def test_every_silver_model_checks_for_failed_casts(name):
    tests = documented_models()[name].get("tests", [])
    expressions = [
        t["dbt_utils.expression_is_true"]["arguments"]["expression"]
        for t in tests
        if isinstance(t, dict) and "dbt_utils.expression_is_true" in t
    ]
    assert "size(_cast_errors) = 0" in expressions


def test_every_tenant_column_is_checked_against_the_known_tenants():
    for name, model in documented_models().items():
        tenant = next(c for c in model["columns"] if c["name"] == "tenant_id")
        accepted = [t for t in tenant["tests"] if isinstance(t, dict) and "accepted_values" in t]
        assert accepted, f"{name}.tenant_id has no accepted_values test"
        assert accepted[0]["accepted_values"]["arguments"]["values"] == ["brand_a", "brand_b"]


def test_the_profile_holds_no_secrets():
    output = yaml_of(DBT / "profiles" / "profiles.yml")["ecommerce_lakehouse"]["outputs"]["dev"]
    for key in ("host", "http_path", "token"):
        assert "env_var(" in output[key], f"{key} must come from an environment variable"
    assert output["catalog"] == "ecommerce"


def test_no_profile_sits_in_the_project_folder_itself():
    # The Databricks job generates its own profile; one here could be used instead.
    assert not (DBT / "profiles.yml").exists()


def test_models_land_in_the_medallion_schemas_exactly():
    config = yaml_of(DBT / "dbt_project.yml")["models"]["ecommerce_lakehouse"]
    assert config["silver"]["+schema"] == "silver"
    assert config["gold"]["+schema"] == "gold"
    macro = (DBT / "macros" / "generate_schema_name.sql").read_text()
    assert "custom_schema_name | trim" in macro, "no <target>_gold prefixing"


def test_reconciliation_tests_exist():
    names = {p.stem for p in (DBT / "tests").glob("*.sql")}
    assert {
        "assert_silver_keys_reconcile_to_bronze",
        "assert_gold_revenue_reconciles_to_silver",
    } <= names
