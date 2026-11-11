import psycopg

with psycopg.connect(DSN) as conn:
    rows = conn.execute("SELECT id, amount, created_at FROM events WHERE created_at > now() - interval '1 day'").fetchall()
