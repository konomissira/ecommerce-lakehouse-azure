-- Gold layer: per-product performance by tenant.
--
-- Grain: one row per (tenant_id, product_id).
-- Ported from BigQuery: safe_divide became Databricks' try_divide (NULL, not an
-- error, when there are no orders).
-- Includes refund_rate so the dashboard can surface problem SKUs.
--
-- Note on refund_rate: we compute refunds/orders at the ORDER level, not at
-- the line-item level. Shopify allows partial refunds, so "refund_rate" here
-- means "% of orders containing this product that had any refund activity".
-- If you later need line-item-level attribution, you'll need Shopify's
-- refund_line_items entity.

with orders as (
    select * from {{ ref('stg_shopify__orders') }}
),

products as (
    select * from {{ ref('stg_shopify__products') }}
),

refunds as (
    select * from {{ ref('stg_shopify__refunds') }}
),

refunded_orders as (
    select distinct tenant_id, order_id
    from refunds
),

orders_with_refund_flag as (
    select
        o.tenant_id,
        o.order_id,
        o.product_id,
        o.quantity,
        o.net_sales,
        case when r.order_id is not null then 1 else 0 end as was_refunded
    from orders o
    left join refunded_orders r
        on  o.tenant_id = r.tenant_id
        and o.order_id  = r.order_id
),

per_product as (
    select
        tenant_id,
        product_id,
        count(distinct order_id)                                   as total_orders,
        sum(quantity)                                              as total_units_sold,
        sum(net_sales)                                             as total_net_sales,
        sum(was_refunded)                                          as orders_with_refund,
        try_divide(sum(was_refunded), count(distinct order_id))    as refund_rate
    from orders_with_refund_flag
    group by 1, 2
)

select
    p.tenant_id,
    p.product_id,
    pr.sku,
    pr.product_name,
    pr.category,
    pr.price,
    p.total_orders,
    p.total_units_sold,
    p.total_net_sales,
    p.orders_with_refund,
    p.refund_rate
from per_product p
join products pr
    on  p.tenant_id  = pr.tenant_id
    and p.product_id = pr.product_id
