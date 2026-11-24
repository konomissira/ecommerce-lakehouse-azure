-- Silver: one row per refund event, typed and deduplicated.
-- A single order can have several partial refunds.
-- See stg_shopify__orders.sql for how try_cast and _cast_errors work.

with source as (
    select * from {{ source('bronze', 'refunds') }}
),

latest as (
    select *
    from source
    qualify row_number() over (
        partition by tenant_id, refund_id
        order by _ingested_at desc, _source_file desc
    ) = 1
),

typed as (
    select
        refund_id,
        tenant_id,
        order_id,
        try_cast(refund_date as timestamp)          as refund_timestamp,
        to_date(try_cast(refund_date as timestamp)) as refund_date,
        try_cast(refund_amount as decimal(18, 2))   as refund_amount,
        reason                                      as refund_reason,

        filter(array(
            case when refund_date   is not null and try_cast(refund_date as timestamp)        is null then 'refund_date'   end,
            case when refund_amount is not null and try_cast(refund_amount as decimal(18, 2)) is null then 'refund_amount' end
        ), column_name -> column_name is not null) as _cast_errors,

        _source_file,
        _ingested_at
    from latest
)

select * from typed
