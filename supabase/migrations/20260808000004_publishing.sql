-- =============================================================================
-- BLUJEANSZ CMS — scheduled publishing
-- =============================================================================
-- Public reads require status = 'published' AND published_at <= now(), so
-- scheduled rows have to be flipped once their time arrives. This function does
-- that flip; it is wired to pg_cron when the extension is available and can be
-- invoked manually (or from an edge function) otherwise.
-- =============================================================================

create or replace function public.publish_due_content()
returns table (kind text, id uuid, title text)
language plpgsql
security definer
set search_path = public
as $$
begin
  return query
  with promoted_insights as (
    update public.insights
    set status       = 'published',
        published_at = coalesce(published_at, scheduled_at, now())
    where status = 'scheduled'
      and scheduled_at is not null
      and scheduled_at <= now()
    returning insights.id, insights.title
  )
  select 'insight'::text, promoted_insights.id, promoted_insights.title
  from promoted_insights;

  return query
  with promoted_cases as (
    update public.case_studies
    set status       = 'published',
        published_at = coalesce(published_at, scheduled_at, now())
    where status = 'scheduled'
      and scheduled_at is not null
      and scheduled_at <= now()
    returning case_studies.id, case_studies.title
  )
  select 'case_study'::text, promoted_cases.id, promoted_cases.title
  from promoted_cases;
end;
$$;

revoke all on function public.publish_due_content() from public, anon;
grant execute on function public.publish_due_content() to authenticated, service_role;

-- Run every 5 minutes when pg_cron is enabled on the project.
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'pg_cron') then
    create extension if not exists pg_cron;

    -- Replace any previous definition so re-running the migration is safe.
    perform cron.unschedule(jobid)
    from cron.job
    where jobname = 'blujeansz-publish-due-content';

    perform cron.schedule(
      'blujeansz-publish-due-content',
      '*/5 * * * *',
      $cron$ select public.publish_due_content(); $cron$
    );
  else
    raise notice 'pg_cron unavailable — call public.publish_due_content() from a scheduled edge function instead.';
  end if;
exception when others then
  raise notice 'Could not schedule publish_due_content (%). Schedule it manually.', sqlerrm;
end $$;
