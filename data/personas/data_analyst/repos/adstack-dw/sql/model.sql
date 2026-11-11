CREATE VIEW fact_sales AS SELECT order_id, territory_id, amount FROM staging.orders;
