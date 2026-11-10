-- Copyright (c) 2026 Prince Boafo. All rights reserved. PolyForm Noncommercial 1.0.0.
-- applied to Supabase project millwork-mcp on 2026-10-26
insert into public.materials (code, description, thickness, sheet_length, sheet_width, sheet_cost, grained) values
 ('PLY-34','3/4" hardwood plywood (nailers)',0.75,96,48,62.00,true),
 ('HPL-WHT','HPL laminate, white, for pre-lam finished ends',0.039,97,49,48.00,false);

alter table public.jobs
  add column finished_end_method text not null default 'prelam' check (finished_end_method in ('prelam','exposed_material')),
  add column finished_end_laminate text references public.materials(code),
  add column nailer_material text not null default 'PLY-34' references public.materials(code);

alter table public.cabinets add column against_wall boolean not null default true;

alter table public.shop_settings
  add column nailer_w numeric(6,3) not null default 4.0,
  add column prelam_allowance numeric(6,3) not null default 0.5,
  add column hr_per_prelam_panel numeric(6,3) not null default 0.30;

alter table public.estimates add column prelam_panels int not null default 0;
