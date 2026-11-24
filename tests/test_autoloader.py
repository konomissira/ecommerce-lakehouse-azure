import pytest

from lakehouse.autoloader import (
    check_batch,
    checkpoint_path,
    landing_path,
    parse_args,
    reader_options,
)
from lakehouse.landing import KNOWN_TENANTS, LandingContractError


def test_the_job_defaults_to_the_cutover():
    args = parse_args([])
    assert args.include_existing_files == "false"
    assert reader_options(args.include_existing_files)["cloudFiles.includeExistingFiles"] == "false"


def test_a_fresh_workspace_can_load_existing_files():
    assert parse_args(["--include-existing-files", "true"]).include_existing_files == "true"
    with pytest.raises(SystemExit):
        parse_args(["--include-existing-files", "yes"])


def test_reader_options_are_as_strict_as_copy_into():
    options = reader_options("false")
    assert options["cloudFiles.format"] == "csv"
    assert (
        options["cloudFiles.schemaEvolutionMode"] == "none"
    ), "the schema is given, never inferred"
    assert options["mode"] == "FAILFAST"
    assert options["header"] == "true"
    assert options["escape"] == '"'


def test_paths_follow_the_landing_layout():
    assert landing_path("ecommerce", "orders") == "/Volumes/ecommerce/bronze/landing/orders/"
    assert checkpoint_path("ecommerce", "orders") == (
        "/Volumes/ecommerce/bronze/checkpoints/autoloader_orders"
    )


def test_each_entity_has_its_own_checkpoint():
    paths = {
        checkpoint_path("ecommerce", e) for e in ("customers", "orders", "products", "refunds")
    }
    assert len(paths) == 4


def test_a_batch_of_known_tenants_passes():
    check_batch({"brand_a": 120, "brand_b": 80}, KNOWN_TENANTS)


@pytest.mark.parametrize("bad", ["brand_c", None, "Brand_A", "brand_a; DROP TABLE x"])
def test_one_bad_tenant_fails_the_whole_batch(bad):
    with pytest.raises(LandingContractError):
        check_batch({"brand_a": 120, bad: 1}, KNOWN_TENANTS)


def test_the_job_script_never_exits_with_systemexit():
    # Databricks treats any SystemExit in a Python file task as a failure, even 0.
    from pathlib import Path

    import lakehouse.autoloader as module

    entry = Path(module.__file__).read_text().split('if __name__ == "__main__":')[1]
    code_lines = [line for line in entry.splitlines() if not line.strip().startswith("#")]
    assert "SystemExit" not in "\n".join(code_lines)
