-- Caption renders on request (web app: template switch, caption editor). A request is a renders row in 'waiting';
-- a worker claims it (making), burns the captions onto the clip's plain fit/crop video, and marks it ready or failed.
alter table public.renders add column error text;            -- internal: why a render failed (not granted to customers)
create index renders_waiting_idx on public.renders (created_at) where status = 'waiting';
create index renders_making_idx on public.renders (updated_at) where status = 'making';
