-- =============================================================================
-- BLUJEANSZ CMS — Row Level Security
-- =============================================================================
-- Permissions are enforced here, at the database. The React admin UI only
-- *reflects* these rules; it never is the rule. Anything the UI forgets to
-- hide is still refused by Postgres.
--
-- Role hierarchy: super_admin > admin > editor > author
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Identity helpers
--
-- These are SECURITY DEFINER on purpose: a policy on `profiles` that itself
-- selects from `profiles` would recurse infinitely. Reading the caller's own
-- role through a definer function breaks that cycle. search_path is pinned so
-- the function body cannot be hijacked by a caller-controlled search_path.
-- -----------------------------------------------------------------------------

create or replace function public.auth_role()
returns public.user_role
language sql
stable
security definer
set search_path = public
as $$
  select role
  from public.profiles
  where user_id = auth.uid()
    and status = 'active'
  limit 1;
$$;

create or replace function public.auth_profile_id()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  select id
  from public.profiles
  where user_id = auth.uid()
    and status = 'active'
  limit 1;
$$;

create or replace function public.role_rank(r public.user_role)
returns integer
language sql
immutable
as $$
  select case r
    when 'super_admin' then 4
    when 'admin'       then 3
    when 'editor'      then 2
    when 'author'      then 1
    else 0
  end;
$$;

-- True when the caller is an active CMS user of at least the given role.
create or replace function public.auth_is_at_least(minimum public.user_role)
returns boolean
language sql
stable
as $$
  select coalesce(public.role_rank(public.auth_role()) >= public.role_rank(minimum), false);
$$;

-- Admins and super admins publish freely; editors and authors only when the
-- `can_publish` flag has been explicitly granted on their profile.
create or replace function public.auth_can_publish()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(
    (
      select role in ('super_admin', 'admin') or can_publish
      from public.profiles
      where user_id = auth.uid()
        and status = 'active'
      limit 1
    ),
    false
  );
$$;

-- The single definition of "the public may see this".
create or replace function public.is_publicly_visible(
  s public.content_status,
  published timestamptz
)
returns boolean
language sql
immutable
as $$
  select s = 'published' and published is not null and published <= now();
$$;

-- -----------------------------------------------------------------------------
-- Privilege-escalation guard
--
-- A user may edit their own profile (name, avatar). They may NOT hand
-- themselves a better role, reactivate a suspended account, or grant
-- themselves publish rights. Only an admin acting on someone else can.
-- -----------------------------------------------------------------------------
create or replace function public.guard_profile_privileges()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if (new.role, new.status, new.can_publish)
     is distinct from (old.role, old.status, old.can_publish)
  then
    -- Changing privileges requires admin rights...
    if not public.auth_is_at_least('admin') then
      raise exception 'insufficient privileges to change role, status or publish rights';
    end if;

    -- ...and nobody may escalate their own.
    if old.user_id = auth.uid() then
      raise exception 'you cannot change your own role, status or publish rights';
    end if;

    -- Only a super admin may mint another super admin.
    if new.role = 'super_admin' and public.auth_role() <> 'super_admin' then
      raise exception 'only a super admin can grant the super_admin role';
    end if;
  end if;

  return new;
end;
$$;

drop trigger if exists guard_profile_privileges on public.profiles;
create trigger guard_profile_privileges
  before update on public.profiles
  for each row execute function public.guard_profile_privileges();

-- =============================================================================
-- profiles
-- =============================================================================
drop policy if exists profiles_select on public.profiles;
create policy profiles_select on public.profiles
  for select to authenticated
  using (user_id = auth.uid() or public.auth_is_at_least('admin'));

drop policy if exists profiles_insert on public.profiles;
create policy profiles_insert on public.profiles
  for insert to authenticated
  with check (public.auth_is_at_least('admin'));

drop policy if exists profiles_update on public.profiles;
create policy profiles_update on public.profiles
  for update to authenticated
  using (user_id = auth.uid() or public.auth_is_at_least('admin'))
  with check (user_id = auth.uid() or public.auth_is_at_least('admin'));

drop policy if exists profiles_delete on public.profiles;
create policy profiles_delete on public.profiles
  for delete to authenticated
  using (public.auth_role() = 'super_admin' and user_id <> auth.uid());

-- =============================================================================
-- authors — public reads active authors; admins manage them
-- =============================================================================
drop policy if exists authors_public_select on public.authors;
create policy authors_public_select on public.authors
  for select to anon
  using (active = true);

