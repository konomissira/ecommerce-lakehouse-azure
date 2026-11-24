-- Silver: one row per product per tenant, typed and deduplicated.
-- See stg_shopify__orders.sql for how try_cast and _cast_errors work.

with source as (
    select * from {{ source('bronze', 'products') }}
),

latest as (
    select *
    from source
    qualify row_number() over (
        partition by tenant_id, product_id
        order by _ingested_at desc, _source_file desc
    ) = 1
),

typed as (
    select
        product_id,
        tenant_id,
        sku,
        product_name,
        category,
        try_cast(price as decimal(18, 2)) as price,

        filter(array(
            case when price is not null and try_cast(price as decimal(18, 2)) is null then 'price' end
        ), column_name -> column_name is not null) as _cast_errors,

        _source_file,
        _ingested_at
    from latest
)

select * from typed
