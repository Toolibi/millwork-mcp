-- Copyright (c) 2026 Prince Boafo. All rights reserved. PolyForm Noncommercial 1.0.0.
-- applied to Supabase project millwork-mcp on 2026-10-26
-- Machine profiles: how the shop cuts and bores parts. Every setting carries its source.
create table public.machine_profiles (
  key text primary key,
  name text not null,
  type text not null check (type in ('nested','panel_saw')),
  is_default boolean not null default false
);
create table public.machine_settings (
  machine_key text not null references public.machine_profiles(key) on delete cascade,
  setting text not null,
  value text not null,
  source text not null default 'assumed' check (source in ('shop','program','assumed')),
  confirmed_at timestamptz,
  primary key (machine_key, setting)
);
alter table public.machine_profiles enable row level security;
alter table public.machine_settings enable row level security;

insert into public.machine_profiles values
 ('stiles_nested','Stiles / HOMAG nested-based CNC router (woodWOP)','nested',true),
 ('panel_saw_cnc','Panel saw + point-to-point CNC','panel_saw',false);
insert into public.machine_settings (machine_key, setting, value) values
 ('stiles_nested','tool_dia','0.375'), ('stiles_nested','part_spacing','0.5'),
 ('stiles_nested','sheet_margin','0.25'), ('stiles_nested','cut_through','0.008'),
 ('stiles_nested','contour_tool','101'), ('stiles_nested','dado_tool','102'),
 ('stiles_nested','clamex_tool','103'), ('stiles_nested','drill_mode','LS'),
 ('stiles_nested','horizontal_boring','false'), ('stiles_nested','small_part_in','4.0'),
 ('stiles_nested','feed_ipm','700'), ('stiles_nested','load_min_per_sheet','3.0'),
 ('stiles_nested','sec_per_bore','2.0'), ('stiles_nested','edge_bore_sec','4.0'),
 ('stiles_nested','hr_per_prelam_sheet','0.5');

alter table public.estimates add column machine_key text references public.machine_profiles(key);
