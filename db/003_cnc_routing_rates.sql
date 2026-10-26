-- applied to Supabase project millwork-mcp on 2026-10-26
alter table public.shop_settings rename column machine_hr_per_sheet to saw_hr_per_sheet;
alter table public.shop_settings
  add column cnc_min_per_part numeric(6,2) not null default 1.5,
  add column cnc_sec_per_op numeric(6,2) not null default 2.5;
