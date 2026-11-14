{# The month being built, as 'YYYY-MM'. Fails loudly when it was not passed. #}
{% macro month_var() %}
    {%- set month = var('month') -%}
    {%- if execute and month is none -%}
        {{ exceptions.raise_compiler_error("Pass the month to build, e.g. --vars '{month: \"2025-03\"}' (or use `make dbt-build MONTH=2025-03`).") }}
    {%- endif -%}
    {{ return(month) }}
{% endmacro %}

{# Config shared by the monthly marts: one month is replaced at a time, and a full
   refresh is refused because trip-level files of older months are pruned (ADR-007). #}
{% macro monthly_incremental(unique_key) %}
    {{ config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key=unique_key,
        full_refresh=false,
        on_schema_change='fail'
    ) }}
{% endmacro %}
