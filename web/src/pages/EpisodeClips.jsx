import React, { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Download, Loader2, PenLine } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import Shell from "@/components/klipvi/Shell";
import CaptionOverlay from "@/components/klipvi/CaptionOverlay";
import DownloadAll from "@/components/klipvi/DownloadAll";
import { downloadClip, getClipEditor, renderLinks } from "@/api/klipvi";
import { parseStep, splitTitle, templateLabel, useCaptionTemplates, useEpisode, useWorkspace } from "@/hooks/useKlipvi";

const VERDICT = {
  strong: "Strong episode.",
  thin: "A few good moments.",
  none: "No clips worth posting from this one.",
};
const STYLES = [
  { key: "crop", label: "Crop", hint: "Follows who matters in each shot" },
  { key: "fit", label: "Fit", hint: "The whole picture, blurred above and below" },
];

/** Becomes true once the element has been on screen (so videos load only when scrolled to). */
function useSeen(ref) {
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (seen || !ref.current) return undefined;
    const io = new IntersectionObserver(([e]) => e.isIntersecting && setSeen(true), { rootMargin: "300px" });
    io.observe(ref.current);
    return () => io.disconnect();
  }, [ref, seen]);
  return seen;
}

/** The clip's video without captions (captioned versions carry caption settings). */
export const plainRender = (clip, style) =>
  clip.renders.find((r) => r.style === style && !r.template_id && r.status === "ready" && !r.settings?.font);

