{#
  By default dbt writes a model with `+schema: gold` to `<target schema>_gold`,
  for example `silver_gold`. We want the medallion schemas created in
  feature/02 exactly: ecommerce.silver and ecommerce.gold.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
