-- Gold layer: daily revenue by tenant. The React dashboard's main chart.
--
-- Design decisions:
--   - Grain: one row per (tenant_id, activity_date).
--   - activity_date (NOT `date`, a reserved word in BigQuery and best avoided in
--     Databricks SQL too).
--   - Refunds are attributed to the day they were PROCESSED, not the day
--     the original order was placed. This is the convention most e-com
--     dashboards use because it matches cash-flow reality.
--   - We FULL OUTER JOIN orders and refunds so a day with refunds but no
--     orders still shows up (otherwise you hide negative-revenue days).

with orders as (
    select * from {{ ref('stg_shopify__orders') }}
),

refunds as (
    select * from {{ ref('stg_shopify__refunds') }}
),

daily_sales as (
    select
        tenant_id,
        order_date as activity_date,
        count(distinct order_id)    as total_orders,
        sum(gross_sales)            as total_gross_sales,
        sum(discount_amount)        as total_discounts,
        sum(net_sales)              as total_net_sales
    from orders
    group by 1, 2
),

daily_refunds as (
    select
        tenant_id,
        refund_date as activity_date,
        count(distinct refund_id)   as total_refund_events,
        sum(refund_amount)          as total_refund_amount
    from refunds
    group by 1, 2
),

combined as (
    select
        coalesce(s.tenant_id, r.tenant_id)         as tenant_id,
        coalesce(s.activity_date, r.activity_date) as activity_date,
        coalesce(s.total_orders, 0)                as total_orders,
        coalesce(s.total_gross_sales, 0)           as total_gross_sales,
        coalesce(s.total_discounts, 0)             as total_discounts,
        coalesce(s.total_net_sales, 0)             as total_net_sales,
        coalesce(r.total_refund_events, 0)         as total_refund_events,
        coalesce(r.total_refund_amount, 0)         as total_refunds
    from daily_sales s
    full outer join daily_refunds r
        on s.tenant_id = r.tenant_id
       and s.activity_date = r.activity_date
)

select
    tenant_id,
    activity_date,
    total_orders,
    total_gross_sales,
    total_discounts,
    total_net_sales,
    total_refund_events,
    total_refunds,
    (total_net_sales - total_refunds) as net_revenue
from combined
