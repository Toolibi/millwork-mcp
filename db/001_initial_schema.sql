-- applied to Supabase project millwork-mcp on 2026-10-26

-- Pricing reference data
create table public.materials (
  code text primary key,
  description text not null,
  thickness numeric(6,4) not null,
  sheet_length numeric(7,3) not null default 97,
  sheet_width numeric(7,3) not null default 49,
  sheet_cost numeric(10,2) not null,
  grained boolean not null default false,
  updated_at timestamptz not null default now()
);

create table public.hardware (
  code text primary key,
  description text not null,
  unit_cost numeric(10,2) not null,
  updated_at timestamptz not null default now()
);

-- Single-row shop settings (labor rates, kerf, markup)
create table public.shop_settings (
  id int primary key default 1 check (id = 1),
  kerf numeric(6,4) not null default 0.125,
  trim numeric(6,4) not null default 0.25,
  edgeband_per_lf numeric(8,3) not null,
  edgeband_waste numeric(5,3) not null default 0.10,
  overhead_pct numeric(5,3) not null default 0.15,
  markup_pct numeric(5,3) not null default 0.30,
  labor_rate_per_hr numeric(8,2) not null,
  assembly_hr_per_box numeric(6,3) not null,
  hr_per_door numeric(6,3) not null,
  hr_per_drawer numeric(6,3) not null,
  machine_hr_per_sheet numeric(6,3) not null,
  edgeband_hr_per_lf numeric(7,4) not null,
  updated_at timestamptz not null default now()
);

-- Jobs and their cabinet schedules
create table public.jobs (
  id uuid primary key default gen_random_uuid(),
  job_number text unique not null,
  name text not null,
  source_drawing text,
  case_material text references public.materials(code),
  back_material text references public.materials(code),
  door_material text references public.materials(code),
  status text not null default 'draft' check (status in ('draft','reviewed','estimated','approved','released')),
  created_at timestamptz not null default now()
);

create table public.cabinets (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.jobs(id) on delete cascade,
  tag text not null,
  type text not null check (type in ('base','wall','tall','drawer_base','sink_base')),
  width numeric(7,3) not null check (width > 0),
  height numeric(7,3) not null check (height > 0),
  depth numeric(7,3) not null check (depth > 0),
  qty int not null default 1 check (qty > 0),
  doors int not null default 0,
  drawers int not null default 0,
  adj_shelves int not null default 0,
  notes text,
  extracted_by text not null default 'human' check (extracted_by in ('human','ai','ai_reviewed')),
  unique (job_id, tag)
);
create index cabinets_job_id_idx on public.cabinets(job_id);

-- Every estimate run is kept, so AI-assisted vs manual accuracy can be measured
create table public.estimates (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.jobs(id) on delete cascade,
  created_at timestamptz not null default now(),
  engine_version text not null,
  sheet_count int not null,
  edgeband_lf numeric(9,2) not null,
  labor_hours numeric(9,2) not null,
  direct_cost numeric(12,2) not null,
  sell_price numeric(12,2) not null,
  review_flags jsonb not null default '[]',
  detail jsonb not null,
  manual_estimate numeric(12,2),
  manual_minutes int,
  ai_minutes int
);
create index estimates_job_id_idx on public.estimates(job_id);

-- Locked down: only the server (service role) reads/writes; no anonymous access.
alter table public.materials enable row level security;
alter table public.hardware enable row level security;
alter table public.shop_settings enable row level security;
alter table public.jobs enable row level security;
alter table public.cabinets enable row level security;
alter table public.estimates enable row level security;
