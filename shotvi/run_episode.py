"""One command for a new Telugu web-series episode, from a YouTube link to the review page.

    python -m shotvi.run_episode <youtube_url_or_video_id> [--out runs/ep_<id>] [--max-ledger-inr N]
                                 [--skip-asr-fix] [--speaker-fix] [--sarvam] [--dry-run]

Steps (each one is skipped when its output already exists, so the command can be re-run after a failure):

  1. download    download_youtube.py  -> scribe_dataset/<id>/<id>.mp4, <id>_audio.wav, youtube_meta.json  (free)
  2. transcribe  transcribe_scribe.py -> scribe_dataset/<id>/<id>_scribe_transcript.json   (ElevenLabs credits,
                 ~66.7 per minute of audio; the script picks the key with enough credits)
  2b. sarvam    (opt-in, --sarvam; off by default, and then not listed at all)
                 tools/transcribe_sarvam.py <id> -> scribe_dataset/<id>/<id>_sarvam_raw.json (Sarvam saaras:v4
                 codemix diarization, Rs 0.59 per minute of audio; skipped when the raw file exists). The script
                 records the job in the project spend ledger (source "sarvam") and refuses to start over the limit;
                 the speaker layer (shotvi.segment.speaker_layer, source "sarvam") is built from it for free
  3. shots       ffmpeg select=gt(scene,0.25) on 320px frames -> runs/video/<id>/shots.json (+ ffmpeg_scene.log)
  4. segments    python -m shotvi.segment <transcript> --out runs/segment_r3/<id>/segments.json --intro rules
  5. asr_fix     python -m shotvi.segment.asr_fix <id>      (Gemini, ~₹2; --skip-asr-fix skips it)
  5b. speaker_fix (opt-in, --speaker-fix; off by default, and then not listed at all)
                 python -m shotvi.segment.speaker_fix <id> -> runs/speaker_fix/<id>.json (Gemini, ₹1.5-4.6 measured
                 per 13-34 min episode); step 6 then runs with --config shotvi/select/configs/ws_v1_speakers.json
                 (corrected speaker names in the discover and review prompts)
  6. select      python -m shotvi.select.webseries <id> --out <out> --no-retry  (Gemini, ~₹10-15), default config
  7. preview     python tools/preview_clips.py <out> --top 50 -> runs/previews/<out name>/review.html
  8. summary     title, length, credits, rupees (project ledger before/after), clips per tier, verdict, page path

Spend guard: a paid Gemini step is refused (exit code 3) when the project ledger plus the step's own projection
(its ``--dry-run``) would exceed ``--max-ledger-inr`` (default: SHOTVI_SPEND_CAP_INR from .env). ``--dry-run``
checks the free steps and prints the paid steps' projections without calling any provider (it only runs the
existing dry-run modes of asr_fix and webseries, which are free).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

CREDITS_PER_MIN = 66.7            # ElevenLabs Scribe (transcribe_scribe.CREDITS_PER_MIN)
SCENE_THRESHOLD = 0.25
SCENE_WIDTH = 320
ASR_FIX_INR_FALLBACK = 2.0        # used only when the transcript is missing, so asr_fix cannot project itself
SELECT_INR_FALLBACK = 15.0        # idem for the clip pipeline (measured ₹13-16 per 20-min episode)
SPEAKER_FIX_INR_FALLBACK = 15.0   # idem for speaker_fix (its own worst case is ~₹3.3 per ~110-line chunk)
SPEAKERS_CONFIG = "shotvi/select/configs/ws_v1_speakers.json"   # select config when --speaker-fix is on
SARVAM_INR_PER_MIN = 0.59         # tools/transcribe_sarvam.INR_PER_MIN (batch STT 0.49 + diarization 0.10)
EXIT_BUDGET_STOP = 2              # a sub-step stopped on its own ledger guard
EXIT_GUARD_REFUSED = 3            # this runner refused to start a paid step
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


# ----------------------------------------------------------------------------------------------- helpers
def video_id_of(arg: str) -> str:
    """Bare 11-character id, or the id of a youtube.com / youtu.be URL."""
    arg = arg.strip()
    if VIDEO_ID_RE.match(arg):
        return arg
    p = urlparse(arg)
    if p.netloc.endswith("youtu.be"):
        vid = p.path.lstrip("/").split("/")[0]
    else:
        vid = (parse_qs(p.query).get("v") or [""])[0]
        if not vid:
            parts = p.path.strip("/").split("/")
            vid = parts[1] if len(parts) >= 2 and parts[0] in ("shorts", "embed", "v", "live") else ""
    if not VIDEO_ID_RE.match(vid or ""):
        raise SystemExit(f"not a YouTube video id or URL: {arg}")
    return vid


def rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(p)


def run_cmd(args: list[str], capture: bool = False) -> str:
    """Run a step as a subprocess from the repo root (the scripts use relative paths). Returns stdout when
    ``capture`` (and still echoes it), else streams. Raises CalledProcessError on a non-zero exit."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    if capture:
        r = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if r.stdout:
            print("    " + r.stdout.strip().replace("\n", "\n    "))
        if r.returncode != 0:
            if r.stderr:
                print(r.stderr.strip()[-1500:], file=sys.stderr)
            raise subprocess.CalledProcessError(r.returncode, args, r.stdout, r.stderr)
        return r.stdout
    r = subprocess.run(args, cwd=ROOT, env=env)
    if r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, args)
    return ""


