-- applied to Supabase project millwork-mcp on 2026-10-26

create table public.joinery_methods (
  key text primary key,
  name text not null,
  pitch_mm numeric(6,1) not null check (mod(pitch_mm, 32) = 0),
  glued boolean not null,
  visible_on_face boolean not null,
  knock_down boolean not null,
  assembly_factor numeric(4,2) not null
);
alter table public.joinery_methods enable row level security;

insert into public.joinery_methods values
 ('dowel','8 x 35mm dowel, 32mm system',96,true,false,false,1.00),
 ('confirmat','7 x 50mm confirmat screw',160,false,true,false,0.75),
 ('cam_dowel','15mm cam & bolt with dowels',96,false,false,true,0.85),
 ('lamello_clamex','Lamello Clamex P-14',192,false,false,true,0.80);

alter table public.jobs
  add column exposed_material text references public.materials(code),
  add column exposed_back_material text references public.materials(code),
  add column joinery text not null default 'dowel' references public.joinery_methods(key),
  add column front_grain text not null default 'vertical' check (front_grain in ('vertical','horizontal')),
  add column grain_match boolean not null default true;

alter table public.cabinets drop constraint cabinets_type_check;
alter table public.cabinets
  add constraint cabinets_type_check check (type in ('base','wall','tall','drawer_base','sink_base','bookcase','microwave')),
  add column fixed_shelves int not null default 0,
  add column opening_height numeric(7,3) not null default 0,
  add column finished_ends text not null default '' check (finished_ends in ('','L','R','LR')),
  add column joinery text references public.joinery_methods(key);

alter table public.estimates add column grain_match_groups int not null default 0;
alter table public.shop_settings add column hr_per_grain_match_group numeric(6,3) not null default 0.35;
