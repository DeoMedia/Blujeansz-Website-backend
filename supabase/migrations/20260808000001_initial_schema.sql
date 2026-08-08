-- =============================================================================
-- BLUJEANSZ CMS — initial schema
-- =============================================================================
-- Creates the enums, tables, constraints and indexes backing the CMS.
-- RLS policies live in 20260808000002_rls_policies.sql — this file only enables
-- RLS so that no table is ever readable before its policies are installed.
-- =============================================================================

create extension if not exists "pgcrypto";
create extension if not exists "unaccent";

-- -----------------------------------------------------------------------------
-- Enums
-- -----------------------------------------------------------------------------
do $$ begin
  create type public.user_role as enum ('super_admin', 'admin', 'editor', 'author');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.user_status as enum ('active', 'invited', 'suspended');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.content_status as enum ('draft', 'in_review', 'scheduled', 'published', 'archived');
exception when duplicate_object then null; end $$;

-- -----------------------------------------------------------------------------
-- Shared helpers
-- -----------------------------------------------------------------------------

-- Keeps updated_at honest without trusting the client.
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- Slugify: "The Cultural Shifts Redefining African & Global Brands in 2026"
--       -> "the-cultural-shifts-redefining-african-global-brands-in-2026"
-- Accents are folded, ampersands and punctuation dropped, spaces collapsed.
create or replace function public.slugify(value text)
returns text
language sql
immutable
strict
as $$
  select trim(both '-' from
    regexp_replace(
      regexp_replace(
        lower(public.unaccent(value)),
        '[^a-z0-9]+', '-', 'g'
      ),
      '-{2,}', '-', 'g'
    )
  );
$$;

-- -----------------------------------------------------------------------------
-- profiles — CMS users, 1:1 with auth.users
-- -----------------------------------------------------------------------------
create table if not exists public.profiles (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null unique references auth.users(id) on delete cascade,
  first_name    text,
  last_name     text,
  email         text not null,
  avatar_url    text,
  role          public.user_role   not null default 'author',
  status        public.user_status not null default 'invited',
  -- Authors cannot publish unless this is explicitly granted (see RLS).
  can_publish   boolean not null default false,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  last_login_at timestamptz
);

create index if not exists profiles_user_id_idx on public.profiles (user_id);
create index if not exists profiles_role_idx    on public.profiles (role);
create index if not exists profiles_status_idx  on public.profiles (status);

