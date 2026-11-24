-- Silver must hold exactly one row per distinct key that Bronze holds, per tenant.
-- Fewer means rows were lost; more means deduplication failed.
-- Returns one row per (entity, tenant) that differs; no rows = pass.

with bronze as (
    select 'customers' as entity, tenant_id, count(distinct customer_id) as row_count
    from {{ source('bronze', 'customers') }} group by 1, 2
    union all
    select 'orders', tenant_id, count(distinct order_id)
    from {{ source('bronze', 'orders') }} group by 1, 2
    union all
    select 'products', tenant_id, count(distinct product_id)
    from {{ source('bronze', 'products') }} group by 1, 2
    union all
    select 'refunds', tenant_id, count(distinct refund_id)
    from {{ source('bronze', 'refunds') }} group by 1, 2
),

silver as (
    select 'customers' as entity, tenant_id, count(*) as row_count
    from {{ ref('stg_shopify__customers') }} group by 1, 2
    union all
    select 'orders', tenant_id, count(*)
    from {{ ref('stg_shopify__orders') }} group by 1, 2
    union all
    select 'products', tenant_id, count(*)
    from {{ ref('stg_shopify__products') }} group by 1, 2
    union all
    select 'refunds', tenant_id, count(*)
    from {{ ref('stg_shopify__refunds') }} group by 1, 2
)

select
    coalesce(b.entity, s.entity)       as entity,
    coalesce(b.tenant_id, s.tenant_id) as tenant_id,
    b.row_count                        as bronze_keys,
    s.row_count                        as silver_rows
from bronze b
full outer join silver s
    on  b.entity = s.entity
    and b.tenant_id = s.tenant_id
where coalesce(b.row_count, -1) != coalesce(s.row_count, -1)
