-- Klipvi v1 core schema (agreed with the founder 2026-11-11). Nine tables:
--   workspaces, members, invitations, caption_templates, episodes, jobs, clips, renders, usage.
-- Access model: customers sign in with Supabase Auth; every write goes through our API/workers with the secret key
-- (bypasses RLS). RLS gives signed-in users READ access to their own workspaces' rows only; columns marked
-- "internal" are not granted to them at all; jobs and usage are not readable by customers.

create schema if not exists private;

create or replace function private.touch_updated_at() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.updated_at = now();
  return new;
end $$;

-- ---------------------------------------------------------------------------------------------- workspaces
create table public.workspaces (
  id                  uuid primary key default gen_random_uuid(),
  name                text not null check (length(btrim(name)) between 1 and 120),
  kind                text not null default 'personal' check (kind in ('personal', 'team')),
  plan                text not null default 'trial',
  minutes_left        numeric(10,2) not null default 0 check (minutes_left >= 0),
  spend_limit_inr     numeric(12,2) not null default 1500 check (spend_limit_inr >= 0),   -- internal: our cost cap per month
  default_template_id uuid,                                                                 -- fk added after caption_templates
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);
create trigger workspaces_touch before update on public.workspaces for each row execute function private.touch_updated_at();

-- ---------------------------------------------------------------------------------------------- members
create table public.members (
  workspace_id uuid not null references public.workspaces (id) on delete cascade,
  user_id      uuid not null references auth.users (id) on delete cascade,
  role         text not null default 'member' check (role in ('owner', 'member')),
  created_at   timestamptz not null default now(),
  primary key (workspace_id, user_id)
);
create index members_user_id_idx on public.members (user_id);

-- ---------------------------------------------------------------------------------------------- invitations
create table public.invitations (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces (id) on delete cascade,
  email        text not null check (email = lower(btrim(email)) and position('@' in email) > 1),
  role         text not null default 'member' check (role in ('owner', 'member')),
  invited_by   uuid references auth.users (id) on delete set null,
  code         text not null unique default encode(extensions.gen_random_bytes(24), 'hex'),  -- internal: secret link part
  expires_at   timestamptz not null default now() + interval '7 days',
  accepted_at  timestamptz,
  created_at   timestamptz not null default now()
);
create index invitations_workspace_id_idx on public.invitations (workspace_id);
create index invitations_invited_by_idx on public.invitations (invited_by);
create unique index invitations_one_pending_idx on public.invitations (workspace_id, email) where accepted_at is null;

-- ---------------------------------------------------------------------------------------------- caption_templates
create table public.caption_templates (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid references public.workspaces (id) on delete cascade,   -- null = Klipvi's shared library
  name         text not null check (length(btrim(name)) between 1 and 80),
  label        text,
  settings     jsonb not null check (jsonb_typeof(settings) = 'object'),
  sort         integer not null default 0,
  created_at   timestamptz not null default now()
);
create index caption_templates_workspace_id_idx on public.caption_templates (workspace_id);
create unique index caption_templates_shared_name_idx on public.caption_templates (name) where workspace_id is null;
create unique index caption_templates_ws_name_idx on public.caption_templates (workspace_id, name) where workspace_id is not null;

alter table public.workspaces
  add constraint workspaces_default_template_fk foreign key (default_template_id)
  references public.caption_templates (id) on delete set null;
create index workspaces_default_template_id_idx on public.workspaces (default_template_id);

-- ---------------------------------------------------------------------------------------------- episodes
create table public.episodes (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces (id) on delete cascade,
  submitted_by uuid references auth.users (id) on delete set null,
  source_url   text,
  source_file  text,                                   -- storage key of an uploaded file
  video_id     text,                                   -- YouTube id (or our id for uploads); names the run folder
  title        text,
  length_s     numeric(10,2) check (length_s >= 0),
  status       text not null default 'waiting' check (status in ('waiting', 'running', 'done', 'failed')),
  step         text,                                   -- human text: "Finding clips (4 of 6)"
  verdict      text check (verdict in ('strong', 'thin', 'none')),
  verdict_why  text,
  message      text,                                   -- friendly error for the customer
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  finished_at  timestamptz,
  check (source_url is not null or source_file is not null)
);
create index episodes_workspace_created_idx on public.episodes (workspace_id, created_at desc);
create index episodes_submitted_by_idx on public.episodes (submitted_by);
create trigger episodes_touch before update on public.episodes for each row execute function private.touch_updated_at();

