import React, { useEffect, useState } from "react";
import { Download, Loader2, X } from "lucide-react";
import { downloadZip, readyRender } from "@/api/klipvi";
import { templateLabel } from "@/hooks/useKlipvi";

const plainOf = (clip, style) => clip.renders.find((r) => r.style === style && !r.template_id && r.status === "ready" && !r.settings?.font);

/**
 * Every clip of the episode in one ZIP (posting order, plus a text file with titles and why each works). Choices: crop
 * or fit; captions none, each clip's own edits (clips without edits come without captions), or one style for all; the
 * letters. Captioned versions are made first (shown as progress), then the download starts.
 */
export default function DownloadAll({ episode, templates, refresh }) {
  const [open, setOpen] = useState(false);
  const [style, setStyle] = useState("crop");
  const [captions, setCaptions] = useState("edits"); // none | edits | <template id>
  const [script, setScript] = useState("telugu");
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState(null);
  const clips = episode.clips || [];
  const withEdits = clips.filter((c) => c.draft).length;

  useEffect(() => {
    if (!open) return undefined;
    const k = (e) => e.key === "Escape" && !progress && setOpen(false);
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, progress]);

  const start = async () => {
    setError(null);
    // the newest state of every clip (a clip may have been re-made since this page loaded)
    const fresh = (refresh ? (await refresh())?.data : null) || episode;
    const clips = fresh.clips || [];
    const missing = clips.filter((c) => !plainOf(c, style));
    if (missing.length) {
      setError(`${missing.length} clip video(s) are still being made. Try again in a minute.`);
      return;
    }
    let done = 0;
    setProgress(`Preparing 0 of ${clips.length} clips…`);
    try {
      const ids = await Promise.all(
        clips.map(async (c) => {
          const plain = plainOf(c, style).id;
          let body = { style };
          if (captions === "edits" && c.draft) {
            body = { style, template_id: c.draft.template_id, settings: { ...c.draft.settings, script }, words: c.draft.words, breaks: c.draft.breaks };
          } else if (captions !== "none" && captions !== "edits") {
            body = { style, template_id: captions, settings: { script } };
          }
          const id = await readyRender(c.id, body, plain);
          done += 1;
          setProgress(`Preparing ${done} of ${clips.length} clips…`);
          return id;
        }),
      );
      setProgress("Downloading…");
      await downloadZip(episode.id, ids);
      setTimeout(() => {
        setProgress(null);
        setOpen(false);
      }, 1500);
    } catch (e) {
      setError(e.message);
      setProgress(null);
    }
  };

  const Choice = ({ value, set, options, label }) => (
    <div>
      <p className="text-sm text-k-muted mb-1.5">{label}</p>
      <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={label}>
        {options.map(([k, text]) => (
          <button
            key={k}
            type="button"
            role="radio"
            aria-checked={value === k}
            onClick={() => set(k)}
            className={`rounded-lg border px-3 py-1.5 text-sm ${value === k ? "border-k-accent bg-k-accent/10 text-k-text font-semibold" : "border-k-line text-k-muted hover:text-k-text"}`}
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className="inline-flex items-center gap-2 rounded-lg border-[1.5px] border-k-text px-4 py-2 font-semibold hover:bg-k-raised">
        <Download size={17} aria-hidden="true" />
        Download all {clips.length}
      </button>
      {open && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onClick={() => !progress && setOpen(false)} role="presentation">
          <div role="dialog" aria-modal="true" aria-labelledby="dl-all-title" className="w-full max-w-md rounded-2xl border border-k-line bg-k-surface p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 id="dl-all-title" className="font-semibold text-lg">
                Download all {clips.length} clips
              </h2>
              <button type="button" onClick={() => !progress && setOpen(false)} className="p-1 text-k-muted hover:text-k-text" aria-label="Close">
                <X size={18} />
              </button>
            </div>
            <div className="space-y-4">
              <Choice label="Picture" value={style} set={setStyle} options={[["crop", "Crop"], ["fit", "Fit"]]} />
              <div>
                <p className="text-sm text-k-muted mb-1.5">Captions</p>
                <select value={captions} onChange={(e) => setCaptions(e.target.value)} className="w-full rounded-lg bg-k-bg border border-k-line px-2.5 py-2 text-sm">
                  <option value="edits">Each clip as edited ({withEdits} edited; the others without captions)</option>
                  <option value="none">No captions</option>
                  {(templates || []).map((t) => (
                    <option key={t.id} value={t.id}>
                      One style for all: {templateLabel(t).name}
                    </option>
                  ))}
                </select>
              </div>
              {captions !== "none" && (
                <Choice label="Captions in" value={script} set={setScript} options={[["telugu", "Telugu letters (ఏంటి)"], ["latin", "English letters (enti)"]]} />
              )}
              <p className="text-sm text-k-subtle">One ZIP, in the order to post them, with a text file of titles and why each clip works.</p>
              {error && (
                <p role="alert" className="text-sm text-k-bad">
                  {error}
                </p>
              )}
              <button
                type="button"
                onClick={start}
                disabled={Boolean(progress)}
                className="w-full inline-flex items-center justify-center gap-2 rounded-lg bg-k-accent text-k-ink font-bold py-2.5 hover:bg-k-accent2 disabled:opacity-70"
              >
                {progress ? <Loader2 size={17} className="animate-spin" aria-hidden="true" /> : <Download size={17} aria-hidden="true" />}
                {progress || "Download ZIP"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
