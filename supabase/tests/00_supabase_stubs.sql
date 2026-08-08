-- =============================================================================
-- Local validation stubs — NOT part of the deployed migrations.
-- =============================================================================
-- Recreates just enough of Supabase's managed `auth` and `storage` schemas for
-- the migrations to be applied against a vanilla Postgres container, so schema
-- and policy syntax can be checked in CI without a live project.
-- =============================================================================

create role anon nologin;
create role authenticated nologin;
create role service_role nologin;

create schema if not exists auth;
create schema if not exists storage;

create table auth.users (
  id                 uuid primary key default gen_random_uuid(),
  email              text,
  raw_user_meta_data jsonb default '{}'::jsonb
);

-- Test harness swaps the acting user by setting `request.jwt.claim.sub`.
create or replace function auth.uid()
returns uuid
language sql
stable
as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
$$;

create table storage.buckets (
  id                 text primary key,
  name               text not null,
  public             boolean default false,
  file_size_limit    bigint,
  allowed_mime_types text[]
);

create table storage.objects (
  id        uuid primary key default gen_random_uuid(),
  bucket_id text references storage.buckets(id),
  name      text not null,
  owner     uuid
);

create or replace function storage.extension(name text)
returns text
language sql
immutable
as $$
  select lower(substring(name from '\.([^\.]+)$'));
$$;

alter table storage.objects enable row level security;
