"""Static checks on databricks.yml: the job's shape, without a workspace."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = yaml.safe_load((ROOT / "databricks.yml").read_text())
JOB = BUNDLE["resources"]["jobs"]["lakehouse_pipeline"]
TASKS = {t["task_key"]: t for t in JOB["tasks"]}


def test_ingest_runs_first_and_dbt_only_after_it():
    assert set(TASKS) == {"bronze_autoloader", "dbt_build"}
    assert "depends_on" not in TASKS["bronze_autoloader"]
    assert TASKS["dbt_build"]["depends_on"] == [{"task_key": "bronze_autoloader"}]


def test_the_ingest_task_runs_our_autoloader_file():
    task = TASKS["bronze_autoloader"]["spark_python_task"]
    assert (ROOT / task["python_file"]).is_file()
    assert "{{job.parameters.include_existing_files}}" in task["parameters"]


def test_dbt_builds_and_tests_on_the_sql_warehouse():
    task = TASKS["dbt_build"]["dbt_task"]
    assert task["commands"] == ["dbt deps", "dbt build"]
    assert task["warehouse_id"] == "${var.warehouse_id}"
    assert task["catalog"] == "${var.catalog}"
    assert task["schema"] == "silver"
    assert (ROOT / "transform" / "dbt_project.yml").is_file()


def test_dbt_in_the_job_is_pinned_like_dbt_on_the_mac():
    envs = {e["environment_key"]: e["spec"] for e in JOB["environments"]}
    assert "dbt-core>=1.10,<2" in envs["dbt"]["dependencies"]
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert '"dbt-core>=1.10,<2"' in pyproject


def test_the_schedule_is_london_time_and_starts_paused():
    schedule = JOB["schedule"]
    assert schedule["timezone_id"] == "Europe/London"
    assert schedule["pause_status"] == "PAUSED"


def test_the_cutover_is_the_default():
    params = {p["name"]: p["default"] for p in JOB["parameters"]}
    assert params["include_existing_files"] == "false"


def test_one_run_at_a_time():
    assert JOB["max_concurrent_runs"] == 1


@pytest.mark.parametrize("secret", ["dapi", "token:", "password", "cloud.databricks.com"])
def test_no_secret_or_host_is_hardcoded(secret):
    assert secret not in (ROOT / "databricks.yml").read_text()


def test_the_upload_never_skips_dbt_files():
    # Exclude patterns follow .gitignore rules: unanchored ones match at any depth.
    # `tests/**` once skipped transform/tests, and the job silently ran 64 of 66 checks.
    import fnmatch

    dbt_files = [
        "transform/tests/assert_silver_keys_reconcile_to_bronze.sql",
        "transform/models/silver/schema.yml",
        "transform/dbt_project.yml",
    ]
    for pattern in BUNDLE.get("sync", {}).get("exclude", []):
        anchored = pattern.startswith("/")
        for path in dbt_files:
            candidates = [path] if anchored else [path, *_suffixes(path)]
            target = pattern.lstrip("/")
            assert not any(fnmatch.fnmatch(c, target) for c in candidates), (pattern, path)


def _suffixes(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[i:]) for i in range(1, len(parts))]