-- ---------------------------------------------------------------------------------------------- jobs (internal)
create table public.jobs (
  id           bigint generated always as identity primary key,
  episode_id   uuid not null references public.episodes (id) on delete cascade,
  status       text not null default 'waiting' check (status in ('waiting', 'running', 'done', 'failed')),
  step         text,                                   -- flow step being run
  attempts     integer not null default 0 check (attempts >= 0),
  max_attempts integer not null default 3 check (max_attempts >= 1),
  run_after    timestamptz not null default now(),     -- retry back-off
  worker       text,
  started_at   timestamptz,
  heartbeat_at timestamptz,
  finished_at  timestamptz,
  error        text,
  cost_inr     numeric(10,2) not null default 0,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index jobs_episode_id_idx on public.jobs (episode_id);
create index jobs_waiting_idx on public.jobs (run_after) where status = 'waiting';
create index jobs_running_heartbeat_idx on public.jobs (heartbeat_at) where status = 'running';
create trigger jobs_touch before update on public.jobs for each row execute function private.touch_updated_at();

-- ---------------------------------------------------------------------------------------------- clips
create table public.clips (
  id           uuid primary key default gen_random_uuid(),
  episode_id   uuid not null references public.episodes (id) on delete cascade,
  number       text not null,                          -- c01, c02 ...
  rank         integer check (rank >= 1),              -- posting order
  post_first   boolean not null default false,
  title        text,
  hook         text,
  posting_text text,
  why          text,
  start_s      numeric(10,2) not null check (start_s >= 0),
  end_s        numeric(10,2) not null,
  length_s     numeric(10,2) not null check (length_s > 0),
  pieces       jsonb not null default '[]' check (jsonb_typeof(pieces) = 'array'),
  strength     text,                                   -- internal: the AI's own rating
  created_at   timestamptz not null default now(),
  check (end_s > start_s),
  unique (episode_id, number)
);

-- ---------------------------------------------------------------------------------------------- renders
create table public.renders (
  id            uuid primary key default gen_random_uuid(),
  clip_id       uuid not null references public.clips (id) on delete cascade,
  style         text not null check (style in ('fit', 'crop')),
  template_id   uuid references public.caption_templates (id) on delete set null,   -- null = no captions
  settings      jsonb not null default '{}' check (jsonb_typeof(settings) = 'object'),
  settings_hash text not null,                         -- same clip + style + settings = same file, reused
  status        text not null default 'waiting' check (status in ('waiting', 'making', 'ready', 'failed')),
  file          text,                                  -- storage key of the mp4
  size_mb       numeric(10,2),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (clip_id, style, settings_hash)
);
create index renders_template_id_idx on public.renders (template_id);
create trigger renders_touch before update on public.renders for each row execute function private.touch_updated_at();

-- ---------------------------------------------------------------------------------------------- usage (internal)
create table public.usage (
  id              bigint generated always as identity primary key,
  workspace_id    uuid not null references public.workspaces (id) on delete restrict,   -- billing record outlives deletes
  episode_id      uuid unique references public.episodes (id) on delete set null,
  minutes_charged numeric(10,2) not null check (minutes_charged >= 0),
  cost_inr        numeric(10,2) not null default 0,     -- internal: what it cost us
  created_at      timestamptz not null default now()
);
create index usage_workspace_created_idx on public.usage (workspace_id, created_at desc);

-- ---------------------------------------------------------------------------------------------- access helpers
create or replace function private.is_member(ws uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.members m where m.workspace_id = ws and m.user_id = (select auth.uid()));
$$;
create or replace function private.is_owner(ws uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.members m where m.workspace_id = ws and m.user_id = (select auth.uid()) and m.role = 'owner');
$$;
revoke all on function private.is_member(uuid), private.is_owner(uuid) from public, anon;
grant usage on schema private to authenticated;
grant execute on function private.is_member(uuid), private.is_owner(uuid) to authenticated;

-- ---------------------------------------------------------------------------------------------- row level security
alter table public.workspaces        enable row level security;
alter table public.members           enable row level security;
alter table public.invitations       enable row level security;
alter table public.caption_templates enable row level security;
alter table public.episodes          enable row level security;
alter table public.jobs              enable row level security;   -- no policies: customers never read jobs
alter table public.clips             enable row level security;
alter table public.renders           enable row level security;
alter table public.usage             enable row level security;   -- no policies: read through our API only

create policy workspaces_read on public.workspaces for select to authenticated
  using ((select private.is_member(id)));
create policy members_read on public.members for select to authenticated
  using ((select private.is_member(workspace_id)));
create policy invitations_read on public.invitations for select to authenticated
  using ((select private.is_owner(workspace_id)));
create policy caption_templates_read on public.caption_templates for select to authenticated
  using (workspace_id is null or (select private.is_member(workspace_id)));
create policy episodes_read on public.episodes for select to authenticated
  using ((select private.is_member(workspace_id)));
create policy clips_read on public.clips for select to authenticated
  using (exists (select 1 from public.episodes e where e.id = clips.episode_id and (select private.is_member(e.workspace_id))));
create policy renders_read on public.renders for select to authenticated
  using (exists (select 1 from public.clips c join public.episodes e on e.id = c.episode_id
                 where c.id = renders.clip_id and (select private.is_member(e.workspace_id))));

-- ---------------------------------------------------------------------------------------------- column grants
-- Supabase grants everything on new public tables to anon/authenticated by default; take it back and grant only
-- the readable, non-internal columns to signed-in users. Writes happen only through our API (secret key).
revoke all on public.workspaces, public.members, public.invitations, public.caption_templates, public.episodes,
              public.jobs, public.clips, public.renders, public.usage from anon, authenticated;
grant select (id, name, kind, plan, minutes_left, default_template_id, created_at, updated_at) on public.workspaces to authenticated;
grant select (workspace_id, user_id, role, created_at) on public.members to authenticated;
grant select (id, workspace_id, email, role, invited_by, expires_at, accepted_at, created_at) on public.invitations to authenticated;
grant select (id, workspace_id, name, label, settings, sort, created_at) on public.caption_templates to authenticated;
grant select (id, workspace_id, submitted_by, source_url, video_id, title, length_s, status, step, verdict, verdict_why,
              message, created_at, updated_at, finished_at) on public.episodes to authenticated;
grant select (id, episode_id, number, rank, post_first, title, hook, posting_text, why, start_s, end_s, length_s, pieces,
              created_at) on public.clips to authenticated;
grant select (id, clip_id, style, template_id, settings, status, file, size_mb, created_at, updated_at) on public.renders to authenticated;
alter default privileges for role postgres in schema public revoke all on tables from anon, authenticated;

-- ---------------------------------------------------------------------------------------------- personal workspace at sign-up
create or replace function private.handle_new_user() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  ws uuid;
  nm text := coalesce(nullif(btrim(new.raw_user_meta_data ->> 'full_name'), ''),
                      nullif(btrim(new.raw_user_meta_data ->> 'name'), ''),
                      split_part(coalesce(new.email, 'My workspace'), '@', 1));
begin
  insert into public.workspaces (name, kind) values (left(nm, 120), 'personal') returning id into ws;
  insert into public.members (workspace_id, user_id, role) values (ws, new.id, 'owner');
  return new;
end $$;
revoke all on function private.handle_new_user() from public, anon, authenticated;
create trigger on_auth_user_created after insert on auth.users for each row execute function private.handle_new_user();
