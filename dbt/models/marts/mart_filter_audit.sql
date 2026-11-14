-- What each validity rule costs, per service and month. A dropped trip is charged to the
-- first rule it fails, so the columns add up: rows_in = sum(lost_*) + rows_kept.

{{ monthly_incremental(['service', 'source_month']) }}

select
    service,
    source_month,
    count(*) as rows_in,
    count(*) filter (where drop_wrong_month) as lost_wrong_month,
    count(*) filter (where not drop_wrong_month and drop_duration) as lost_duration,
    count(*) filter (where not (drop_wrong_month or drop_duration) and drop_distance) as lost_distance,
    count(*) filter (
        where not (drop_wrong_month or drop_duration or drop_distance) and drop_fare
    ) as lost_fare,
    count(*) filter (
        where not (drop_wrong_month or drop_duration or drop_distance or drop_fare)
    ) as rows_kept
from {{ ref('int_trips_flagged') }}
where source_month = '{{ month_var() }}'
group by all
