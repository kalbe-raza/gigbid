# adstack-dw

Marketing data warehouse. Built 2024. (Synthetic portfolio repository.)

## Architecture
Loaded the platforms into a warehouse and modelled spend and conversions with tested transformations.

## Tech
- dbt
- SQL
- ETL
- BigQuery
- Airbyte
- Looker Studio

## Layout
- `sql/model.sql`
- `ingestion/connections.yaml`
- `docs/looker_fields.md`
- `sql/cohorts.sql`

## Running
```bash
cp .env.example .env
make run
```
