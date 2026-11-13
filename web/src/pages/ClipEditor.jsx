import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Download, Keyboard, Loader2, Pause, Play, Redo2, Scissors, ShieldCheck, Star, Undo, Undo2 } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Shell from "@/components/klipvi/Shell";
import CaptionOverlay, { CaptionBlock, useVideoTime } from "@/components/klipvi/CaptionOverlay";
import TeluguTextarea from "@/components/klipvi/TeluguTextarea";
import TimingPanel from "@/components/klipvi/TimingPanel";
import Timeline, { fmtT } from "@/components/klipvi/Timeline";
import { API_BASE_URL } from "@/api/client";
import { OUT_W } from "@/lib/captionEngine";
import { downloadClip, getClipEditor, renderLinks, saveDraft, setTiming } from "@/api/klipvi";
import { templateLabel, useCaptionTemplates } from "@/hooks/useKlipvi";
import { DEFAULT_POS_Y, FONTS, cueAt, cues as buildCues, withDefaults } from "@/lib/captionEngine";
import { romanize } from "@/lib/romanize";

const fmt = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;


/** Replace the words of one caption line with new text. Same number of words keeps their times; otherwise the line's
 *  time span is shared evenly. Empty text removes the line's words. Line splits after the line shift with it. */
export function editLine(words, breaks, first, count, text) {
  const toks = text.split(/\s+/).map((x) => x.trim()).filter(Boolean);
  const old = words.slice(first, first + count);
  let repl;
  if (toks.length === old.length) {
    repl = old.map((w, i) => ({ ...w, tok: toks[i] }));
  } else if (toks.length) {
    const a = old[0].start;
    const b = old[old.length - 1].end;
    const step = (b - a) / toks.length;
    repl = toks.map((tok, i) => ({ tok, start: +(a + i * step).toFixed(3), end: +(a + (i + 1) * step).toFixed(3) }));
  } else {
    repl = [];
  }
  const delta = repl.length - count;
  const nextWords = [...words.slice(0, first), ...repl, ...words.slice(first + count)];
  const nextBreaks = (breaks || [])
    .filter((b) => b <= first || b >= first + count) // splits inside the edited line go (its words changed)
    .map((b) => (b >= first + count ? b + delta : b)) // splits after it move with the words
    .filter((b) => b > 0 && b < nextWords.length);
  return { words: nextWords, breaks: [...new Set(nextBreaks)].sort((x, y) => x - y) };
}

function Row({ label, children, htmlFor }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <label htmlFor={htmlFor} className="text-sm text-k-muted">
        {label}
      </label>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}

const range = (id, value, min, max, onChange) => (
  <input id={id} type="range" min={min} max={max} value={value} onChange={(e) => onChange(Number(e.target.value))} className="w-28 accent-[rgb(var(--k-accent))]" />
);

const hex6 = (c) => (c || "#000000").slice(0, 7);
const alphaOf = (c) => (c && c.length === 9 ? Math.round((parseInt(c.slice(7, 9), 16) / 255) * 100) : 100);
const withAlpha = (c, pct) => `${hex6(c)}${Math.round((pct / 100) * 255).toString(16).padStart(2, "0").toUpperCase()}`;

/** A style's look on this clip: a still from the clip with one of its own caption lines, the second word "spoken". */
function StyleTile({ template, sample, script, active, onPick }) {
  const s = useMemo(() => withDefaults({ ...template.settings, script }), [template.settings, script]);
  const { name, hint } = templateLabel(template);
  const cue = useMemo(() => (sample ? buildCues(sample.words, s)[0] : null), [sample, s]);
  const t = cue ? cue.words[Math.min(1, cue.words.length - 1)].start + 0.02 : 0;
  // the tile is the video's width in miniature, so a line fits it exactly as it fits the video
  const box = useRef(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!box.current) return undefined;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);
  return (
    <button
      type="button"
      title={hint}
      aria-pressed={active}
      onClick={onPick}
      className={`group rounded-xl border text-left overflow-hidden ${active ? "border-k-accent ring-2 ring-k-accent/40" : "border-k-line hover:border-k-line2"}`}
    >
      <div ref={box} className="relative h-[62px] overflow-hidden bg-[#11131C] border-b border-k-line" aria-hidden="true">
        {cue && w > 0 && <CaptionBlock cue={cue} t={t} settings={s} scale={w / OUT_W} style={{ left: "50%", top: "50%", transform: "translate(-50%, -50%)", pointerEvents: "none" }} />}
      </div>
      <p className={`px-2 py-1.5 text-[0.8rem] leading-tight ${active ? "text-k-text font-semibold" : "text-k-muted group-hover:text-k-text"}`}>{name}</p>
    </button>
  );
}

