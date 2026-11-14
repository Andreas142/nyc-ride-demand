-- Warning only: a service-month that loses more than 5% of its rows to the validity rules
-- deserves a look (and a line in the findings), not a silent pass.
{{ config(severity='warn') }}

select service, source_month, rows_in, rows_kept
from {{ ref('mart_filter_audit') }}
where rows_kept < 0.95 * rows_in
