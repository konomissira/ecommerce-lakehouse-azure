-- Gold's daily revenue must add up to Silver, per tenant, to the penny:
-- orders, net sales and refunds. A broken join or a dropped day shows up here.
-- Returns one row per tenant that differs; no rows = pass.

with silver_orders as (
    select tenant_id, count(*) as orders, sum(net_sales) as net_sales
    from {{ ref('stg_shopify__orders') }}
    group by 1
),

silver_refunds as (
    select tenant_id, sum(refund_amount) as refunds
    from {{ ref('stg_shopify__refunds') }}
    group by 1
),

gold as (
    select
        tenant_id,
        sum(total_orders)    as orders,
        sum(total_net_sales) as net_sales,
        sum(total_refunds)   as refunds
    from {{ ref('mart_daily_revenue') }}
    group by 1
),

silver as (
    select
        coalesce(o.tenant_id, r.tenant_id) as tenant_id,
        coalesce(o.orders, 0)              as orders,
        coalesce(o.net_sales, 0)           as net_sales,
        coalesce(r.refunds, 0)             as refunds
    from silver_orders o
    full outer join silver_refunds r on o.tenant_id = r.tenant_id
)

select
    coalesce(s.tenant_id, g.tenant_id) as tenant_id,
    s.orders    as silver_orders,    g.orders    as gold_orders,
    s.net_sales as silver_net_sales, g.net_sales as gold_net_sales,
    s.refunds   as silver_refunds,   g.refunds   as gold_refunds
from silver s
full outer join gold g on s.tenant_id = g.tenant_id
where coalesce(s.orders, -1) != coalesce(g.orders, -1)
   or abs(coalesce(s.net_sales, 0) - coalesce(g.net_sales, 0)) > 0.005
   or abs(coalesce(s.refunds, 0) - coalesce(g.refunds, 0)) > 0.005
   or s.tenant_id is null
   or g.tenant_id is null
