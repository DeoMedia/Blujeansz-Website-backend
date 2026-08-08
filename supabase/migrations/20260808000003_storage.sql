-- =============================================================================
-- BLUJEANSZ CMS — Supabase Storage
-- =============================================================================
-- One public bucket for website media. Reads are open (these images are served
-- on the public site); writes are restricted to active CMS users and policed
-- by both extension and MIME type.
-- =============================================================================

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'media',
  'media',
  true,
  10485760, -- 10 MB
  array['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
)
on conflict (id) do update
  set public             = excluded.public,
      file_size_limit    = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- -----------------------------------------------------------------------------
-- Object policies
-- -----------------------------------------------------------------------------

-- Anyone may read — the bucket backs public <img> tags.
drop policy if exists media_public_read on storage.objects;
create policy media_public_read on storage.objects
  for select to anon, authenticated
  using (bucket_id = 'media');

-- Any active CMS user may upload.
drop policy if exists media_authenticated_upload on storage.objects;
create policy media_authenticated_upload on storage.objects
  for insert to authenticated
  with check (
    bucket_id = 'media'
    and public.auth_is_at_least('author')
    and storage.extension(name) in ('jpg', 'jpeg', 'png', 'webp')
  );

-- Uploaders may replace their own objects; admins may replace anything.
drop policy if exists media_update on storage.objects;
create policy media_update on storage.objects
  for update to authenticated
  using (
    bucket_id = 'media'
    and (owner = auth.uid() or public.auth_is_at_least('admin'))
  )
  with check (
    bucket_id = 'media'
    and storage.extension(name) in ('jpg', 'jpeg', 'png', 'webp')
  );

drop policy if exists media_delete on storage.objects;
create policy media_delete on storage.objects
  for delete to authenticated
  using (
    bucket_id = 'media'
    and (owner = auth.uid() or public.auth_is_at_least('admin'))
  );
