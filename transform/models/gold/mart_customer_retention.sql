-- Gold layer: daily new vs returning customers by tenant.
--
-- Definition: a customer is "new" on the day of their FIRST order (per tenant).
-- Every subsequent order from that customer is "returning".
-- This is the Shopify-standard definition and matches what agencies report.
--
-- Grain: one row per (tenant_id, activity_date).

with orders as (
    select * from {{ ref('stg_shopify__orders') }}
),

first_order as (
    select
        tenant_id,
        customer_id,
        min(order_date) as first_order_date
    from orders
    group by 1, 2
),

tagged as (
    select
        o.tenant_id,
        o.order_date,
        o.customer_id,
        case
            when o.order_date = f.first_order_date then 'new'
            else 'returning'
        end as customer_type
    from orders o
    join first_order f
        on  o.tenant_id = f.tenant_id
        and o.customer_id = f.customer_id
)

select
    tenant_id,
    order_date as activity_date,
    count(distinct case when customer_type = 'new'       then customer_id end) as new_customers,
    count(distinct case when customer_type = 'returning' then customer_id end) as returning_customers,
    count(distinct customer_id)                                                as total_active_customers
from tagged
group by 1, 2
