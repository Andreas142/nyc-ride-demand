-- Every built service-month holds every mappable zone for every hour of the month.
with expected as (
    select count(*) as zones from {{ ref('stg_zones') }} where is_mappable
),

per_month as (
    select
        service,
        date_trunc('month', hour) as month_start,
        count(*) as n_rows
    from {{ ref('fct_zone_hour_demand') }}
    group by all
)

select p.*
from per_month p
cross join expected e
where p.n_rows <> e.zones * date_diff('hour', p.month_start, p.month_start + interval 1 month)
