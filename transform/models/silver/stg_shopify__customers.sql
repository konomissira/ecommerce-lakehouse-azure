-- Silver: one row per customer per tenant, typed and deduplicated.
-- See stg_shopify__orders.sql for how try_cast and _cast_errors work.

with source as (
    select * from {{ source('bronze', 'customers') }}
),

latest as (
    select *
    from source
    qualify row_number() over (
        partition by tenant_id, customer_id
        order by _ingested_at desc, _source_file desc
    ) = 1
),

typed as (
    select
        customer_id,
        tenant_id,
        first_name,
        last_name,
        lower(trim(email))                          as email,  -- normalise for dedup / joins
        city,
        try_cast(created_at as timestamp)           as created_at,
        to_date(try_cast(created_at as timestamp))  as signup_date,

        filter(array(
            case when created_at is not null and try_cast(created_at as timestamp) is null then 'created_at' end
        ), column_name -> column_name is not null) as _cast_errors,

        _source_file,
        _ingested_at
    from latest
)

select * from typed