-- Any active CMS user needs the full list to populate byline pickers.
drop policy if exists authors_staff_select on public.authors;
create policy authors_staff_select on public.authors
  for select to authenticated
  using (active = true or public.auth_is_at_least('author'));

drop policy if exists authors_write on public.authors;
create policy authors_write on public.authors
  for all to authenticated
  using (public.auth_is_at_least('admin'))
  with check (public.auth_is_at_least('admin'));

-- =============================================================================
-- staff_members
-- =============================================================================
drop policy if exists staff_public_select on public.staff_members;
create policy staff_public_select on public.staff_members
  for select to anon
  using (active = true);

drop policy if exists staff_staff_select on public.staff_members;
create policy staff_staff_select on public.staff_members
  for select to authenticated
  using (active = true or public.auth_is_at_least('author'));

drop policy if exists staff_write on public.staff_members;
create policy staff_write on public.staff_members
  for all to authenticated
  using (public.auth_is_at_least('admin'))
  with check (public.auth_is_at_least('admin'));

-- =============================================================================
-- insight_categories
-- =============================================================================
drop policy if exists categories_public_select on public.insight_categories;
create policy categories_public_select on public.insight_categories
  for select to anon
  using (active = true);

drop policy if exists categories_staff_select on public.insight_categories;
create policy categories_staff_select on public.insight_categories
  for select to authenticated
  using (public.auth_is_at_least('author'));

drop policy if exists categories_write on public.insight_categories;
create policy categories_write on public.insight_categories
  for all to authenticated
  using (public.auth_is_at_least('admin'))
  with check (public.auth_is_at_least('admin'));

-- =============================================================================
-- insights
--
-- anon                : published only, and only once published_at has passed
-- author              : own drafts + everything published; may not publish
-- editor              : all content, may not publish unless granted
-- admin / super_admin : everything
-- =============================================================================
drop policy if exists insights_public_select on public.insights;
create policy insights_public_select on public.insights
  for select to anon
  using (public.is_publicly_visible(status, published_at));

drop policy if exists insights_staff_select on public.insights;
create policy insights_staff_select on public.insights
  for select to authenticated
  using (
    public.is_publicly_visible(status, published_at)
    or public.auth_is_at_least('editor')
    or created_by = public.auth_profile_id()
  );

drop policy if exists insights_insert on public.insights;
create policy insights_insert on public.insights
  for insert to authenticated
  with check (
    public.auth_is_at_least('author')
    and created_by = public.auth_profile_id()
    -- Creating something already marked published requires publish rights.
    and (status <> 'published' or public.auth_can_publish())
  );

drop policy if exists insights_update on public.insights;
create policy insights_update on public.insights
  for update to authenticated
  using (
    public.auth_is_at_least('editor')
    or created_by = public.auth_profile_id()
  )
  with check (
    (
      public.auth_is_at_least('editor')
      or created_by = public.auth_profile_id()
    )
    -- Moving a row into published/scheduled state needs publish rights,
    -- regardless of who owns it.
    and (status not in ('published', 'scheduled') or public.auth_can_publish())
  );

drop policy if exists insights_delete on public.insights;
create policy insights_delete on public.insights
  for delete to authenticated
  using (public.auth_is_at_least('admin'));

-- =============================================================================
-- case_studies — editors and above; authors have no case-study rights
-- =============================================================================
drop policy if exists case_studies_public_select on public.case_studies;
create policy case_studies_public_select on public.case_studies
  for select to anon
  using (public.is_publicly_visible(status, published_at));

drop policy if exists case_studies_staff_select on public.case_studies;
create policy case_studies_staff_select on public.case_studies
  for select to authenticated
  using (
    public.is_publicly_visible(status, published_at)
    or public.auth_is_at_least('editor')
  );

drop policy if exists case_studies_insert on public.case_studies;
create policy case_studies_insert on public.case_studies
  for insert to authenticated
  with check (
    public.auth_is_at_least('editor')
    and created_by = public.auth_profile_id()
    and (status <> 'published' or public.auth_can_publish())
  );

drop policy if exists case_studies_update on public.case_studies;
create policy case_studies_update on public.case_studies
  for update to authenticated
  using (public.auth_is_at_least('editor'))
  with check (
    public.auth_is_at_least('editor')
    and (status not in ('published', 'scheduled') or public.auth_can_publish())
  );

drop policy if exists case_studies_delete on public.case_studies;
create policy case_studies_delete on public.case_studies
  for delete to authenticated
  using (public.auth_is_at_least('admin'));

