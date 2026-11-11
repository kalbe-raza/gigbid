let
    Source = Sql.Database("dw", "sales"),
    Filtered = Table.SelectRows(Source, each [Date] >= #date(2023, 1, 1))
in
    Filtered
