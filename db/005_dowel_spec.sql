-- applied to Supabase project millwork-mcp on 2026-10-26
-- Shop dowel spec: 8mm in 3/4", 5mm in 1/2" and 3/8"; 32mm, 96mm, then every 96mm
create table public.dowel_spec (
  min_thickness numeric(6,4) primary key,
  dia_mm numeric(4,1) not null,
  hardware_code text not null references public.hardware(code)
);
alter table public.dowel_spec enable row level security;

update public.hardware set code = 'dowel_8x35', description = '8 x 35mm fluted dowel (3/4" material)' where code = 'dowel';
insert into public.hardware (code, description, unit_cost) values
 ('dowel_5x25','5 x 25mm fluted dowel (1/2" material)',0.02),
 ('dowel_5x20','5 x 20mm fluted dowel (3/8" material)',0.02);
insert into public.dowel_spec values (0.70, 8, 'dowel_8x35'), (0.45, 5, 'dowel_5x25'), (0.375, 5, 'dowel_5x20');

alter table public.shop_settings
  add column dowel_first_mm numeric(5,1) not null default 32,
  add column dowel_second_mm numeric(5,1) not null default 96,
  add column dowel_pitch_mm numeric(5,1) not null default 96;

insert into public.materials (code, description, thickness, sheet_length, sheet_width, sheet_cost, grained) values
 ('MEL-WHT-12','1/2" white melamine PB',0.5,97,49,46.00,false),
 ('MEL-WHT-38','3/8" white melamine',0.375,97,49,40.00,false);
