-- Copyright (c) 2026 Prince Boafo. All rights reserved. PolyForm Noncommercial 1.0.0.
-- applied to Supabase project millwork-mcp on 2026-10-26
alter table public.shop_settings add column dowel_closing_mm numeric(5,1) not null default 32;
comment on column public.shop_settings.dowel_closing_mm is 'Closing dowel distance in front of the back of the joint (the back dado on bottoms/tops)';
