"""Auto Loader: stream new landing files into Bronze, checking every batch first.

Runs as the first task of the Databricks job (see databricks.yml), on serverless
compute. For each entity it starts one Auto Loader stream with
`trigger(availableNow=True)`: process every new file, then stop. Progress is kept
in a checkpoint, so each file is loaded exactly once across runs.

Cutover from COPY INTO: Bronze already holds every file that `make bronze` loaded.
With `--include-existing-files false` (the job's default), Auto Loader skips the
files already in the landing volume and picks up only files that arrive later,
so nothing is loaded twice. On a fresh, empty workspace, run the job once with
`include_existing_files=true` instead.

Every micro-batch is checked before it is written: every tenant_id must be one of
the known tenants, as in the landing contract. One bad row fails the run, nothing
from that batch is written, and the checkpoint does not move, so the same files
are retried once fixed.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping

METADATA = ("_source_file", "_source_modified_at", "_ingested_at")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--catalog", default="ecommerce")
    parser.add_argument("--src", help="where the lakehouse package lives in the workspace")
    parser.add_argument("--include-existing-files", default="false", choices=["true", "false"])
    return parser.parse_args(argv)


def landing_path(catalog: str, entity: str) -> str:
    return f"/Volumes/{catalog}/bronze/landing/{entity}/"


def checkpoint_path(catalog: str, entity: str) -> str:
    return f"/Volumes/{catalog}/bronze/checkpoints/autoloader_{entity}"


def reader_options(include_existing_files: str) -> dict[str, str]:
    """Auto Loader options: the same strictness as COPY INTO in feature/03."""
    return {
        "cloudFiles.format": "csv",
        "cloudFiles.includeExistingFiles": include_existing_files,
        "cloudFiles.schemaEvolutionMode": "none",  # the schema is given, never inferred
        "header": "true",
        "mode": "FAILFAST",  # a malformed row fails the run
        "escape": '"',  # quotes as pandas writes them
        "pathGlobFilter": "*.csv",
    }


def check_batch(rows_per_tenant: Mapping[str | None, int], known: frozenset[str]) -> None:
    """Raise unless every tenant in the batch is well formed and known."""
    from lakehouse.landing import check_tenant

    for tenant in rows_per_tenant:
        check_tenant(tenant, known)  # type: ignore[arg-type]


def run(spark, catalog: str, include_existing_files: str) -> None:  # pragma: no cover
    """Start one availableNow stream per entity and wait for each to finish."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType, StructField, StructType

    from lakehouse.landing import COLUMN_ORDER, KNOWN_TENANTS
    from lakehouse.setup_workspace import check_identifier

    catalog = check_identifier(catalog)
    spark.sql(f"CREATE VOLUME IF NOT EXISTS {catalog}.bronze.checkpoints")

    for entity, columns in COLUMN_ORDER.items():
        table = f"{catalog}.bronze.{entity}"
        schema = StructType([StructField(c, StringType(), True) for c in columns])

        stream = (
            spark.readStream.format("cloudFiles")
            .options(**reader_options(include_existing_files))
            .schema(schema)
            .load(landing_path(catalog, entity))
            .select(
                *columns,
                F.col("_metadata.file_path").alias("_source_file"),
                F.col("_metadata.file_modification_time").alias("_source_modified_at"),
                F.current_timestamp().alias("_ingested_at"),
            )
        )

        def write_batch(batch, batch_id, table=table, entity=entity):
            counts = {
                r["tenant_id"]: r["count"] for r in batch.groupBy("tenant_id").count().collect()
            }
            check_batch(counts, KNOWN_TENANTS)
            batch.write.mode("append").saveAsTable(table)
            print(f"{entity}: batch {batch_id} appended {sum(counts.values()):,} rows {counts}")

        query = (
            stream.writeStream.foreachBatch(write_batch)
            .option("checkpointLocation", checkpoint_path(catalog, entity))
            .trigger(availableNow=True)
            .start()
        )
        query.awaitTermination()
        print(f"{entity}: up to date")


def main(argv: list[str] | None = None) -> None:  # pragma: no cover
    args = parse_args(argv)
    if args.src and args.src not in sys.path:
        sys.path.insert(0, args.src)  # the bundle syncs src/ to the workspace

    from pyspark.sql import SparkSession

    run(SparkSession.builder.getOrCreate(), args.catalog, args.include_existing_files)


if __name__ == "__main__":  # pragma: no cover
    # No `raise SystemExit(main())` here: Databricks runs Python file tasks in an
    # IPython session, where any SystemExit, even 0, counts as a failed task.
    # Success = finishing normally; a failure raises its own exception.
    main()
