"""Write .env.azure from what Terraform and Databricks report right now.

    python -m lakehouse.azure_env          # or: make azure-env

The Azure workspace is recreated every session (`make tf-apply`, then
`make tf-destroy`), and each time its URL and SQL warehouse ID change. Copying
them by hand every session is slow and error-prone, so this reads:

- the workspace URL from `terraform output`;
- the SQL warehouse from the workspace itself, through the `azure` CLI profile;

and writes the settings the Makefile needs when you run `make ... ENV=azure`.
The file holds no secrets: tokens are still fetched at run time.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from lakehouse.setup_workspace import SetupError, make_client, pick_warehouse

PROFILE = "azure"
TARGET = Path(".env.azure")


def host_without_scheme(url: str) -> str:
    host = url.strip().removeprefix("https://").removeprefix("http://").rstrip("/")
    if not host or "/" in host:
        raise SetupError(f"not a workspace URL: {url!r}")
    return host


def render(workspace_url: str, warehouse_id: str) -> str:
    """The contents of .env.azure."""
    if not warehouse_id or not warehouse_id.isalnum():
        raise SetupError(f"not a warehouse ID: {warehouse_id!r}")
    host = host_without_scheme(workspace_url)
    return (
        "# Written by `make azure-env` from Terraform and the Azure workspace.\n"
        "# Regenerate it after every `make tf-apply`: the URL and IDs change.\n"
        f"DATABRICKS_CONFIG_PROFILE={PROFILE}\n"
        f"DBT_DATABRICKS_HOST={host}\n"
        f"DBT_DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/{warehouse_id}\n"
        f"BUNDLE_VAR_warehouse_id={warehouse_id}\n"
    )


def terraform_output(name: str, infra: Path = Path("infra")) -> str:  # pragma: no cover
    result = subprocess.run(
        ["terraform", f"-chdir={infra}", "output", "-json", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SetupError(f"terraform output {name} failed; run `make tf-apply` first")
    return json.loads(result.stdout)


def main() -> int:  # pragma: no cover
    try:
        url = terraform_output("workspace_url")
        warehouse_id = pick_warehouse(make_client(PROFILE))
        TARGET.write_text(render(url, warehouse_id))
    except SetupError as exc:
        print(f"Could not write {TARGET}: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {TARGET} for {url} (warehouse {warehouse_id})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
