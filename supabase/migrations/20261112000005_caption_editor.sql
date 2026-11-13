-- Caption editor (founder, 2026-11-12): each clip keeps its editor draft (picture style, caption settings, edited
-- words, line splits) so reopening the clip shows the edits; a render remembers the edited words it was made with.
alter table public.clips add column draft jsonb check (draft is null or jsonb_typeof(draft) = 'object');
alter table public.renders add column captions jsonb check (captions is null or jsonb_typeof(captions) = 'object');
grant select (draft) on public.clips to authenticated;
grant select (captions) on public.renders to authenticated;
