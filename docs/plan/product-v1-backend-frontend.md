# Klipvi product v1: backend and frontend plan

Written 11 Nov 2026 from the discussion with the founder. Scope: Telugu web series (comedy shows and podcasts stay
supported by the pipeline but are not the first customers). Everything below is a proposal to agree on, not a build log.

**Name (founder, 11 Nov 2026): the product is Klipvi, domain klipvi.com.** "Shotvi" stays as the internal code
name (repo, Python packages, the Supabase project) so nothing in the pipeline has to be renamed. Customer-facing
everything says Klipvi: the web app (app.klipvi.com), emails (from @klipvi.com via Resend), the sign-in screen.

## What the customer gets

1. **Submit an episode**: a YouTube link, or an uploaded video file.
2. **Wait**: 20–45 minutes. A notification (email) when the clips are ready. Progress is visible on the episode page.
3. **Clips page**: the clips in posting order, each with its title and posting text. Per clip: a picture-style switch
   (fit with blurred fill / crop to the person), a caption template switch, a draggable caption position, and a
   download button. Sensible defaults are already applied, so most users just download.
4. **Download**: one clip, or all clips of an episode as a ZIP.
5. **Account**: a **workspace**. Every person gets a personal workspace at first sign-in; a channel or production house
   uses a team workspace whose owner invites members by email from Settings → Members (built in v1). Episodes,
   minutes, the spend limit and the bill belong to the workspace; roles: owner (billing, invites) and member.

Not for customers (internal only, already built): the ₹ estimate and approval, the rating pages, the style
comparison pages, the Streamlit app. Deferred: title/hook overlays, emojis in captions, editing cuts, posting straight
to Instagram/YouTube (needs Meta/Google app approvals; apply when there is demand).

## Load to design for

Founder (11 Nov 2026): Shotvi is not announced yet, so the start is **under 10 episodes a day** (plan for ≈ 5).
The design is the same at any volume: growing to 10–20 a day means a bigger server and more worker processes, not a
rebuild.

| | Per episode (25 min) | Per day at 5 episodes (start) | Per day at 20 episodes (later) |
|---|---|---|---|
| Wall time | ≈ 45 min, mostly waiting on Google / ElevenLabs | 1–2 episodes at once is enough | 3–4 at once |
| CPU work (cutting, both vertical styles, captions) | ≈ 10 min on 4–8 cores | under 1 hour | ≈ 3.5 hours |
| Gemini | ₹40–50 now; ₹80–100 from Jan 2027 | ₹200–250 (₹400–500 from Jan) | ₹800–2,000 |
| ElevenLabs Scribe | 66.7 credits per minute of video | ≈ 8k credits a day, ≈ 250k a month | ≈ 33k a day |
| Storage | 0.5–1 GB (source + clips) | ≈ 75–150 GB with 30-day retention | ≈ 300–600 GB |

## Architecture (proposal)

```
browser (Next.js on Vercel)
   │  HTTPS, JSON
   ▼
API (FastAPI, Python) ──── Postgres (Supabase: data + sign-in)
   │ writes jobs
   ▼
workers (Python, 3–4 processes on one Linux server)
   │ run the existing flow: download → Scribe → word fix → speaker fix → text step →
   │ video step → scene notes → clips → posting order → cut → vertical (fit + crop) → captions
   ▼
object storage (Cloudflare R2): sources, clips, caption renders, thumbnails
```

Reused as is: `selection_v2.flow_v2` (the steps), `vertical.py`, `captions.py`, `stage4_rank.py`, the spend ledger
(`shotvi/llm/spend.py`), the transcription scripts. New: the API, the job table and workers, storage upload/download,
workspaces, members and invitations, usage metering, the web app.

Decisions and the suggested choice:

| Decision | Suggestion | Why |
|---|---|---|
| Server | **Hetzner** cloud, start at 8 vCPU / 16 GB (≈ ₹3–4k/month); resize to 16 vCPU / 32 GB (≈ ₹6–7k) when volume passes ≈ 10 a day. India-region alternative: AWS Mumbai (≈ 3–4× the price) | CPU only, no GPU needed; resizing is a reboot, not a rebuild |
| Jobs | a `jobs` table in Postgres + worker processes polling it (no Redis to start) | 30-minute jobs must survive restarts; one fewer service |
| Packaging | **Docker** for the API and the workers only: one image (Python 3.11, ffmpeg with libass, yt-dlp, the Telugu fonts, the face models), started by one `docker compose` file with two services | the same image runs on the Mac and the server; no hand setup of ffmpeg/fonts; update = rebuild + restart. Not used for the frontend (Vercel), database (Supabase) or files (R2) |
| Files | **Cloudflare R2** | no download fees; S3-compatible; ≈ ₹1–2k/month at this scale |
| Database + sign-in | **Supabase** (Postgres + Auth, Mumbai region). Free plan while building; Pro (≈ ₹2k/month) before the first customer, since free projects pause when idle and have no backups | Google sign-in and magic links without building them |
| Frontend | **Next.js + Tailwind + shadcn**, hosted on **Vercel** (free tier to start) | small app; the `frontend-design` skill and Playwright MCP are set up |
| Payments (later) | **Razorpay**: prepaid minutes; invoiced B2B for houses | Indian cards / UPI |
| Data retention | sources 30 days, clips 90 days, delete on request; Gemini paid API does not train on inputs | put it in the terms |