-- -----------------------------------------------------------------------------
-- authors — bylines. An author does NOT need login access.
-- -----------------------------------------------------------------------------
create table if not exists public.authors (
  id                uuid primary key default gen_random_uuid(),
  profile_id        uuid references public.profiles(id) on delete set null,
  first_name        text not null,
  last_name         text not null,
  display_name      text not null,
  slug              text not null unique,
  job_title         text,
  short_bio         text,
  full_bio          text,
  profile_image_url text,
  email             text,
  linkedin_url      text,
  instagram_url     text,
  website_url       text,
  active            boolean not null default true,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create index if not exists authors_slug_idx    on public.authors (slug);
create index if not exists authors_active_idx  on public.authors (active);
create index if not exists authors_profile_idx on public.authors (profile_id);

-- -----------------------------------------------------------------------------
-- staff_members — the About/Team section. Does NOT imply CMS access.
-- -----------------------------------------------------------------------------
create table if not exists public.staff_members (
  id                uuid primary key default gen_random_uuid(),
  profile_id        uuid references public.profiles(id) on delete set null,
  first_name        text not null,
  last_name         text not null,
  slug              text not null unique,
  job_title         text,
  department        text,
  short_bio         text,
  full_bio          text,
  profile_image_url text,
  email             text,
  linkedin_url      text,
  instagram_url     text,
  website_url       text,
  display_order     integer not null default 0,
  featured          boolean not null default false,
  active            boolean not null default true,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create index if not exists staff_active_order_idx on public.staff_members (active, display_order);
create index if not exists staff_slug_idx         on public.staff_members (slug);
create index if not exists staff_featured_idx     on public.staff_members (featured);

-- -----------------------------------------------------------------------------
-- insight_categories
-- -----------------------------------------------------------------------------
create table if not exists public.insight_categories (
  id            uuid primary key default gen_random_uuid(),
  name          text not null unique,
  slug          text not null unique,
  description   text,
  display_order integer not null default 0,
  active        boolean not null default true,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index if not exists insight_categories_active_order_idx
  on public.insight_categories (active, display_order);

-- -----------------------------------------------------------------------------
-- media_assets — mirrors objects in the `media` storage bucket
-- -----------------------------------------------------------------------------
create table if not exists public.media_assets (
  id           uuid primary key default gen_random_uuid(),
  file_name    text not null,
  storage_path text not null unique,
  public_url   text not null,
  mime_type    text not null,
  file_size    bigint not null,
  width        integer,
  height       integer,
  alt_text     text,
  caption      text,
  uploaded_by  uuid references public.profiles(id) on delete set null,
  created_at   timestamptz not null default now(),

  constraint media_assets_mime_allowed check (
    mime_type in ('image/jpeg', 'image/jpg', 'image/png', 'image/webp')
  )
);

create index if not exists media_assets_uploaded_by_idx on public.media_assets (uploaded_by);
create index if not exists media_assets_created_at_idx  on public.media_assets (created_at desc);

-- -----------------------------------------------------------------------------
-- insights — blog posts
-- -----------------------------------------------------------------------------
create table if not exists public.insights (
  id                  uuid primary key default gen_random_uuid(),
  title               text not null,
  slug                text not null unique,
  excerpt             text,
  -- Structured block JSON. See docs/content-blocks.md for the block schema.
  content             jsonb not null default '[]'::jsonb,
  category_id         uuid references public.insight_categories(id) on delete set null,
  author_id           uuid references public.authors(id) on delete set null,
  featured_image_url  text,
  featured_image_alt  text,
  hero_image_position text not null default 'center',
  read_time_minutes   integer,
  status              public.content_status not null default 'draft',
  featured            boolean not null default false,

  -- SEO
  seo_title           text,
  meta_description    text,
  canonical_url       text,
  og_title            text,
  og_description      text,
  og_image_url        text,

  created_by          uuid references public.profiles(id) on delete set null,
  updated_by          uuid references public.profiles(id) on delete set null,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  published_at        timestamptz,
  scheduled_at        timestamptz,

  -- Content must be a JSON array of blocks, never a bare object or scalar.
  constraint insights_content_is_array check (jsonb_typeof(content) = 'array'),
  -- Published content must carry a publish timestamp, otherwise the public
  -- `published_at <= now()` filter would silently hide it forever.
  constraint insights_published_needs_date check (
    status <> 'published' or published_at is not null
  ),
  constraint insights_scheduled_needs_date check (
    status <> 'scheduled' or scheduled_at is not null
  )
);

create index if not exists insights_slug_idx        on public.insights (slug);
create index if not exists insights_status_idx      on public.insights (status);
create index if not exists insights_published_idx   on public.insights (published_at desc);
create index if not exists insights_author_idx      on public.insights (author_id);
create index if not exists insights_category_idx    on public.insights (category_id);
create index if not exists insights_featured_idx    on public.insights (featured);
create index if not exists insights_created_by_idx  on public.insights (created_by);
-- The hot path for every public listing query.
create index if not exists insights_public_feed_idx
  on public.insights (status, published_at desc)
  where status = 'published';

-- -----------------------------------------------------------------------------
-- case_studies
-- -----------------------------------------------------------------------------
create table if not exists public.case_studies (
  id                 uuid primary key default gen_random_uuid(),
  title              text not null,
  slug               text not null unique,
  client             text,
  industry           text,
  project_year       integer,
  location           text,
  summary            text,
  featured_image_url text,
  hero_image_url     text,

  -- Long-form narrative sections, stored as block JSON like insights.content.
  challenge           jsonb not null default '[]'::jsonb,
  strategic_approach  jsonb not null default '[]'::jsonb,
  solution            jsonb not null default '[]'::jsonb,
  execution           jsonb not null default '[]'::jsonb,
  results_summary     jsonb not null default '[]'::jsonb,

  client_quote       text,
  quote_attribution  text,
  status             public.content_status not null default 'draft',
  featured           boolean not null default false,

  -- SEO
  seo_title          text,
  meta_description   text,
  og_image_url       text,

  created_by         uuid references public.profiles(id) on delete set null,
  updated_by         uuid references public.profiles(id) on delete set null,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  published_at       timestamptz,
  scheduled_at       timestamptz,

  constraint case_studies_published_needs_date check (
    status <> 'published' or published_at is not null
  ),
  constraint case_studies_scheduled_needs_date check (
    status <> 'scheduled' or scheduled_at is not null
  )
);

create index if not exists case_studies_slug_idx      on public.case_studies (slug);
create index if not exists case_studies_status_idx    on public.case_studies (status);
create index if not exists case_studies_published_idx on public.case_studies (published_at desc);
create index if not exists case_studies_featured_idx  on public.case_studies (featured);
create index if not exists case_studies_public_feed_idx
  on public.case_studies (status, published_at desc)
  where status = 'published';

-- -----------------------------------------------------------------------------
-- case_study_metrics — flexible, never a fixed set of metric types
-- -----------------------------------------------------------------------------
create table if not exists public.case_study_metrics (
  id             uuid primary key default gen_random_uuid(),
  case_study_id  uuid not null references public.case_studies(id) on delete cascade,
  value          text not null,
  label          text not null,
  description    text,
  display_order  integer not null default 0,
  created_at     timestamptz not null default now()
);

create index if not exists case_study_metrics_parent_idx
  on public.case_study_metrics (case_study_id, display_order);

-- -----------------------------------------------------------------------------
-- services
-- -----------------------------------------------------------------------------
create table if not exists public.services (
  id            uuid primary key default gen_random_uuid(),
  name          text not null,
  slug          text not null unique,
  short_description text,
  description   text,
  icon          text,
  display_order integer not null default 0,
  active        boolean not null default true,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index if not exists services_active_order_idx on public.services (active, display_order);

-- -----------------------------------------------------------------------------
-- content_relationships — generic joins (related insights, case study services…)
-- -----------------------------------------------------------------------------
create table if not exists public.content_relationships (
  id                uuid primary key default gen_random_uuid(),
  source_type       text not null,
  source_id         uuid not null,
  target_type       text not null,
  target_id         uuid not null,
  relationship_type text not null default 'related',
  display_order     integer not null default 0,
  created_at        timestamptz not null default now(),

  constraint content_relationships_types_valid check (
    source_type in ('insight', 'case_study', 'service', 'author', 'staff_member')
    and target_type in ('insight', 'case_study', 'service', 'author', 'staff_member')
  ),
  constraint content_relationships_no_self check (
    not (source_type = target_type and source_id = target_id)
  ),
  unique (source_type, source_id, target_type, target_id, relationship_type)
);

create index if not exists content_relationships_source_idx
  on public.content_relationships (source_type, source_id);
create index if not exists content_relationships_target_idx
  on public.content_relationships (target_type, target_id);

-- -----------------------------------------------------------------------------
-- site_settings — key/value config surfaced to the public site
-- -----------------------------------------------------------------------------
create table if not exists public.site_settings (
  id          uuid primary key default gen_random_uuid(),
  key         text not null unique,
  value       jsonb not null default '{}'::jsonb,
  description text,
  -- Only settings flagged public are readable by anonymous visitors.
  is_public   boolean not null default false,
  updated_by  uuid references public.profiles(id) on delete set null,
  updated_at  timestamptz not null default now()
);

create index if not exists site_settings_key_idx on public.site_settings (key);

-- -----------------------------------------------------------------------------
-- updated_at triggers
-- -----------------------------------------------------------------------------
do $$
declare
  t text;
begin
  foreach t in array array[
    'profiles', 'authors', 'staff_members', 'insight_categories',
    'insights', 'case_studies', 'services', 'site_settings'
  ]
  loop
    execute format('drop trigger if exists set_%1$s_updated_at on public.%1$s', t);
    execute format(
      'create trigger set_%1$s_updated_at before update on public.%1$s
       for each row execute function public.set_updated_at()', t
    );
  end loop;
end $$;

-- -----------------------------------------------------------------------------
-- Provision a profile whenever an admin invites/creates an auth user.
-- There is no public registration; this simply keeps profiles in sync.
-- -----------------------------------------------------------------------------
create or replace function public.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (user_id, email, first_name, last_name, role, status)
  values (
    new.id,
    new.email,
    nullif(new.raw_user_meta_data ->> 'first_name', ''),
    nullif(new.raw_user_meta_data ->> 'last_name', ''),
    coalesce(
      nullif(new.raw_user_meta_data ->> 'role', '')::public.user_role,
      'author'
    ),
    'invited'
  )
  on conflict (user_id) do nothing;

  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_auth_user();

-- -----------------------------------------------------------------------------
-- Enable RLS everywhere. Policies are added in the next migration; until then
-- these tables are closed to anon/authenticated by default.
-- -----------------------------------------------------------------------------
alter table public.profiles              enable row level security;
alter table public.authors               enable row level security;
alter table public.staff_members         enable row level security;
alter table public.insight_categories    enable row level security;
alter table public.insights              enable row level security;
alter table public.case_studies          enable row level security;
alter table public.case_study_metrics    enable row level security;
alter table public.media_assets          enable row level security;
alter table public.services              enable row level security;
alter table public.content_relationships enable row level security;
alter table public.site_settings         enable row level security;
