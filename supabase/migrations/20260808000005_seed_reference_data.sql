-- =============================================================================
-- BLUJEANSZ CMS — reference data seed
-- =============================================================================
-- Idempotent. Seeds the categories the brief specifies, the service list the
-- public site already offers, default site settings, and the team currently
-- hard-coded in About.tsx.
--
-- Image URLs are intentionally left NULL: the existing artwork lives in the
-- frontend bundle, not in Storage. Run `npm run seed:media` in the frontend
-- repo once Supabase credentials are configured to upload it and backfill.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Insight categories
-- -----------------------------------------------------------------------------
insert into public.insight_categories (name, slug, description, display_order)
values
  ('Cultural Intelligence', 'cultural-intelligence', 'How culture moves, and what it means for brands.', 1),
  ('African Markets',       'african-markets',       'On-the-ground perspective across African markets.', 2),
  ('Brand Growth',          'brand-growth',          'Positioning, scaling and sustainable brand growth.', 3),
  ('Youth & Influence',     'youth-influence',       'Youth culture, creators and the influence economy.', 4),
  ('Global × Local',        'global-local',          'Global brands in Africa, African brands worldwide.', 5)
on conflict (slug) do update
  set name          = excluded.name,
      description   = excluded.description,
      display_order = excluded.display_order;

-- -----------------------------------------------------------------------------
-- Services — mirrors the existing public Services page
-- -----------------------------------------------------------------------------
insert into public.services (name, slug, short_description, display_order)
values
  ('Brand Strategy',                 'brand-strategy',                 'Positioning, architecture and identity that earn cultural relevance.', 1),
  ('Digital Marketing',              'digital-marketing',              'Performance and always-on campaigns built for measurable growth.', 2),
  ('Public Relations',               'public-relations',               'Reputation, media relations and narrative management.', 3),
  ('Content Marketing',              'content-marketing',              'Editorial, video and social content that travels.', 4),
  ('Media Strategy & Buying',        'media-strategy-buying',          'Planning and buying across paid, owned and earned channels.', 5),
  ('Brand Activations & Experiences','brand-activations-experiences',  'Live, hybrid and experiential brand moments.', 6)
on conflict (slug) do update
  set name              = excluded.name,
      short_description = excluded.short_description,
      display_order     = excluded.display_order;

-- -----------------------------------------------------------------------------
-- Site settings
-- -----------------------------------------------------------------------------
insert into public.site_settings (key, value, description, is_public)
values
  ('site_meta', jsonb_build_object(
      'title', 'BLUJEANSZ',
      'tagline', 'Turning Culture Into Market Power.',
      'description', 'A global marketing communications consultancy that transforms how brands connect with culture, engage diverse markets, and achieve sustainable growth.'
    ), 'Default document title and meta description.', true),
  ('contact_email', to_jsonb('hello@blujeansz.com'::text), 'Primary public contact address.', true),
  ('insights_per_page', to_jsonb(12), 'Page size for the public insights listing.', true),
  ('social_links', jsonb_build_object(
      'linkedin', '', 'instagram', '', 'x', ''
    ), 'Public social profile URLs.', true)
on conflict (key) do nothing;

-- -----------------------------------------------------------------------------
-- Team — the 11 people currently hard-coded in About.tsx, in display order.
-- -----------------------------------------------------------------------------
insert into public.staff_members (first_name, last_name, slug, job_title, display_order, featured)
values
  ('Fabian A.', 'Lojede',     public.slugify('Fabian A. Lojede'),      'Founder | Deo Media (UK, South Africa, Nigeria) | Executive Creative Director | Communications Strategist', 1,  true),
  ('Olanrewaju','Olalekan',   public.slugify('Olanrewaju Olalekan'),   'Head | Global Operations', 2, false),
  ('Kenny',     'Olaleye',    public.slugify('Kenny Olaleye'),         'PMP, PMI-ACP, MBCS Founder & Deputy CEO | Scrum Master | Agile Practitioner | Project Manager', 3, true),
  ('Lebogang',  'Ramphele',   public.slugify('Lebogang Ramphele'),     'Head | Global Operations', 4, false),
  ('Gustav',    'Mdluli',     public.slugify('Gustav Mdluli'),         'Graphic Designer | Brand Developer | Business Administrator', 5, false),
  ('Adelola',   'Chu-Osakwe', public.slugify('Adelola Chu-Osakwe'),    'Head of Account Management', 6, false),
  ('Anita Isioma','Chukwuma', public.slugify('Anita Isioma Chukwuma'), 'Executive Assistant | Business Operations Manager', 7, false),
  ('Yandisa',   'Hlangwana',  public.slugify('Yandisa Hlangwana'),     'Frontend Developer', 8, false),
  ('Norbi',     'Zylberberg', public.slugify('Norbi Zylberberg'),      'Partner | Socialsssima', 9, false),
  ('Shivani',   'Naidoo',     public.slugify('Shivani Naidoo'),        'Partner | Global Media Strategy', 10, false),
  ('Jonny',     'Cohen',      public.slugify('Jonny Cohen'),           'Partner | Pathfinder', 11, false)
on conflict (slug) do update
  set job_title     = excluded.job_title,
      display_order = excluded.display_order,
      featured      = excluded.featured;
