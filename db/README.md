# Database (Supabase / Postgres)

`001_initial_schema.sql` creates the pricing tables (materials, hardware, shop_settings),
jobs, cabinets and estimates. Row level security is on with no public policies, so only
the server (service role) can read or write.

The `estimates` table keeps every run plus `manual_estimate`, `manual_minutes` and
`ai_minutes`, which is how the accuracy and time-saved study is measured.
