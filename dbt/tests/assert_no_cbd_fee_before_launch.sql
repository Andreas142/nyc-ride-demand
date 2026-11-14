-- No trip can carry a congestion relief fee before the programme started.
select *
from {{ ref('stg_trips') }}
where pickup_at < cast('{{ var("cbd_fee_start") }}' as timestamp)
  and cbd_congestion_fee > 0