def ledger_inr() -> float:
    """Project LLM ledger in rupees (what ``python -m shotvi.llm.spend`` prints)."""
    from shotvi.llm.spend import total_inr
    return total_inr()


def spend_cap_inr() -> float:
    from shotvi.llm.spend import cap_inr
    return cap_inr()


def media_duration_s(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                        str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def write_youtube_meta(url: str, path: Path) -> dict:
    """scribe_dataset/<id>/youtube_meta.json as the GT/segment tools expect it (yt_dlp extract_info, no download)."""
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    meta = {k: info.get(k) for k in ("id", "title", "channel", "channel_url", "duration", "upload_date",
                                     "description", "chapters", "view_count")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def scene_cuts(mp4: Path, log_path: Path) -> list[float]:
    """Shot changes (seconds) from ffmpeg's scene detector on 320px-wide frames; the ffmpeg log is kept."""
    vf = f"scale={SCENE_WIDTH}:-1,select='gt(scene,{SCENE_THRESHOLD})',showinfo"
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(mp4), "-vf", vf, "-an", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(r.stderr, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg scene detection failed (see {rel(log_path)}):\n{r.stderr[-600:]}")
    cuts = sorted({round(float(m), 3) for m in re.findall(r"pts_time:\s*([0-9.]+)", r.stderr)})
    return [c for c in cuts if c > 0]


def shots_doc(vid: str, cuts: list[float], duration: float) -> dict:
    """Same shape as the existing runs/video/<id>/shots.json files."""
    edges = [0.0] + [c for c in cuts if c < duration] + [duration]
    shots = [{"id": i, "start": a, "end": b, "dur": round(b - a, 3)}
             for i, (a, b) in enumerate(zip(edges, edges[1:]))]
    return {"video_id": vid, "method": f"ffmpeg select=gt(scene,{SCENE_THRESHOLD}) on {SCENE_WIDTH}px frames",
            "duration": duration, "cuts": cuts, "shots": shots}


def parse_inr(text: str, pattern: str) -> float | None:
    m = re.search(pattern, text)
    return float(m.group(1)) if m else None


# ----------------------------------------------------------------------------------------------- paths
@dataclass
class Paths:
    vid: str
    out: Path

    @property
    def src(self) -> Path:
        return ROOT / "scribe_dataset" / self.vid

    @property
    def mp4(self) -> Path:
        return self.src / f"{self.vid}.mp4"

    @property
    def wav(self) -> Path:
        return self.src / f"{self.vid}_audio.wav"

    @property
    def meta(self) -> Path:
        return self.src / "youtube_meta.json"

    @property
    def transcript(self) -> Path:
        return self.src / f"{self.vid}_scribe_transcript.json"

    @property
    def shots(self) -> Path:
        return ROOT / "runs" / "video" / self.vid / "shots.json"

    @property
    def scene_log(self) -> Path:
        return ROOT / "runs" / "video" / self.vid / "ffmpeg_scene.log"

    @property
    def segments(self) -> Path:
        return ROOT / "runs" / "segment_r3" / self.vid / "segments.json"

    @property
    def corrections(self) -> Path:
        return ROOT / "runs" / "asr_fix" / self.vid / "corrections.json"

    @property
    def speakers(self) -> Path:
        return ROOT / "runs" / "speaker_fix" / f"{self.vid}.json"

    @property
    def sarvam_raw(self) -> Path:
        return self.src / f"{self.vid}_sarvam_raw.json"

    @property
    def clips(self) -> Path:
        return self.out / f"{self.vid}.json"

    @property
    def review(self) -> Path:
        return ROOT / "runs" / "previews" / self.out.name / "review.html"


@dataclass
class Ctx:
    p: Paths
    url: str
    dry_run: bool
    limit_inr: float | None
    skip_asr_fix: bool
    ledger0: float
    speaker_fix: bool = False           # opt-in step 5b; select then uses SPEAKERS_CONFIG
    sarvam: bool = False                # opt-in step 2b (Sarvam diarization)
    credits_used: float = 0.0
    inr_projected: float = 0.0          # dry run: rupees the paid steps would bill
    credits_projected: float = 0.0
    ran: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class GuardRefused(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------------- steps
class Step:
    name = ""
    paid = False          # Gemini rupees (guarded by the ledger)

    def outputs(self, p: Paths) -> list[Path]:
        raise NotImplementedError

    def done(self, p: Paths) -> bool:
        return all(x.exists() for x in self.outputs(p))

    def missing(self, p: Paths) -> list[Path]:
        return [x for x in self.outputs(p) if not x.exists()]

    def project(self, ctx: Ctx) -> tuple[float | None, str]:
        """(rupees this step would bill or None when free, note). Must not call a provider."""
        return None, "free"

    def run(self, ctx: Ctx) -> None:
        raise NotImplementedError


class Download(Step):
    name = "download"

    def outputs(self, p):
        return [p.mp4, p.wav, p.meta]

    def project(self, ctx):
        return None, "free (yt-dlp + ffmpeg)"

    def run(self, ctx):
        p = ctx.p
        if not (p.mp4.exists() and p.wav.exists()):
            run_cmd([PY, "download_youtube.py", ctx.url, "scribe_dataset"])
        if not p.meta.exists():
            write_youtube_meta(ctx.url, p.meta)


class Transcribe(Step):
    name = "transcribe"

    def outputs(self, p):
        return [p.transcript]

    def project(self, ctx):
        if not ctx.p.wav.exists():
            return None, "ElevenLabs credits: unknown until the audio is downloaded (~66.7 credits/min)"
        mins = media_duration_s(ctx.p.wav) / 60.0
        ctx.credits_projected = mins * CREDITS_PER_MIN
        return None, f"ElevenLabs: {mins:.1f} min -> ~{ctx.credits_projected:,.0f} credits (no rupees)"

    def run(self, ctx):
        mins = media_duration_s(ctx.p.wav) / 60.0
        print(f"    credit estimate: {mins:.1f} min x {CREDITS_PER_MIN} = ~{mins * CREDITS_PER_MIN:,.0f} credits")
        run_cmd([PY, "transcribe_scribe.py", rel(ctx.p.wav), rel(ctx.p.transcript)])
        ctx.credits_used += mins * CREDITS_PER_MIN


class Sarvam(Step):
    """Opt-in (--sarvam): Sarvam diarization (scribe_dataset/<id>/<id>_sarvam_raw.json) for the speaker layer.
    Rupees: minutes x Rs 0.59, guarded here and in the script; the script records the job in the project ledger."""
    name = "sarvam"
    paid = True

    def outputs(self, p):
        return [p.sarvam_raw]

    def project(self, ctx):
        if not ctx.p.wav.exists():
            return None, "Sarvam: unknown until the audio is downloaded (Rs 0.59/min)"
        mins = media_duration_s(ctx.p.wav) / 60.0
        inr = round(mins * SARVAM_INR_PER_MIN, 2)
        return inr, f"Sarvam: {mins:.1f} min x Rs {SARVAM_INR_PER_MIN} = Rs {inr:.2f} (recorded in the ledger)"

    def run(self, ctx):
        args = [PY, "tools/transcribe_sarvam.py", ctx.p.vid, "--force"]
        if ctx.limit_inr is not None:
            args += ["--max-ledger-inr", f"{ctx.limit_inr:g}"]
        run_cmd(args)


class Shots(Step):
    name = "shots"

    def outputs(self, p):
        return [p.shots]

    def project(self, ctx):
        return None, "free (ffmpeg scene detection, ~1 min per 30 min of video)"

    def run(self, ctx):
        p = ctx.p
        cuts = scene_cuts(p.mp4, p.scene_log)
        dur = round(media_duration_s(p.mp4), 3)
        p.shots.parent.mkdir(parents=True, exist_ok=True)
        p.shots.write_text(json.dumps(shots_doc(p.vid, cuts, dur)), encoding="utf-8")
        print(f"    {len(cuts)} cuts over {dur / 60:.1f} min -> {rel(p.shots)}")


class Segments(Step):
    name = "segments"

    def outputs(self, p):
        return [p.segments]

    def run(self, ctx):
        run_cmd([PY, "-m", "shotvi.segment", rel(ctx.p.transcript), "--out", rel(ctx.p.segments),
                 "--intro", "rules"])


class AsrFix(Step):
    name = "asr_fix"
    paid = True

    def outputs(self, p):
        return [p.corrections]

    def project(self, ctx):
        if not ctx.p.transcript.exists():
            return ASR_FIX_INR_FALLBACK, f"~₹{ASR_FIX_INR_FALLBACK:.0f} (rough; asr_fix projects itself once the transcript exists)"
        out = run_cmd([PY, "-m", "shotvi.segment.asr_fix", ctx.p.vid, "--dry-run"], capture=True)
        inr = parse_inr(out, r"projected total: ₹\s*([0-9.]+)")
        if inr is None:
            raise RuntimeError("could not read asr_fix's projection from its --dry-run output")
        return inr, f"Gemini, projected ₹{inr:.2f} (asr_fix --dry-run)"

    def run(self, ctx):
        args = [PY, "-m", "shotvi.segment.asr_fix", ctx.p.vid]
        if ctx.limit_inr is not None:
            args += ["--max-ledger-inr", f"{ctx.limit_inr:g}"]
        run_cmd(args)


class SpeakerFix(Step):
    """Opt-in (--speaker-fix): LLM speaker names per line (runs/speaker_fix/<id>.json, never edits the transcript)."""
    name = "speaker_fix"
    paid = True

    def outputs(self, p):
        return [p.speakers]

    def project(self, ctx):
        if not ctx.p.transcript.exists():
            return SPEAKER_FIX_INR_FALLBACK, (f"~₹{SPEAKER_FIX_INR_FALLBACK:.0f} (rough; speaker_fix projects itself "
                                              f"once the transcript exists)")
        out = run_cmd([PY, "-m", "shotvi.segment.speaker_fix", ctx.p.vid, "--dry-run"], capture=True)
        m = re.search(r"projected total ₹\s*([0-9.]+) \(worst case ₹\s*([0-9.]+)\)", out)
        if not m:
            raise RuntimeError("could not read speaker_fix's projection from its --dry-run output")
        expected, worst = float(m.group(1)), float(m.group(2))
        # the guard checks the worst case (every chunk at its whole output budget), like select's guard projection
        return worst, (f"Gemini, projected ₹{expected:.2f} expected (conservative: the measured runs billed ~0.4x "
                       f"of it), ₹{worst:.2f} worst case (speaker_fix --dry-run)")

    def run(self, ctx):
        args = [PY, "-m", "shotvi.segment.speaker_fix", ctx.p.vid]
        if ctx.limit_inr is not None:
            args += ["--max-ledger-inr", f"{ctx.limit_inr:g}"]
        run_cmd(args)


class Select(Step):
    name = "select"
    paid = True

    def outputs(self, p):
        return [p.clips]

    @staticmethod
    def config_args(ctx) -> list[str]:
        return ["--config", SPEAKERS_CONFIG] if ctx.speaker_fix else []

    def project(self, ctx):
        p = ctx.p
        if not (p.transcript.exists() and p.shots.exists()):
            return SELECT_INR_FALLBACK, (f"~₹{SELECT_INR_FALLBACK:.0f} (rough; the pipeline projects itself once the "
                                         f"transcript and shots exist)")
        out = run_cmd([PY, "-m", "shotvi.select.webseries", p.vid, "--out", rel(p.out), *self.config_args(ctx),
                       "--dry-run"], capture=True)
        guard = parse_inr(out, r"guard projection ₹\s*([0-9.]+)")
        expected = parse_inr(out, r"DRY RUN total: ₹\s*([0-9.]+)")
        if guard is None or expected is None:
            raise RuntimeError("could not read the clip pipeline's projection from its --dry-run output")
        return guard, f"Gemini, projected ₹{expected:.2f} expected, ₹{guard:.2f} worst case (webseries --dry-run)"

    def run(self, ctx):
        p = ctx.p
        args = [PY, "-m", "shotvi.select.webseries", p.vid, "--out", rel(p.out), *self.config_args(ctx), "--no-retry"]
        if ctx.limit_inr is not None:
            args += ["--max-ledger-inr", f"{ctx.limit_inr:g}"]
        run_cmd(args)


class Preview(Step):
    name = "preview"

    def outputs(self, p):
        return [p.review]

    def done(self, p):
        # the page is rebuilt when the clip document is newer than it (a re-run of the selection)
        return p.review.exists() and (not p.clips.exists() or p.review.stat().st_mtime >= p.clips.stat().st_mtime)

    def project(self, ctx):
        return None, "free (ffmpeg jump-cuts of the top clips)"

    def run(self, ctx):
        run_cmd([PY, "tools/preview_clips.py", rel(ctx.p.out), "--top", "50"])


STEPS: list[Step] = [Download(), Transcribe(), Shots(), Segments(), AsrFix(), Select(), Preview()]


def steps_for(speaker_fix: bool, sarvam: bool = False) -> list[Step]:
    """The default steps; --speaker-fix inserts the speaker_fix step between asr_fix and select; --sarvam inserts
    the Sarvam diarization step right after transcribe."""
    steps = list(STEPS)
    if speaker_fix:
        k = next(i for i, s in enumerate(steps) if s.name == "asr_fix") + 1
        steps = steps[:k] + [SpeakerFix()] + steps[k:]
    if sarvam:
        k = next(i for i, s in enumerate(steps) if s.name == "transcribe") + 1
        steps = steps[:k] + [Sarvam()] + steps[k:]
    return steps


# ----------------------------------------------------------------------------------------------- driver
def run_step(i: int, step: Step, ctx: Ctx, n_steps: int = len(STEPS)) -> None:
    tag = f"[{i}/{n_steps}] {step.name:10}"
    if step.name == "asr_fix" and ctx.skip_asr_fix and not step.done(ctx.p):
        print(f"{tag} skipped (--skip-asr-fix)   cost: ₹0")
        ctx.skipped.append(step.name)
        return
    if step.done(ctx.p):
        print(f"{tag} done already ({rel(step.outputs(ctx.p)[0])})   cost: ₹0")
        ctx.skipped.append(step.name)
        return
    inr, note = step.project(ctx)
    if step.paid:
        led = ledger_inr()
        fits = ctx.limit_inr is None or led + (inr or 0.0) <= ctx.limit_inr
        verdict = ("" if ctx.limit_inr is None else
                   f"; ledger ₹{led:.2f} + ₹{inr or 0:.2f} {'<=' if fits else '>'} limit ₹{ctx.limit_inr:.2f}")
        if ctx.dry_run:
            ctx.inr_projected += inr or 0.0
            print(f"{tag} would run   {note}{verdict}{'' if fits else '  -> the guard would REFUSE it'}")
            return
        if not fits:
            raise GuardRefused(f"{step.name}: ledger ₹{led:.2f} + projected ₹{inr:.2f} > limit ₹{ctx.limit_inr:.2f} "
                               f"(raise --max-ledger-inr or SHOTVI_SPEND_CAP_INR after the founder approves)")
        print(f"{tag} running   {note}{verdict}")
    else:
        if ctx.dry_run:
            print(f"{tag} would run   {note}   missing: {', '.join(rel(x) for x in step.missing(ctx.p))}")
            return
        print(f"{tag} running   {note}")
    t0, led0 = time.perf_counter(), (ledger_inr() if step.paid else 0.0)
    step.run(ctx)
    cost = f"₹{ledger_inr() - led0:.2f}" if step.paid else ("credits ~%.0f" % ctx.credits_used
                                                             if step.name == "transcribe" else "₹0")
    print(f"{tag} done in {time.perf_counter() - t0:.0f}s   cost: {cost}")
    ctx.ran.append(step.name)


def summary(ctx: Ctx) -> dict:
    p = ctx.p
    meta = json.loads(p.meta.read_text(encoding="utf-8")) if p.meta.exists() else {}
    dur = meta.get("duration")
    if not dur and p.shots.exists():
        dur = json.loads(p.shots.read_text(encoding="utf-8")).get("duration")
    tiers: dict[str, int] = {}
    verdict, n_rej = None, None
    if p.clips.exists():
        doc = json.loads(p.clips.read_text(encoding="utf-8"))
        for c in doc.get("proposals", []):
            tiers[c.get("tier") or "untiered"] = tiers.get(c.get("tier") or "untiered", 0) + 1
        n_rej = len(doc.get("rejected") or [])
        verdict = doc.get("episode_verdict")
    led1 = ledger_inr()
    s = {"video_id": p.vid, "title": meta.get("title") or "(no youtube_meta.json)", "duration_s": dur,
         "credits_used": round(ctx.credits_used), "inr_spent": round(led1 - ctx.ledger0, 2),
         "ledger_inr": round(led1, 2), "clips_per_tier": tiers, "rejected": n_rej, "episode_verdict": verdict,
         "review_page": rel(p.review) if p.review.exists() else None, "ran": ctx.ran, "skipped": ctx.skipped}
    print("\n== summary")
    print(f"  episode:   {s['title']}  [{p.vid}]")
    print(f"  length:    {dur / 60:.1f} min" if dur else "  length:    unknown")
    if ctx.dry_run:
        print(f"  dry run:   paid steps would bill ~₹{ctx.inr_projected:.2f}"
              + (f" and ~{ctx.credits_projected:,.0f} ElevenLabs credits" if ctx.credits_projected else "")
              + f"; ledger ₹{led1:.2f}" + (f" of limit ₹{ctx.limit_inr:.2f}" if ctx.limit_inr is not None else ""))
    print(f"  credits:   ~{s['credits_used']:,} ElevenLabs credits used this run")
    print(f"  rupees:    ₹{s['inr_spent']:.2f} this run (ledger ₹{ctx.ledger0:.2f} -> ₹{led1:.2f})")
    if tiers:
        print("  clips:     " + ", ".join(f"{k}: {v}" for k, v in sorted(tiers.items()))
              + (f"; rejected: {n_rej}" if n_rej is not None else ""))
    elif p.clips.exists():
        print("  clips:     none proposed")
    else:
        print("  clips:     (no clip document yet)")
    if verdict:
        print(f"  verdict:   {str(verdict.get('verdict', '')).upper()} "
              f"({verdict.get('strong_clips')} strong candidates, "
              f"{verdict.get('worth_a_look')} other candidates"
              + (f", {verdict['unchecked']} unchecked" if verdict.get("unchecked") else "")
              + f"){': ' + verdict['note'] if verdict.get('note') else ''}")
    print(f"  review:    {s['review_page'] or '(not built)'}")
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m shotvi.run_episode",
                                 description="New web-series episode: download, transcribe, shots, segments, "
                                             "ASR fix, clip selection, review page.")
    ap.add_argument("video", help="YouTube URL or 11-character video id")
    ap.add_argument("--out", default=None, help="clip run directory (default runs/ep_<id>)")
    ap.add_argument("--max-ledger-inr", type=float, default=None,
                    help="refuse a paid Gemini step when ledger + projection would exceed this "
                         "(default: SHOTVI_SPEND_CAP_INR from .env)")
    ap.add_argument("--skip-asr-fix", action="store_true", help="skip the Gemini ASR correction layer")
    ap.add_argument("--speaker-fix", action="store_true",
                    help="opt-in: run the LLM speaker-name layer (shotvi.segment.speaker_fix) after asr_fix and "
                         f"select with {SPEAKERS_CONFIG} (speaker names in the prompts)")
    ap.add_argument("--sarvam", action="store_true",
                    help="opt-in: Sarvam diarization after transcribe (tools/transcribe_sarvam.py, Rs 0.59/min, "
                         "recorded in the project ledger; skipped when the raw file exists)")
    ap.add_argument("--dry-run", action="store_true",
                    help="check the free steps and print the paid steps' projections; no provider call")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    vid = video_id_of(a.video)
    url = a.video if a.video.startswith(("http://", "https://")) else f"https://www.youtube.com/watch?v={vid}"
    out = Path(a.out) if a.out else Path("runs") / f"ep_{vid}"
    if not out.is_absolute():
        out = ROOT / out
    limit = a.max_ledger_inr if a.max_ledger_inr is not None else spend_cap_inr()
    ctx = Ctx(Paths(vid, out), url, a.dry_run, limit, a.skip_asr_fix, ledger_inr(), speaker_fix=a.speaker_fix,
              sarvam=a.sarvam)
    steps = steps_for(a.speaker_fix, a.sarvam)
    print(f"{'DRY RUN: ' if a.dry_run else ''}{vid} -> {rel(out)}   ledger ₹{ctx.ledger0:.2f}, limit ₹{limit:.2f}")
    code = 0
    try:
        for i, step in enumerate(steps, 1):
            run_step(i, step, ctx, len(steps))
    except GuardRefused as e:
        print(f"REFUSED: {e}")
        code = EXIT_GUARD_REFUSED
    except subprocess.CalledProcessError as e:
        name = next((s.name for s in steps if s.name not in ctx.ran and s.name not in ctx.skipped), "?")
        if e.returncode == 2 and name in ("asr_fix", "speaker_fix", "sarvam", "select"):
            print(f"STOP: {name} stopped on its ledger guard (exit 2); nothing after it ran")
            code = EXIT_BUDGET_STOP
        else:
            print(f"FAILED: {name} exited {e.returncode} ({' '.join(map(str, e.cmd))[:200]}); re-run to resume")
            code = 1
    except (RuntimeError, OSError) as e:
        print(f"FAILED: {e}")
        code = 1
    summary(ctx)
    return code


if __name__ == "__main__":
    sys.exit(main())
