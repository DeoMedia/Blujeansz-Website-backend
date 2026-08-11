-- =============================================================================
-- Fix: allow trusted server-side operations to set privileges
-- =============================================================================
-- guard_profile_privileges() refused ANY change to role, status or can_publish
-- unless the caller was an authenticated admin. auth.uid() is null for
-- service-role connections and for SQL run in the dashboard editor, so those
-- were refused too — which made it impossible to create the first super admin.
-- The documented bootstrap in DEPLOYMENT.md would have failed with
-- "insufficient privileges to change role, status or publish rights".
--
-- The guard now steps aside when there is no authenticated user. That is safe:
-- the guard exists to stop a signed-in user escalating themselves, and an
-- unauthenticated caller cannot reach this table at all — no RLS policy on
-- profiles grants anon any write, and every policy is scoped `to authenticated`.
-- A null auth.uid() therefore only ever means a trusted server-side context:
-- the service-role key, a migration, or the SQL editor.
-- =============================================================================

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
    -- Trusted server-side context (service role, migration, SQL editor).
    -- Unauthenticated callers cannot reach this table through RLS, so this
    -- cannot be reached by an anonymous request.
    if auth.uid() is null then
      return new;
    end if;

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