function StylePanel({ settings, set, templates, templateId, pickTemplate, style, sample, poster }) {
  const s = settings;
  const telugu = FONTS.filter((f) => f.telugu);
  const english = FONTS.filter((f) => !f.telugu);
  const [tab, setTab] = useState("styles");
  return (
    <div>
      <div className="sticky -top-4 z-10 -mx-4 -mt-4 mb-4 px-4 pt-4 pb-3 bg-k-surface border-b border-k-line" role="tablist" aria-label="Caption look">
        <div className="mb-3">
          <p className="text-xs text-k-subtle mb-1.5">Captions in</p>
          <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Captions in">
            {[
              ["telugu", "Telugu letters", "ఏంటి"],
              ["latin", "English letters", "enti"],
            ].map(([k, label, eg]) => (
              <button
                key={k}
                type="button"
                role="radio"
                aria-checked={(s.script || "telugu") === k}
                onClick={() => set({ script: k })}
                className={`rounded-lg border px-2.5 py-1.5 text-left ${(s.script || "telugu") === k ? "border-k-accent bg-k-accent/10" : "border-k-line hover:border-k-line2"}`}
              >
                <span className="block text-sm font-semibold text-k-text">{label}</span>
                <span className="block text-xs text-k-muted">{eg}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 rounded-lg bg-k-bg p-1">
          {[
            ["styles", "Styles"],
            ["customize", "Customize"],
          ].map(([k, label]) => (
            <button
              key={k}
              type="button"
              role="tab"
              aria-selected={tab === k}
              onClick={() => setTab(k)}
              className={`rounded-md py-1.5 text-sm font-semibold ${tab === k ? "bg-k-raised text-k-text shadow-sm" : "text-k-muted hover:text-k-text"}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {tab === "styles" ? (
      <section>
        <p className="text-sm text-k-subtle mb-3">Each style is shown on a line from this clip. Pick one, then fine-tune it under Customize.</p>
        <div className="grid grid-cols-2 gap-2">
          {(templates || []).map((t) => (
            <StyleTile key={t.id} template={t} sample={sample} script={s.script} active={templateId === t.id} onPick={() => pickTemplate(t)} />
          ))}
        </div>
      </section>
      ) : (
      <div className="space-y-6">

      <section>
        <h2 className="font-semibold mb-1">Text</h2>
        <Row label="Font" htmlFor="cap-font">
          <select id="cap-font" value={s.font} onChange={(e) => set({ font: e.target.value })} className="w-40 rounded-md bg-k-surface border border-k-line px-2 py-1 text-sm">
            <optgroup label="Telugu and English">
              {telugu.map((f) => (
                <option key={f.file} value={f.file}>
                  {f.family}
                </option>
              ))}
            </optgroup>
            <optgroup label="English only (no Telugu letters)">
              {english.map((f) => (
                <option key={f.file} value={f.file}>
                  {f.family}
                </option>
              ))}
            </optgroup>
          </select>
        </Row>
        <Row label={`Size (${s.size})`} htmlFor="cap-size">{range("cap-size", s.size, 36, 160, (v) => set({ size: v }))}</Row>
        <Row label="Colour" htmlFor="cap-colour">
          <input id="cap-colour" type="color" value={hex6(s.colour)} onChange={(e) => set({ colour: e.target.value.toUpperCase() })} />
        </Row>
        <Row label="Highlight the spoken word" htmlFor="cap-hl">
          <input id="cap-hl" type="checkbox" checked={Boolean(s.highlight)} onChange={(e) => set({ highlight: e.target.checked ? "#FFD400" : null })} />
          {s.highlight && <input type="color" aria-label="Highlight colour" value={hex6(s.highlight)} onChange={(e) => set({ highlight: e.target.value.toUpperCase() })} />}
        </Row>
        <Row label="Box behind the spoken word" htmlFor="cap-wbox">
          <input id="cap-wbox" type="checkbox" checked={Boolean(s.highlight_box)} onChange={(e) => set({ highlight_box: e.target.checked ? "#FFD400" : null })} />
          {s.highlight_box && <input type="color" aria-label="Word box colour" value={hex6(s.highlight_box)} onChange={(e) => set({ highlight_box: e.target.value.toUpperCase() })} />}
        </Row>
        <Row label="Keyword colour" htmlFor="cap-emph">
          <input id="cap-emph" type="checkbox" checked={Boolean(s.emphasis)} onChange={(e) => set({ emphasis: e.target.checked ? "#FFC93C" : null })} />
          {s.emphasis && <input type="color" aria-label="Keyword colour" value={hex6(s.emphasis)} onChange={(e) => set({ emphasis: e.target.value.toUpperCase() })} />}
        </Row>
        <Row label="English words in capitals" htmlFor="cap-upper">
          <input id="cap-upper" type="checkbox" checked={s.uppercase_latin} onChange={(e) => set({ uppercase_latin: e.target.checked })} />
        </Row>
      </section>

      <section>
        <h2 className="font-semibold mb-1">Edges</h2>
        <Row label="Box behind the text" htmlFor="cap-box">
          <input id="cap-box" type="checkbox" checked={Boolean(s.box)} onChange={(e) => set({ box: e.target.checked ? "#000000B3" : null })} />
          {s.box && <input type="color" aria-label="Box colour" value={hex6(s.box)} onChange={(e) => set({ box: withAlpha(e.target.value, alphaOf(s.box)) })} />}
        </Row>
        {s.box && <Row label={`Box opacity (${alphaOf(s.box)}%)`} htmlFor="cap-box-a">{range("cap-box-a", alphaOf(s.box), 10, 100, (v) => set({ box: withAlpha(s.box, v) }))}</Row>}
        {!s.box && (
          <>
            <Row label={`Outline (${s.outline})`} htmlFor="cap-outline">
              {range("cap-outline", s.outline, 0, 12, (v) => set({ outline: v }))}
              <input type="color" aria-label="Outline colour" value={hex6(s.outline_colour)} onChange={(e) => set({ outline_colour: e.target.value.toUpperCase() })} />
            </Row>
            <Row label={`Glow (${s.glow || 0})`} htmlFor="cap-glow">{range("cap-glow", s.glow || 0, 0, 20, (v) => set({ glow: v }))}</Row>
            <Row label={`Shadow (${s.shadow})`} htmlFor="cap-shadow">{range("cap-shadow", s.shadow, 0, 12, (v) => set({ shadow: v }))}</Row>
          </>
        )}
      </section>

      <section>
        <h2 className="font-semibold mb-1">Layout</h2>
        <Row label="Animation" htmlFor="cap-anim">
          <select id="cap-anim" value={s.animation} onChange={(e) => set({ animation: e.target.value })} className="rounded-md bg-k-surface border border-k-line px-2 py-1 text-sm">
            <option value="none">None</option>
            <option value="fade">Fade in</option>
            <option value="pop">Pop on the spoken word</option>
            <option value="reveal">Appear as spoken</option>
          </select>
        </Row>
        <Row label="Lines at a time" htmlFor="cap-lines">
          <select id="cap-lines" value={s.max_lines} onChange={(e) => set({ max_lines: Number(e.target.value) })} className="rounded-md bg-k-surface border border-k-line px-2 py-1 text-sm">
            {[1, 2, 3].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </Row>
        <Row label="Words per line" htmlFor="cap-wpl">
          <select id="cap-wpl" value={s.words_per_line} onChange={(e) => set({ words_per_line: Number(e.target.value) })} className="rounded-md bg-k-surface border border-k-line px-2 py-1 text-sm">
            <option value={0}>Fit the width</option>
            {[1, 2, 3, 4, 5, 6].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </Row>
        <p className="text-sm text-k-subtle mt-2">Drag the caption on the video to move it; drag a corner to make it bigger or smaller.</p>
        <button
          type="button"
          onClick={() => set({ pos_x: 0.5, pos_y: null })}
          className="mt-2 text-sm text-k-accentsoft hover:underline disabled:opacity-50"
          disabled={s.pos_x === 0.5 && s.pos_y == null}
        >
          Put it back at the bottom ({Math.round(DEFAULT_POS_Y[style] * 100)}% down)
        </button>
      </section>
      </div>
      )}
    </div>
  );
}

function Transcript({ words, breaks, settings, time, seek, setEdits, typeTelugu }) {
  const cs = useMemo(() => buildCues(words, settings, breaks), [words, settings, breaks]);
  const index = useMemo(() => new Map(words.map((w, i) => [w, i])), [words]);
  const [splitting, setSplitting] = useState(null); // cue index showing its words to split at
  const [marking, setMarking] = useState(null); // cue index showing its words to mark as keywords
  const brk = new Set(breaks);
  return (
    <ol className="space-y-1.5">
      {cs.map((c, ci) => {
        const first = index.get(c.words[0]);
        const on = time >= c.start && time < c.end;
        const text = c.words.map((w) => (settings.script === "latin" ? romanize(w.tok) : w.tok)).join(" ");
        return (
          <li key={`${first}-${c.words.length}`} className={`group rounded-lg px-2 py-1.5 ${on ? "bg-k-raised" : "hover:bg-k-raised/40"}`} data-on={on || undefined}>
            <div className="flex items-start gap-2">
              <button type="button" onClick={() => seek(c.start)} className="shrink-0 w-11 pt-1.5 text-left text-xs tabular-nums text-k-subtle hover:text-k-accentsoft" aria-label={`Play from ${fmt(c.start)}`}>
                {fmt(c.start)}
              </button>
              <TeluguTextarea
                key={text}
                value={text}
                telugu={typeTelugu}
                aria-label={`Caption line at ${fmt(c.start)}`}
                onCommit={(v) => v !== text && setEdits((d) => editLine(d.words, d.breaks, first, c.words.length, v))}
                className="rounded-md bg-transparent border border-transparent hover:border-k-line focus:border-k-accent focus:outline-none px-2 py-1 text-[0.95rem] leading-normal"
              />
              <button
                type="button"
                onClick={() => setMarking(marking === ci ? null : ci)}
                className={`shrink-0 p-1.5 hover:text-k-text ${c.words.some((w) => w.emph) ? "text-k-accentsoft" : "text-k-subtle opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 group-data-[on]:opacity-100 focus:opacity-100"}`}
                title="Mark keywords in this line"
                aria-label="Mark keywords in this line"
              >
                <Star size={15} />
              </button>
              <button type="button" onClick={() => setSplitting(splitting === ci ? null : ci)} className="shrink-0 p-1.5 text-k-subtle hover:text-k-text opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 group-data-[on]:opacity-100 focus:opacity-100" title="Split this line" aria-label="Split this line" disabled={c.words.length < 2}>
                <Scissors size={15} />
              </button>
              {brk.has(first) && (
                <button
                  type="button"
                  onClick={() => setEdits((d) => ({ ...d, breaks: d.breaks.filter((b) => b !== first) }))}
                  className="shrink-0 p-1.5 text-k-subtle hover:text-k-text"
                  title="Join with the line before"
                  aria-label="Join with the line before"
                >
                  <Undo2 size={15} />
                </button>
              )}
            </div>
            {marking === ci && (
              <div className="mt-1.5 ml-12 flex flex-wrap gap-1.5" role="group" aria-label="Keywords">
                <span className="text-xs text-k-subtle w-full">Tap the words to show in the keyword colour:</span>
                {c.words.map((w, k) => (
                  <button
                    key={k}
                    type="button"
                    aria-pressed={Boolean(w.emph)}
                    onClick={() =>
                      setEdits((d) => ({
                        ...d,
                        words: d.words.map((x, i) => (i === first + k ? { ...x, emph: !x.emph || undefined } : x)),
                      }))
                    }
                    className={`rounded-md border px-2 py-0.5 text-sm ${w.emph ? "border-k-accent bg-k-accent/15 text-k-accentsoft" : "border-k-line hover:border-k-line2"}`}
                  >
                    {w.tok}
                  </button>
                ))}
              </div>
            )}
            {splitting === ci && (
              <div className="mt-1.5 ml-12 flex flex-wrap gap-1.5" role="group" aria-label="Start a new line at">
                <span className="text-xs text-k-subtle w-full">Start a new line at:</span>
                {c.words.slice(1).map((w, k) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => {
                      setEdits((d) => ({ ...d, breaks: [...new Set([...d.breaks, first + k + 1])].sort((x, y) => x - y) }));
                      setSplitting(null);
                    }}
                    className="rounded-md border border-k-line px-2 py-0.5 text-sm hover:border-k-accent"
                  >
                    {w.tok}
                  </button>
                ))}
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** State with undo/redo: every change is a step; changes less than 0.6 s apart (a slider drag) make one step. */
function useUndoable() {
  const [state, setRaw] = useState(null);
  const ref = useRef(null);
  const past = useRef([]);
  const future = useRef([]);
  const last = useRef(0);
  const set = useCallback((upd) => {
    const cur = ref.current;
    const next = typeof upd === "function" ? upd(cur) : upd;
    if (next === cur) return;
    if (cur != null) {
      const now = Date.now();
      if (now - last.current > 600) {
        past.current.push(cur);
        if (past.current.length > 100) past.current.shift();
      }
      last.current = now;
      future.current = [];
    }
    ref.current = next;
    setRaw(next);
  }, []);
  const undo = useCallback(() => {
    if (!past.current.length) return;
    future.current.push(ref.current);
    ref.current = past.current.pop();
    last.current = 0;
    setRaw(ref.current);
  }, []);
  const redo = useCallback(() => {
    if (!future.current.length) return;
    past.current.push(ref.current);
    ref.current = future.current.pop();
    last.current = 0;
    setRaw(ref.current);
  }, []);
  const reset = useCallback(() => {
    past.current = [];
    future.current = [];
    last.current = 0;
    ref.current = null;
    setRaw(null);
  }, []);
  return [state, set, { undo, redo, reset, canUndo: past.current.length > 0, canRedo: future.current.length > 0 }];
}

const MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const MOD = MAC ? "⌘" : "Ctrl";
const SHORTCUTS = [
  ["Space", "Play or pause"],
  ["← →", "Back or forward 1 second"],
  ["Shift + ← →", "Back or forward 5 seconds"],
  [`${MOD} + Z`, "Undo"],
  [MAC ? "⌘ + Shift + Z" : "Ctrl + Y", "Redo"],
  ["?", "Show these shortcuts"],
];

function ShortcutsDialog({ onClose }) {
  useEffect(() => {
    const k = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onClick={onClose} role="presentation">
      <div role="dialog" aria-modal="true" aria-labelledby="kbd-title" className="w-full max-w-sm rounded-2xl border border-k-line bg-k-surface p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h2 id="kbd-title" className="font-semibold text-lg mb-3">Keyboard shortcuts</h2>
        <dl className="space-y-2">
          {SHORTCUTS.map(([k, what]) => (
            <div key={k} className="flex items-center justify-between gap-4">
              <dt className="text-k-muted">{what}</dt>
              <dd>
                <kbd className="rounded-md border border-k-line2 bg-k-bg px-2 py-0.5 text-sm text-k-text">{k}</kbd>
              </dd>
            </div>
          ))}
        </dl>
        <p className="mt-4 text-sm text-k-subtle">Shortcuts pause while you type in a caption line.</p>
        <button type="button" onClick={onClose} className="mt-4 w-full rounded-lg bg-k-raised py-2 font-semibold hover:bg-k-line">
          Close
        </button>
      </div>
    </div>
  );
}

export default function ClipEditor() {
  const { clipId } = useParams();
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["clip-editor", clipId],
    queryFn: () => getClipEditor(clipId),
    staleTime: 0,
    refetchInterval: (q) => (q.state.data?.remaking?.length ? 3000 : false), // the new version is being made
  });
  const { data: templates } = useCaptionTemplates(data?.clip?.workspace_id);
  const [state, setState, history] = useUndoable(); // { style, template_id, settings, words, breaks }
  const [showKeys, setShowKeys] = useState(false);
  const [leftTab, setLeftTab] = useState("captions"); // captions | timing
  const [tParts, setTParts] = useState(null); // the parts of the episode that play, being edited ({start, end} s)
  const [scrub, setScrub] = useState(null); // an episode moment shown in the player while trimming (null = the clip)
  const sourceRef = useRef(null);
  const savedPieces = data?.timing?.pieces;
  const resetParts = useCallback(() => {
    if (savedPieces?.length) setTParts(savedPieces.map((p) => ({ start: p.start, end: p.end })));
  }, [savedPieces]);
  useEffect(resetParts, [resetParts]);
  useEffect(() => {
    if (scrub != null && sourceRef.current) sourceRef.current.currentTime = scrub;
  }, [scrub]);
  const [timingBusy, setTimingBusy] = useState(false);
  const applyTiming = async (parts) => {
    setTimingBusy(true);
    setProblem(null);
    try {
      await setTiming(clipId, parts);
      first.current = true; // reloading the clip is not an edit: don't save a draft for it
      history.reset(); // the clip changed: start again from the carried-over draft
      await qc.invalidateQueries({ queryKey: ["clip-editor", clipId] });
      qc.invalidateQueries({ queryKey: ["episode"] });
    } catch (e) {
      setProblem(e.message);
    } finally {
      setTimingBusy(false);
    }
  };
  const [links, setLinks] = useState(null);
  const [saving, setSaving] = useState("");
  const [busy, setBusy] = useState(null);
  const [problem, setProblem] = useState(null);
  const [safe, setSafe] = useState(false);
  const [platform, setPlatform] = useState("instagram"); // whose buttons the safe zones show: none | all | instagram | youtube
  const [typeTelugu, setTypeTelugu] = useState(true); // typing English letters gives Telugu suggestions
  const scriptNow = state?.settings?.script;
  useEffect(() => {
    if (scriptNow) setTypeTelugu(scriptNow !== "latin");
  }, [scriptNow]);
  const [playing, setPlaying] = useState(false);
  const videoRef = useRef(null);
  const time = useVideoTime(videoRef);
  const first = useRef(true);

  // start from the saved draft, else the "Clean" style with the clip's own words
  useEffect(() => {
    if (!data || !templates || state) return;
    const d = data.draft;
    const clean = templates.find((t) => t.name === "clean") || templates[0];
    setState(
      d
        ? { style: d.style, template_id: d.template_id, settings: withDefaults(d.settings), words: d.words || data.words, breaks: d.breaks || [] }
        : { style: "crop", template_id: clean?.id || null, settings: withDefaults(clean?.settings), words: data.words, breaks: [] },
    );
  }, [data, templates, state]);

  // the plain video for the chosen picture style
  const videoId = state && data?.videos?.[state.style];
  const remaking = Boolean(data?.remaking?.length);
  useEffect(() => {
    let live = true;
    setLinks(null);
    if (!videoId) return undefined;
    renderLinks(videoId)
      .then((l) => live && setLinks(l))
      .catch((e) => live && e.status !== 409 && setProblem(e.message)); // 409: being made again, the poll brings it back
    return () => {
      live = false;
    };
  }, [videoId]);

  // save the draft a moment after each change
  useEffect(() => {
    if (!state) return undefined;
    if (first.current) {
      first.current = false;
      return undefined;
    }
    setSaving("Saving…");
    const h = setTimeout(() => {
      saveDraft(clipId, state)
        .then(() => setSaving("Saved"))
        .catch((e) => setSaving(e.message));
    }, 800);
    return () => clearTimeout(h);
  }, [state, clipId]);

  const set = useCallback((patch) => setState((s) => ({ ...s, settings: { ...s.settings, ...patch } })), []);
  const pickTemplate = (t) =>
    setState((s) => ({
      ...s,
      template_id: t.id,
      settings: { ...withDefaults(t.settings), pos_x: s.settings.pos_x, pos_y: s.settings.pos_y, script: s.settings.script },
    }));
  const setEdits = (fn) => setState((s) => ({ ...s, ...fn({ words: s.words, breaks: s.breaks }) }));
  const seek = (t) => {
    if (videoRef.current) videoRef.current.currentTime = t + 0.01;
  };
  const toggle = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) v.play();
    else v.pause();
  };

  // keyboard shortcuts (not while typing in a caption line or another field)
  useEffect(() => {
    const onKey = (e) => {
      const el = e.target;
      const typing = el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
      const mod = e.metaKey || e.ctrlKey;
      if (typing) return;
      const k = e.key.toLowerCase();
      if (mod && k === "z" && !e.shiftKey) {
        e.preventDefault();
        history.undo();
      } else if ((mod && k === "z" && e.shiftKey) || (e.ctrlKey && k === "y")) {
        e.preventDefault();
        history.redo();
      } else if (e.key === " " && el.tagName !== "BUTTON") {
        e.preventDefault();
        toggle();
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        const v = videoRef.current;
        if (!v) return;
        e.preventDefault();
        const step = (e.shiftKey ? 5 : 1) * (e.key === "ArrowLeft" ? -1 : 1);
        v.currentTime = Math.max(0, Math.min(v.duration || 1e9, v.currentTime + step));
      } else if (e.key === "?") {
        setShowKeys(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [history]); // eslint-disable-line react-hooks/exhaustive-deps

  const download = async () => {
    setProblem(null);
    try {
      await downloadClip(
        clipId,
        { style: state.style, template_id: state.template_id, settings: state.settings, words: state.words, breaks: state.breaks },
        videoId,
        setBusy,
      );
    } catch (e) {
      setProblem(e.message);
    } finally {
      setTimeout(() => setBusy(null), 1500);
    }
  };

  const clip = data?.clip;
  // the line the style previews show: the one playing now, else the first line with a few words
  const sample = useMemo(() => {
    if (!state) return null;
    const cs = buildCues(state.words, withDefaults({}), state.breaks);
    const c = cueAt(cs, time) || cs.find((x) => x.words.length >= 3) || cs[0];
    return c ? { words: c.words } : null;
  }, [state, Math.floor(time)]); // eslint-disable-line react-hooks/exhaustive-deps
  const duration = videoRef.current?.duration || clip?.length_s || 0;

  return (
    <Shell
      compact
      title={clip ? `${clip.rank}. ${clip.title}` : " "}
      back={clip ? { to: `/episodes/${clip.episode_id}`, label: "All clips" } : { to: "/dashboard", label: "Episodes" }}
      actions={
        state && (
          <div className="flex items-center gap-3">
            <span className="text-sm text-k-subtle" role="status">{saving}</span>
            <div className="inline-flex items-center rounded-lg border border-k-line bg-k-surface">
              <button type="button" onClick={history.undo} disabled={!history.canUndo} className="p-2 text-k-muted hover:text-k-text disabled:opacity-35" title={`Undo (${MOD} + Z)`} aria-label="Undo">
                <Undo size={17} />
              </button>
              <button type="button" onClick={history.redo} disabled={!history.canRedo} className="p-2 text-k-muted hover:text-k-text disabled:opacity-35 border-l border-k-line" title={MAC ? "Redo (⌘ + Shift + Z)" : "Redo (Ctrl + Y)"} aria-label="Redo">
                <Redo2 size={17} />
              </button>
              <button type="button" onClick={() => setShowKeys(true)} className="p-2 text-k-muted hover:text-k-text border-l border-k-line" title="Keyboard shortcuts (?)" aria-label="Keyboard shortcuts">
                <Keyboard size={17} />
              </button>
            </div>
            <div className="inline-flex rounded-lg border border-k-line overflow-hidden" role="group" aria-label="Picture style">
              {["crop", "fit"].map((st) => (
                <button
                  key={st}
                  type="button"
                  aria-pressed={state.style === st}
                  onClick={() => setState((s) => ({ ...s, style: st }))}
                  className={`px-3 py-1.5 text-sm capitalize ${state.style === st ? "bg-k-text text-k-bg font-semibold" : "bg-k-surface text-k-muted hover:text-k-text"}`}
                >
                  {st}
                </button>
              ))}
            </div>
            <button type="button" onClick={download} disabled={!videoId || Boolean(busy)} className="inline-flex items-center gap-2 rounded-lg bg-k-accent text-k-ink font-bold px-4 py-2 hover:bg-k-accent2 disabled:opacity-60">
              {busy ? <Loader2 size={17} className="animate-spin" aria-hidden="true" /> : <Download size={17} aria-hidden="true" />}
              {busy || "Download"}
            </button>
          </div>
        )
      }
    >
      {showKeys && <ShortcutsDialog onClose={() => setShowKeys(false)} />}
      {isLoading && <p className="mt-6 text-k-muted">Opening the clip…</p>}
      {(error || problem) && <p role="alert" className="mt-4 text-k-bad">{(error || {}).message || problem}</p>}
      {/* wide screens: a fixed workspace; the video keeps its place, each side panel scrolls on its own */}
      {state && (
        <div className="mt-4 grid gap-5 xl:gap-6 lg:grid-cols-[minmax(240px,320px)_minmax(300px,1fr)_minmax(260px,310px)] lg:h-[calc(100dvh-5.75rem)]">
          <section aria-label="Captions, line by line" className="order-3 lg:order-1 lg:h-full lg:overflow-y-auto overscroll-contain rounded-2xl border border-k-line bg-k-surface p-3">
            {/* the tabs stay in view while the panel scrolls */}
            <div className="lg:sticky lg:-top-3 z-20 -mx-3 -mt-3 px-3 pt-3 pb-3 bg-k-surface rounded-t-2xl">
            <div className="grid grid-cols-2 rounded-lg bg-k-bg p-1" role="tablist" aria-label="Edit">
              {[
                ["captions", "Captions"],
                ["timing", "Timing"],
              ].map(([k, label]) => (
                <button
                  key={k}
                  type="button"
                  role="tab"
                  aria-selected={leftTab === k}
                  onClick={() => setLeftTab(k)}
                  className={`rounded-md py-1.5 text-sm font-semibold ${leftTab === k ? "bg-k-raised text-k-text shadow-sm" : "text-k-muted hover:text-k-text"}`}
                >
                  {label}
                </button>
              ))}
            </div>
            </div>
            {leftTab === "timing" && data?.timing && tParts ? (
              <TimingPanel timing={data.timing} busy={timingBusy || remaking} onApply={applyTiming} parts={tParts} setParts={setTParts} />
            ) : (
              <>
            <div className="flex items-center justify-between gap-3 px-2 pb-2">
              <h2 className="font-semibold">Captions</h2>
              <label className="inline-flex items-center gap-2 text-sm text-k-muted cursor-pointer" title="Type a word in English letters and pick its Telugu spelling">
                <input type="checkbox" checked={typeTelugu} onChange={(e) => setTypeTelugu(e.target.checked)} className="accent-[rgb(var(--k-accent))]" />
                Type in Telugu
              </label>
            </div>
            <p className="px-2 pb-3 text-sm text-k-subtle">
              Click a line to fix a word{typeTelugu ? " (type in English letters, e.g. enti, and pick ఏంటి)" : ""}. Use the scissors to start a new line at a word.
              {state.settings.script === "latin" && " In English letters, type the spelling you want and it is kept as typed."}
            </p>
            <Transcript
              words={state.words}
              breaks={state.breaks}
              settings={state.settings}
              time={time}
              seek={seek}
              setEdits={setEdits}
              typeTelugu={typeTelugu}
            />
              </>
            )}
          </section>

          <section aria-label="Preview" className="order-1 lg:order-2 lg:h-full min-h-0 flex flex-col items-center">
            <div className="relative aspect-[9/16] w-full max-w-[420px] lg:w-auto lg:max-w-full lg:h-[calc(100%-11rem)] rounded-xl overflow-hidden bg-black ring-1 ring-k-line">
              {links ? (
                <video
                  ref={videoRef}
                  src={links.url}
                  poster={links.poster || undefined}
                  playsInline
                  preload="metadata"
                  onClick={toggle}
                  onPlay={() => setPlaying(true)}
                  onPause={() => setPlaying(false)}
                  className="w-full h-full object-cover bg-black"
                />
              ) : (
                <div className="absolute inset-0 grid place-items-center px-6 text-center text-k-muted">
                  {remaking ? (
                    <span className="inline-flex items-center gap-2">
                      <Loader2 size={18} className="animate-spin" aria-hidden="true" />
                      Making the new version of this clip… about a minute
                    </span>
                  ) : (
                    "Loading video…"
                  )}
                </div>
              )}
              {scrub != null && data?.timing?.source_url && (
                <div className="absolute inset-0 z-30 bg-black grid place-items-center" aria-live="polite">
                  <video ref={sourceRef} src={`${API_BASE_URL}${data.timing.source_url}`} muted playsInline preload="auto" className="w-full" />
                  <span className="absolute top-3 left-3 right-3 rounded-md bg-black/70 px-2.5 py-1 text-sm text-white">
                    Preview: the episode at <span className="tabular-nums font-semibold">{fmtT(scrub)}</span>
                  </span>
                </div>
              )}
              {links && (
                <CaptionOverlay
                  videoRef={videoRef}
                  words={state.words}
                  settings={state.settings}
                  breaks={state.breaks}
                  style={state.style}
                  draggable
                  onMove={(p) => set(p)}
                  onResize={(size) => set({ size })}
                  showZones={safe}
                  platform={platform}
                />
              )}

            </div>
            <div className="mt-3 flex items-center gap-3 w-full max-w-[420px] lg:max-w-[min(100%,520px)]">
              <button type="button" onClick={toggle} className="rounded-full bg-k-text text-k-bg p-2" aria-label={playing ? "Pause" : "Play"}>
                {playing ? <Pause size={18} /> : <Play size={18} />}
              </button>
              <input
                type="range"
                min={0}
                max={duration || 1}
                step={0.05}
                value={Math.min(time, duration || 1)}
                onChange={(e) => seek(Number(e.target.value))}
                className="flex-1 accent-[rgb(var(--k-accent))]"
                aria-label="Position in the clip"
              />
              <span className="text-sm tabular-nums text-k-muted whitespace-nowrap text-right">
                {fmt(time)} / {fmt(duration)}
              </span>
              <button
                type="button"
                onClick={() => setSafe((x) => !x)}
                aria-pressed={safe}
                title="Safe zones: where Instagram and YouTube put their buttons"
                className={`rounded-md p-1.5 ${safe ? "text-k-mark" : "text-k-subtle hover:text-k-text"}`}
              >
                <ShieldCheck size={18} />
              </button>
            </div>
            {data?.timing?.window && tParts && (
              <div className="mt-2 w-full max-w-[680px]">
                <Timeline
                  timing={data.timing}
                  stripUrl={`${API_BASE_URL}${data.timing.strip_url}`}
                  parts={tParts}
                  setParts={setTParts}
                  clipTime={time}
                  onScrub={setScrub}
                  busy={timingBusy || remaking}
                  onApply={applyTiming}
                  onReset={resetParts}
                />
              </div>
            )}
            {safe && (
              <div className="mt-1.5 flex flex-wrap items-center justify-center gap-2 text-sm text-k-muted">
                <span>Show what covers the video on</span>
                <div className="inline-flex rounded-md border border-k-line overflow-hidden" role="group" aria-label="Safe zones for">
                  {[
                    ["none", "None"],
                    ["all", "All"],
                    ["instagram", "Instagram"],
                    ["youtube", "YouTube"],
                  ].map(([k, label]) => (
                    <button
                      key={k}
                      type="button"
                      aria-pressed={platform === k}
                      onClick={() => setPlatform(k)}
                      className={`px-2 py-0.5 ${platform === k ? "bg-k-text text-k-bg font-semibold" : "bg-k-surface hover:text-k-text"}`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </section>

          <section aria-label="Caption look" className="order-2 lg:order-3 lg:h-full lg:overflow-y-auto overscroll-contain rounded-2xl border border-k-line bg-k-surface p-4">
            <StylePanel
              settings={state.settings}
              set={set}
              templates={templates}
              templateId={state.template_id}
              pickTemplate={pickTemplate}
              style={state.style}
              sample={sample}
              poster={links?.poster}
            />
          </section>
        </div>
      )}
    </Shell>
  );
}