-- =============================================================================
-- case_study_metrics — visibility follows the parent case study
-- =============================================================================
drop policy if exists metrics_public_select on public.case_study_metrics;
create policy metrics_public_select on public.case_study_metrics
  for select to anon
  using (
    exists (
      select 1 from public.case_studies cs
      where cs.id = case_study_id
        and public.is_publicly_visible(cs.status, cs.published_at)
    )
  );

drop policy if exists metrics_staff_select on public.case_study_metrics;
create policy metrics_staff_select on public.case_study_metrics
  for select to authenticated
  using (
    public.auth_is_at_least('editor')
    or exists (
      select 1 from public.case_studies cs
      where cs.id = case_study_id
        and public.is_publicly_visible(cs.status, cs.published_at)
    )
  );

drop policy if exists metrics_write on public.case_study_metrics;
create policy metrics_write on public.case_study_metrics
  for all to authenticated
  using (public.auth_is_at_least('editor'))
  with check (public.auth_is_at_least('editor'));

-- =============================================================================
-- media_assets — public may read metadata (alt text, dimensions); any active
-- CMS user may upload; only the uploader or an admin may change/remove.
-- =============================================================================
drop policy if exists media_public_select on public.media_assets;
create policy media_public_select on public.media_assets
  for select to anon
  using (true);

drop policy if exists media_staff_select on public.media_assets;
create policy media_staff_select on public.media_assets
  for select to authenticated
  using (true);

drop policy if exists media_insert on public.media_assets;
create policy media_insert on public.media_assets
  for insert to authenticated
  with check (
    public.auth_is_at_least('author')
    and uploaded_by = public.auth_profile_id()
  );

drop policy if exists media_update on public.media_assets;
create policy media_update on public.media_assets
  for update to authenticated
  using (uploaded_by = public.auth_profile_id() or public.auth_is_at_least('admin'))
  with check (uploaded_by = public.auth_profile_id() or public.auth_is_at_least('admin'));

drop policy if exists media_delete on public.media_assets;
create policy media_delete on public.media_assets
  for delete to authenticated
  using (uploaded_by = public.auth_profile_id() or public.auth_is_at_least('admin'));

-- =============================================================================
-- services
-- =============================================================================
drop policy if exists services_public_select on public.services;
create policy services_public_select on public.services
  for select to anon
  using (active = true);

drop policy if exists services_staff_select on public.services;
create policy services_staff_select on public.services
  for select to authenticated
  using (active = true or public.auth_is_at_least('editor'));

drop policy if exists services_write on public.services;
create policy services_write on public.services
  for all to authenticated
  using (public.auth_is_at_least('admin'))
  with check (public.auth_is_at_least('admin'));

-- =============================================================================
-- content_relationships — readable by all (drives "related insights"),
-- writable by editors and above.
-- =============================================================================
drop policy if exists relationships_public_select on public.content_relationships;
create policy relationships_public_select on public.content_relationships
  for select to anon
  using (true);

drop policy if exists relationships_staff_select on public.content_relationships;
create policy relationships_staff_select on public.content_relationships
  for select to authenticated
  using (true);

drop policy if exists relationships_write on public.content_relationships;
create policy relationships_write on public.content_relationships
  for all to authenticated
  using (public.auth_is_at_least('editor'))
  with check (public.auth_is_at_least('editor'));

-- =============================================================================
-- site_settings — anon sees only rows explicitly flagged public
-- =============================================================================
drop policy if exists settings_public_select on public.site_settings;
create policy settings_public_select on public.site_settings
  for select to anon
  using (is_public = true);

drop policy if exists settings_staff_select on public.site_settings;
create policy settings_staff_select on public.site_settings
  for select to authenticated
  using (is_public = true or public.auth_is_at_least('admin'));

drop policy if exists settings_write on public.site_settings;
create policy settings_write on public.site_settings
  for all to authenticated
  using (public.auth_is_at_least('admin'))
  with check (public.auth_is_at_least('admin'));

-- -----------------------------------------------------------------------------
-- Lock down the helper functions themselves.
-- -----------------------------------------------------------------------------
revoke all on function public.auth_role()            from public, anon;
revoke all on function public.auth_profile_id()      from public, anon;
revoke all on function public.auth_can_publish()     from public, anon;
grant execute on function public.auth_role()        to authenticated;
grant execute on function public.auth_profile_id()  to authenticated;
grant execute on function public.auth_can_publish() to authenticated;
grant execute on function public.auth_is_at_least(public.user_role) to authenticated;
grant execute on function public.is_publicly_visible(public.content_status, timestamptz) to anon, authenticated;
grant execute on function public.role_rank(public.user_role) to anon, authenticated;
