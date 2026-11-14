-- The audit charges each dropped trip to exactly one rule.
select *
from {{ ref('mart_filter_audit') }}
where rows_in <> lost_wrong_month + lost_duration + lost_distance + lost_fare + rows_kept
