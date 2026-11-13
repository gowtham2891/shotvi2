import React, { useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, Loader2 } from "lucide-react";
import Shell from "@/components/klipvi/Shell";
import { minutesLabel, parseStep, splitTitle, useEpisodes, useSubmitEpisode, useWorkspace, youtubeThumb } from "@/hooks/useKlipvi";

const VERDICT = {
  strong: "Strong episode",
  thin: "A few good moments",
  none: "No clips worth posting",
};

function timeAgo(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function SubmitBox({ workspace }) {
  const [url, setUrl] = useState("");
  const submit = useSubmitEpisode(workspace?.id);
  const err = submit.error;
  const onSubmit = (e) => {
    e.preventDefault();
    if (!url.trim()) return;
    submit.mutate(url.trim(), { onSuccess: () => setUrl("") });
  };
  return (
    <form onSubmit={onSubmit} className="mt-6">
      <label htmlFor="episode-url" className="block text-k-muted mb-2">
        Paste a YouTube link to one episode. Clips are ready in about 30 to 45 minutes.
      </label>
      <div className="flex flex-col sm:flex-row gap-3">
        <input
          id="episode-url"
          type="url"
          inputMode="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.youtube.com/watch?v=…"
          className="flex-1 min-w-0 rounded-xl bg-k-surface border border-k-line px-4 py-3 text-k-text placeholder:text-k-faint focus:outline-none focus-visible:ring-2 focus-visible:ring-k-accent"
          aria-invalid={Boolean(err)}
          aria-describedby={err ? "episode-url-error" : undefined}
        />
        <button
          type="submit"
          disabled={submit.isPending || !workspace}
          className="rounded-xl bg-k-accent text-k-ink font-bold px-6 py-3 hover:bg-k-accent2 disabled:opacity-60 inline-flex items-center justify-center gap-2"
        >
          {submit.isPending && <Loader2 size={18} className="animate-spin" aria-hidden="true" />}
          {submit.isPending ? "Checking the video" : "Make clips"}
        </button>
      </div>
      {err && (
        <p id="episode-url-error" role="alert" className="mt-3 text-k-bad flex items-start gap-2">
          <AlertCircle size={18} className="mt-0.5 shrink-0" aria-hidden="true" />
          {err.message}
        </p>
      )}
    </form>
  );
}

function Status({ ep }) {
  if (ep.status === "done") {
    return (
      <p className="text-k-text">
        <span className="font-semibold">{ep.clip_count} clips</span>
        {ep.verdict && <span className="text-k-muted">, {VERDICT[ep.verdict].toLowerCase()}</span>}
      </p>
    );
  }
  if (ep.status === "failed") {
    return <p className="text-k-bad">{ep.message || "Something went wrong with this episode."}</p>;
  }
  const s = parseStep(ep.step);
  const pct = s.total ? Math.round(((s.done - 0.5) / s.total) * 100) : 4; // finished steps + half of the current one
  return (
    <div className="w-full max-w-sm">
      <p className="text-k-text">
        {ep.status === "waiting" ? "Waiting to start" : s.label}
        {s.total > 0 && <span className="text-k-muted"> ({s.done} of {s.total})</span>}
      </p>
      {ep.message && <p className="text-sm text-k-warn">{ep.message}</p>}
      <div className="mt-2 h-1.5 rounded-full bg-k-raised overflow-hidden" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Progress">
        <div className="h-full bg-k-accent transition-[width] duration-700" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function EpisodeRow({ ep }) {
  const thumb = youtubeThumb(ep.video_id);
  const body = (
    <>
      <div className="w-36 sm:w-44 shrink-0 aspect-video rounded-lg overflow-hidden bg-k-raised">
        {thumb && <img src={thumb} alt="" className="w-full h-full object-cover" loading="lazy" />}
      </div>
      <div className="min-w-0 flex-1 flex flex-col gap-1.5">
        <h2 className="font-semibold text-[1.05rem] leading-snug line-clamp-2">{splitTitle(ep.title).main || "New episode"}</h2>
        {splitTitle(ep.title).rest && <p className="text-sm text-k-subtle line-clamp-1 -mt-1">{splitTitle(ep.title).rest}</p>}
        <p className="text-sm text-k-subtle">
          {ep.minutes ? `${minutesLabel(ep.minutes)} episode, ` : ""}
          added {timeAgo(ep.created_at)}
        </p>
        <Status ep={ep} />
      </div>
    </>
  );
  const cls = "flex gap-4 sm:gap-5 p-3 sm:p-4 rounded-2xl border border-k-line bg-k-surface";
  return ep.status === "done" ? (
    <Link to={`/episodes/${ep.id}`} className={`${cls} hover:border-k-line2 hover:bg-k-raised/50 focus-visible:ring-2 focus-visible:ring-k-accent outline-none`}>
      {body}
    </Link>
  ) : (
    <div className={cls}>{body}</div>
  );
}

export default function Episodes() {
  const { workspace, isLoading: meLoading, error: meError } = useWorkspace();
  const episodes = useEpisodes(workspace?.id);
  const list = episodes.data || [];

  return (
    <Shell title="Episodes">
      <SubmitBox workspace={workspace} />
      <section className="mt-10" aria-label="Your episodes">
        {(meLoading || episodes.isLoading) && <p className="text-k-muted">Loading your episodes…</p>}
        {(meError || episodes.error) && (
          <p role="alert" className="text-k-bad">{(meError || episodes.error).message}</p>
        )}
        {!episodes.isLoading && !episodes.error && workspace && list.length === 0 && (
          <div className="rounded-2xl border border-dashed border-k-line2 p-8 text-center text-k-muted">
            Your episodes will appear here. Paste a link above to get the first one's clips.
          </div>
        )}
        <div className="flex flex-col gap-3">
          {list.map((ep) => (
            <EpisodeRow key={ep.id} ep={ep} />
          ))}
        </div>
      </section>
    </Shell>
  );
}
