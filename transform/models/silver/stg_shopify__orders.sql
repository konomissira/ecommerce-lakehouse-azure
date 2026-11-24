-- Silver: one row per order, typed and deduplicated.
--
-- Bronze keeps every value as a string. Types are applied here with try_cast,
-- which gives NULL instead of failing on a bad value. So that a bad value is
-- never lost silently, _cast_errors lists every column whose raw value was
-- present but could not be cast, and a test requires it to be empty.
--
-- Money is DECIMAL(18, 2), never FLOAT: rounding matters (GBP assumed).

with source as (
    select * from {{ source('bronze', 'orders') }}
),

latest as (
    -- If the same order was loaded more than once, keep the most recent load.
    select *
    from source
    qualify row_number() over (
        partition by tenant_id, order_id
        order by _ingested_at desc, _source_file desc
    ) = 1
),

typed as (
    select
        order_id,
        tenant_id,
        customer_id,
        product_id,

        -- Keep both the full timestamp and the date: marts group by date,
        -- anomaly investigation wants the timestamp.
        try_cast(order_date as timestamp)          as order_timestamp,
        to_date(try_cast(order_date as timestamp)) as order_date,

        try_cast(quantity as int)                  as quantity,
        try_cast(gross_sales as decimal(18, 2))    as gross_sales,
        try_cast(discount as decimal(18, 2))       as discount_amount,
        try_cast(net_sales as decimal(18, 2))      as net_sales,

        utm_source,

        filter(array(
            case when order_date  is not null and try_cast(order_date as timestamp)        is null then 'order_date'  end,
            case when quantity    is not null and try_cast(quantity as int)                is null then 'quantity'    end,
            case when gross_sales is not null and try_cast(gross_sales as decimal(18, 2))  is null then 'gross_sales' end,
            case when discount    is not null and try_cast(discount as decimal(18, 2))     is null then 'discount'    end,
            case when net_sales   is not null and try_cast(net_sales as decimal(18, 2))    is null then 'net_sales'   end
        ), column_name -> column_name is not null) as _cast_errors,

        _source_file,
        _ingested_at
    from latest
)

select * from typed