Infra total: **≈ ₹3–4k/month while building** (server only; Supabase, R2 and Vercel free tiers), **≈ ₹6–7k/month with the first customers** (Supabase Pro added), ≈ ₹10–12k at 10–20 episodes a day. All before Gemini and ElevenLabs usage.

## Minutes and our cost (founder, 11 Nov 2026)

- **Customers are limited only by the minutes they paid for**, never by what an episode costs us.
- At submit we read the length from YouTube (no download). The episode **holds** its minutes (length rounded **up**
  to whole minutes); too few free minutes = refused at once with "This episode is 22 min; you have 8 min free."
- Minutes are **spent** when the run finishes, **whether or not good clips were found** (the work was done), and
  **refunded** if the run fails. Our own team's workspace (plan `internal`) is not counted.
- **Our cost** is guarded by a **runaway check**: a run costing over ₹12 per minute of episode (≈ 3× normal; raise
  to ~₹18 when Gemini prices double in Jan 2027) is stopped, marked RUNAWAY and refunded.
- For launch: a **separate production Gemini key** with Google Cloud **budget alerts** (e.g. ₹5k / ₹10k / ₹20k a
  month), instead of the ₹4,500 development cap, which stays for our own test runs.

## Data model (first cut)

- `workspaces` (name, kind personal|team, plan, minutes_left, minutes_held), `members` (user → workspace, role owner|member)
- `invitations` (workspace, email, role, link code, expires_at, accepted_at)
- `episodes` (organisation, source url/file, title, duration, status, cost_inr, created_at)
- `jobs` (episode, step, status, attempts, started/finished, log, error)
- `clips` (episode, order, title, hook, posting text, start/end, pieces, strength, verdict)
- `renders` (clip, kind: fit|crop|caption, settings JSON, storage key, size, created_at)
- `caption_templates` (organisation or global, settings JSON)
- `usage` (organisation, episode, minutes, cost_inr, date)

## API (first cut)

- `POST /episodes` (url or upload) → episode id; `GET /episodes`, `GET /episodes/{id}` (status + progress)
- `GET /episodes/{id}/clips`; `POST /clips/{id}/render` (style, caption template, position) → render; `GET /renders/{id}/download`
- `GET /episodes/{id}/download` (ZIP); `GET /caption-templates`
- `GET /me`, `GET /workspaces`, `POST /workspaces/{id}/invitations`, `POST /invitations/{code}/accept`, `DELETE /workspaces/{id}/members/{user}`
- Sign-in handled by Supabase; the API checks the token.

## Milestones

| # | What | Done when | Time |
|---|---|---|---|
| 1 | Accounts: Hetzner, Cloudflare R2, Supabase, Vercel (founder creates; I send exact steps) | keys in `.env` on the server | 1 day |
| 2 | Backend: API + jobs + workers running the flow, files to R2, per-organisation cap and ledger | an episode submitted by API comes back with clips in R2 | 1–2 weeks |
| 3 | Frontend: sign-in, submit, episode list with progress, clips page (styles, captions, drag, download) | the founder runs an episode end to end in the browser | 1–2 weeks |
| 4 | Server setup: ffmpeg-full, fonts, face models, retries, logs, alerts | 2–3 real episodes a day for a week without manual help | 3–4 days |
| 5 | Billing: Razorpay prepaid minutes; invoices for B2B | first paying organisation | after first users |

## Open items carried from the pipeline work

- Box A storage time (step 5 → step 6): extend when step 5 finishes, or keep 45–60 min. Needs the founder's decision.
- Endings that stop on the last word in some clips (hostel food, Soham-is-dead); the "సరే సరే" comedy button.
- Run-to-run variation in step 7 (a good clip missed one run in three).
- Comedy shows not yet run through the new flow.
- Google's Gemini price doubles on 1 Jan 2027: re-check pricing in December.
