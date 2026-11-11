-- Customers are limited by the minutes they paid for, never by our rupee cost (founder, 2026-11-11).
--   workspaces.minutes_left  = minutes the workspace still owns (includes minutes on hold)
--   workspaces.minutes_held  = minutes reserved by episodes that are queued or running
--   free to use              = minutes_left - minutes_held
-- An episode holds its minutes (length rounded UP to whole minutes) when it is submitted; they are spent when the
-- run finishes (clips found or not: the work was done) and given back if the run fails. Workspaces on the
-- 'internal' plan (our own team) are not counted. Our own cost is guarded by the worker's runaway check instead of
-- a per-workspace rupee limit, so spend_limit_inr goes.

alter table public.workspaces
  add column minutes_held numeric(10,2) not null default 0 check (minutes_held >= 0),
  add constraint workspaces_held_within_left check (minutes_held <= minutes_left);
alter table public.workspaces drop column spend_limit_inr;

alter table public.episodes
  add column minutes numeric(10,2) check (minutes >= 0),
  add column minutes_state text check (minutes_state in ('held', 'spent', 'refunded'));

grant select (minutes_held) on public.workspaces to authenticated;
grant select (minutes, minutes_state) on public.episodes to authenticated;
