# ecommerce-lakehouse-azure

**The same governed AI pattern, now on the enterprise stack UK employers use.**

The multi-tenant ecommerce KPI platform, rebuilt as a lakehouse on **Azure Databricks**.

The original runs on BigQuery with a dbt medallion pipeline. This port keeps the same
data (customers, orders, products and refunds for two brands), the same tenants and the
same Gold metrics, so the two can be compared honestly on cost, developer experience
and governance.

> Work in progress: built one feature branch at a time.

## Planned architecture

```
Mock Shopify CSV ──► Landing (volume / ADLS) ──► Bronze Delta ──► Silver ──► Gold marts
                      contract-checked            Auto Loader      dbt-databricks
                                                │
                                   Unity Catalog: catalog, schemas, grants,
                                   per-tenant row filters, lineage
Infrastructure: Terraform · Orchestration: Databricks Workflows · CI/CD: GitHub Actions
```

## Roadmap

Databricks work first on the free tier, then the Azure infrastructure in one short burst,
then the governed AI layer that ties this project to the rest of the portfolio.

| Branch | What it adds |
|---|---|
| `feature/01-repo-bootstrap` | Layout, tooling, CI, landing-path contract |
| `feature/02-databricks-setup` | Databricks Free Edition workspace: catalog, Bronze/Silver/Gold schemas, raw data in a volume |
| `feature/03-bronze-ingestion` | `COPY INTO` Bronze Delta tables (all strings + load metadata), reconciled per tenant |
| `feature/04-dbt-databricks` | Silver (typed, deduplicated) and Gold models ported to `dbt-databricks`, reconciled to Bronze |
| `feature/05-orchestration` | Databricks Asset Bundle: one job runs Auto Loader ingest, then dbt |
| `feature/06-azure-infra` | Terraform: resource group, ADLS Gen2, Azure Databricks workspace, budget alert |
| `feature/07-unity-catalog-azure` | Storage credential and external location on your own ADLS |
| `feature/08-governance` | Grants, per-tenant row filters, lineage |
| `feature/09-cicd` | GitHub Actions deploys the bundle (OIDC, no stored secrets) |
| `feature/10-governed-questions` | Natural-language questions over Gold: the model picks a vetted question, deterministic code runs parameterised SQL |
| `feature/11-comparison` | BigQuery vs Databricks on Azure: cost, developer experience, governance |

## Quick start

Requires Python 3.11+ and `make`.

```bash
make setup     # install the package and the git hooks
make check     # lint and tests, the same as CI
make data      # generate the mock Shopify data
```

### Databricks workspace

Requires the [Databricks CLI](https://docs.databricks.com/dev-tools/cli/install.html),
signed in once with `databricks auth login --host <your-workspace-url>`.

```bash
make workspace-plan   # check the CSVs against the contract; changes nothing
make workspace        # create catalog, schemas and volume; upload the CSVs
```

This creates `ecommerce.bronze`, `ecommerce.silver`, `ecommerce.gold` and the volume
`ecommerce.bronze.landing`. Set `LAKEHOUSE_CATALOG=workspace` to use the catalog that
Databricks Free Edition creates for you instead.

### Bronze

```bash
make bronze-plan      # print the CREATE TABLE and COPY INTO statements
make bronze           # load Bronze, then check row counts per tenant against the files
```

Bronze keeps every source column as a `STRING`, plus `_source_file`,
`_source_modified_at` and `_ingested_at`. `COPY INTO` skips files it has already
loaded, so the load is safe to re-run.

### Silver and Gold (dbt)

The dbt project lives in `transform/`. Copy `.env.example` to `.env` and fill in
`DBT_DATABRICKS_HOST` and `DBT_DATABRICKS_HTTP_PATH` (the SQL warehouse's
Connection details tab). There is no access token to store: `make dbt-build`
fetches a short-lived one from your Databricks CLI login.

```bash
make dbt-deps     # install dbt_utils
make dbt-parse    # check the project compiles, offline
make dbt-build    # build Silver and Gold, then run every test
```

| Layer | Models | Lands in |
|---|---|---|
| Silver | `stg_shopify__customers`, `__orders`, `__products`, `__refunds` (views) | `ecommerce.silver` |
| Gold | `mart_daily_revenue`, `mart_customer_retention`, `mart_product_performance` (Delta tables) | `ecommerce.gold` |

Silver applies types with `try_cast` and lists any value that failed in
`_cast_errors`, which a test requires to be empty, so nothing is lost silently. Two
reconciliation tests check that Silver holds every Bronze key once, and that Gold's
revenue adds up to Silver's, per tenant, to the penny.

## Landing-zone contract

Raw CSV files land as `<entity>/<file>.csv`, for `customers`, `orders`, `products` and
`refunds`. Before Bronze accepts a file, the contract checks:

- the path: a known entity folder and a `.csv` file;
- the header: exactly the expected columns, in any order;
- every `tenant_id`: well formed and one of the known tenants.

Anything that fails is rejected rather than guessed. See `src/lakehouse/landing.py`.