function ClipCard({ clip, templates }) {
  const draft = clip.draft;
  const [style, setStyle] = useState(draft?.style || "crop");
  const [caption, setCaption] = useState(draft ? "draft" : ""); // "" none, "draft" the editor's version, else a template id
  const [script, setScript] = useState(draft?.settings?.script || "telugu"); // telugu | latin
  const [links, setLinks] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null); // download status text
  const videoRef = useRef(null);
  const box = useRef(null);
  const seen = useSeen(box);
  const render = plainRender(clip, style);

  // the clip's spoken words (for live captions): loaded once a caption style is chosen
  const editor = useQuery({
    queryKey: ["clip-editor", clip.id],
    queryFn: () => getClipEditor(clip.id),
    enabled: Boolean(caption),
    staleTime: 10 * 60_000,
  });
  const template = templates?.find((t) => t.id === caption);
  const captions = useMemo(() => {
    if (caption === "draft" && draft) return { settings: { ...draft.settings, script }, words: draft.words || editor.data?.words, breaks: draft.breaks };
    if (template) return { settings: { ...template.settings, script }, words: editor.data?.words, breaks: [] };
    return null;
  }, [caption, draft, template, editor.data, script]);

  useEffect(() => {
    let live = true;
    setLinks(null);
    setError(null);
    if (!seen || !render) return undefined;
    renderLinks(render.id)
      .then((l) => live && setLinks(l))
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [seen, render?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const download = async () => {
    if (!render) return;
    setError(null);
    const body = { style };
    if (caption === "draft" && draft) {
      Object.assign(body, { template_id: draft.template_id, settings: { ...draft.settings, script }, words: draft.words, breaks: draft.breaks });
    } else if (template) {
      Object.assign(body, { template_id: template.id, settings: { script } });
    }
    try {
      await downloadClip(clip.id, body, render.id, setBusy);
    } catch (e) {
      setError(e.message);
    } finally {
      setTimeout(() => setBusy(null), 1500);
    }
  };

  return (
    <article className="flex flex-col" aria-labelledby={`clip-${clip.id}-title`}>
      <div ref={box} className="relative aspect-[9/16] rounded-xl overflow-hidden bg-black ring-1 ring-k-line">
        {links ? (
          <video
            ref={videoRef}
            key={links.url}
            src={links.url}
            poster={links.poster || undefined}
            controls
            playsInline
            preload={links.poster ? "none" : "metadata"}
            className="w-full h-full object-cover bg-black"
          />
        ) : (
          <div className="absolute inset-0 grid place-items-center text-k-faint text-sm px-4 text-center">
            {error || (render ? "Loading video…" : "This video is still being made.")}
          </div>
        )}
        {captions?.words && links && (
          <CaptionOverlay videoRef={videoRef} words={captions.words} settings={captions.settings} breaks={captions.breaks} style={style} />
        )}
        <span
          className="absolute left-2.5 top-1.5 font-display text-[2.4rem] font-extrabold leading-none text-white pointer-events-none"
          style={{ textShadow: "0 2px 10px rgba(0,0,0,.6)", fontVariationSettings: '"wdth" 90' }}
          aria-label={`Post number ${clip.rank}`}
        >
          {clip.rank}
        </span>
        {clip.post_first && (
          <span className="absolute right-2 top-2.5 rounded-md bg-k-mark px-2 py-0.5 text-[0.8rem] font-bold text-white pointer-events-none">
            Post first
          </span>
        )}
      </div>

      <h3 id={`clip-${clip.id}-title`} className="mt-3 font-bold text-[1.05rem] leading-[1.55]">
        <Link to={`/clips/${clip.id}/edit`} className="hover:text-k-accentsoft">
          {clip.title}
        </Link>
        <span className="ml-2 text-sm font-normal text-k-subtle whitespace-nowrap">{Math.round(clip.length_s)} s</span>
      </h3>
      {clip.why && <p className="mt-1 text-[0.92rem] leading-normal text-k-muted line-clamp-3">{clip.why}</p>}

      <div className="mt-auto pt-3 flex items-center gap-2">
      <label className="flex-1 min-w-0 block">
        <span className="sr-only">Captions</span>
        <select
          value={caption}
          onChange={(e) => setCaption(e.target.value)}
          className="w-full rounded-lg bg-k-surface border border-k-line px-2.5 py-1.5 text-sm text-k-text"
        >
          <option value="">No captions</option>
          {draft && <option value="draft">Captions: your edits</option>}
          {(templates || []).map((t) => (
            <option key={t.id} value={t.id}>
              Captions: {templateLabel(t).name}
            </option>
          ))}
        </select>
      </label>
      {caption && (
        <div className="shrink-0 inline-flex rounded-lg border border-k-line overflow-hidden" role="group" aria-label="Captions in">
          {[
            ["telugu", "తె", "Telugu letters"],
            ["latin", "En", "English letters (enti)"],
          ].map(([k, label, hint]) => (
            <button
              key={k}
              type="button"
              title={hint}
              aria-pressed={script === k}
              onClick={() => setScript(k)}
              className={`px-2 py-1.5 text-sm ${script === k ? "bg-k-text text-k-bg font-semibold" : "bg-k-surface text-k-muted hover:text-k-text"}`}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      </div>
      <div className="pt-2 flex items-center gap-2">
        <div className="inline-flex rounded-lg border border-k-line overflow-hidden" role="group" aria-label="Picture style">
          {STYLES.map((s) => (
            <button
              key={s.key}
              type="button"
              title={s.hint}
              aria-pressed={style === s.key}
              onClick={() => setStyle(s.key)}
              className={`px-3 py-1.5 text-sm ${style === s.key ? "bg-k-text text-k-bg font-semibold" : "bg-k-surface text-k-muted hover:text-k-text"}`}
            >
              {s.label}
            </button>
          ))}
        </div>
        <Link
          to={`/clips/${clip.id}/edit`}
          className="inline-flex items-center gap-1 rounded-lg border border-k-line px-2.5 py-1.5 text-sm text-k-muted hover:text-k-text"
        >
          <PenLine size={15} aria-hidden="true" />
          Edit
        </Link>
        <button
          type="button"
          onClick={download}
          disabled={!render || Boolean(busy)}
          className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-k-accent text-k-ink font-bold px-3 py-1.5 text-sm hover:bg-k-accent2 disabled:opacity-60"
          aria-label="Download this clip"
        >
          {busy ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Download size={15} aria-hidden="true" />}
          {busy ? "" : "Download"}
        </button>
      </div>
      {busy && <p className="mt-1.5 text-sm text-k-muted" role="status">{busy}</p>}
      {error && links && <p className="mt-1.5 text-sm text-k-bad" role="alert">{error}</p>}
    </article>
  );
}

function Working({ ep }) {
  const s = parseStep(ep.step);
  const pct = s.total ? Math.round(((s.done - 0.5) / s.total) * 100) : 4; // finished steps + half of the current one
  if (ep.status === "failed") return <p role="alert" className="mt-6 text-k-bad">{ep.message}</p>;
  return (
    <div className="mt-6 max-w-xl rounded-2xl border border-k-line bg-k-surface p-5" role="status">
      <p className="text-k-text">
        {ep.status === "waiting" ? "Waiting to start" : s.label}
        {s.total > 0 && <span className="text-k-muted"> ({s.done} of {s.total})</span>}
      </p>
      <div className="mt-3 h-1.5 rounded-full bg-k-raised overflow-hidden">
        <div className="h-full bg-k-accent transition-[width] duration-700" style={{ width: `${pct}%` }} />
      </div>
      <p className="mt-3 text-sm text-k-muted">You can leave this page. The clips will be here when they are ready.</p>
    </div>
  );
}

export default function EpisodeClips() {
  const { episodeId } = useParams();
  const { data: ep, isLoading, error, refetch } = useEpisode(episodeId);
  const { workspace } = useWorkspace();
  const { data: templates } = useCaptionTemplates(ep?.workspace_id || workspace?.id);
  const t = ep ? splitTitle(ep.title) : null;

  return (
    <Shell
      title={t ? t.main || "Episode" : " "}
      subtitle={t?.rest || ""}
      back={{ to: "/dashboard", label: "Episodes" }}
      actions={ep && ep.status === "done" && ep.clips.length > 0 ? <DownloadAll episode={ep} templates={templates} refresh={refetch} /> : null}
    >
      {isLoading && <p className="mt-6 text-k-muted">Loading the clips…</p>}
      {error && <p role="alert" className="mt-6 text-k-bad">{error.message}</p>}
      {ep && ep.status !== "done" && <Working ep={ep} />}
      {ep && ep.status === "done" && (
        <>
          <p className="mt-3 max-w-3xl text-k-muted">
            <span className="font-semibold text-k-text">
              {ep.verdict ? VERDICT[ep.verdict] : ""} {ep.clips.length} clips, in the order to post them.
            </span>{" "}
            {ep.verdict_why}
          </p>
          <section aria-label="Clips in posting order" className="mt-8 grid gap-x-6 gap-y-10 grid-cols-[repeat(auto-fill,minmax(220px,1fr))]">
            {ep.clips.map((c) => (
              <ClipCard key={c.id} clip={c} templates={templates} />
            ))}
          </section>
        </>
      )}
    </Shell>
  );
}
