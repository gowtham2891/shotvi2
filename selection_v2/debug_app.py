"""Streamlit debug app for selection_v2.

    venv/bin/streamlit run selection_v2/debug_app.py

Sidebar: pick an episode on disk, see which inputs / stage outputs exist, run the speaker fix when missing.
One tab per stage (Run button, elapsed time, full traceback on failure, tables; raw JSON behind an expander),
plus Tags (every Scribe tag in the dataset with its class), Eval (golden set + recall) and Runs log (runs.csv).
Nothing here swallows an exception: every failure shows its traceback in the tab.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from selection_v2 import present, runner  # noqa: E402
from selection_v2.config import inr_per_usd, load_cast_lists, load_config  # noqa: E402
from selection_v2.eval import golden_set, recall  # noqa: E402
from selection_v2.paths import EpisodePaths, episodes_on_disk, load_json  # noqa: E402
from selection_v2.reference import compare as ref_compare  # noqa: E402
from selection_v2.stage1_facts import fmt_ts  # noqa: E402
from selection_v2.tags_review import tag_tally  # noqa: E402

st.set_page_config(page_title="selection_v2 debug", layout="wide")
CFG = load_config()
RATE = inr_per_usd(CFG)


# ----------------------------------------------------------------------------------------------- helpers
def show_exception(e: BaseException, tb: str | None = None):
    st.error(f"{type(e).__name__}: {e}")
    st.code(tb or traceback.format_exc(), language="text")


def facts_of(vid: str) -> dict | None:
    p = EpisodePaths(vid, CFG).facts
    try:
        return load_json(p) if p.exists() else None
    except Exception as e:  # a corrupt file must be visible, not hidden
        show_exception(e)
        return None


def line_fmt(r: dict) -> str:
    who = "(sound)" if r["kind"] == "event" else f"[{r.get('speaker_key') or r.get('speaker')} {r.get('speaker_name') or ''}]".replace(" ]", "]")
    conf = "" if r["kind"] == "event" or r.get("speaker_confidence") in (None, 0.9) else f" c={r.get('speaker_confidence')}"
    z = " (zero-len)" if r.get("zero_len") else ""
    return f"{r['id']} [{fmt_ts(r['start'])}-{fmt_ts(r['end'])}] {who}{conf}: {r['text']}{z}"


def context_block(facts: dict, line_ids: list[str], n: int = 6):
    """Transcript lines around the given ids (union of windows), with the ids marked."""
    lines = facts["lines"]
    idx = {r["id"]: i for i, r in enumerate(lines)}
    want = set()
    for lid in line_ids:
        if lid in idx:
            i = idx[lid]
            want.update(range(max(0, i - n), min(len(lines), i + n + 1)))
    if not want:
        st.info(f"no lines found for {line_ids}")
        return
    out = []
    for i in sorted(want):
        r = lines[i]
        out.append(("> " if r["id"] in line_ids else "  ") + line_fmt(r))
    st.code("\n".join(out), language="text")


def video_at(t: float, lead_s: float = 4.0):
    """Play the episode's mp4 starting a few seconds before t (the file is on disk under scribe_dataset/)."""
    if not P.mp4.exists():
        st.warning(f"no video file at {P.mp4}; cannot play")
        return
    start = max(0.0, float(t) - lead_s)
    st.markdown(f"**▶ video from {fmt_ts(start)}** (the marked moment is at {fmt_ts(t)}, {lead_s:g} s in)")
    st.video(str(P.mp4), start_time=int(start))


def selected_rows(df: pd.DataFrame, key: str, height: int | None = None) -> list[int]:
    """Render a selectable dataframe; return the selected row positions."""
    ev = st.dataframe(df, key=key, on_select="rerun", selection_mode="single-row", height=height, width="stretch")
    try:
        return list(ev.selection.rows)
    except Exception:
        return []


def run_button(vid: str, key: str, label: str):
    if st.button(f"Run {label}", key=f"run_{key}_{vid}", type="primary"):
        t0 = time.perf_counter()
        with st.spinner(f"Running stage {key} on {vid}. " + (
                "For Stage 1B: cutting the episode to 480p (about a minute), uploading it, then one Gemini call. Expect 3 to 6 minutes; keep this tab open."
                if key == "1b" else "Stage 1A takes under a second." if key == "1a" else "This can take a few minutes; keep this tab open.")):
            try:
                res = runner.run_stage(vid, key, CFG)
                st.session_state[f"last_{key}_{vid}"] = {"ok": True, "s": res.duration_s, "warn": res.warnings}
            except BaseException as e:  # noqa: BLE001 - shown in full
                st.session_state[f"last_{key}_{vid}"] = {"ok": False, "s": time.perf_counter() - t0,
                                                         "err": e, "tb": traceback.format_exc()}
    last = st.session_state.get(f"last_{key}_{vid}")
    if last:
        if last["ok"]:
            st.success(f"stage {key} finished in {last['s']:.2f} s")
            for w in last.get("warn") or []:
                st.warning(w)
        else:
            st.error(f"stage {key} FAILED after {last['s']:.2f} s")
            show_exception(last["err"], last["tb"])


# ----------------------------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("selection_v2")
    st.caption(f"config {Path(CFG['_path']).name} sha {CFG['_sha8']} · ₹{RATE:g} per USD "
               f"({CFG['rates']['inr_per_usd_env']}) · model {CFG['models']['default']['model']}")
    vids = episodes_on_disk()
    if not vids:
        st.error("no episodes under scribe_dataset/")
        st.stop()
    labels = {}
    for v in vids:
        t = EpisodePaths(v, CFG).title()
        labels[v] = f"{t[:48]} [{v}]" if t else v
    vid = st.selectbox("episode", vids, format_func=lambda v: labels[v])
    P = EpisodePaths(vid, CFG)
    stt = P.status()
    st.markdown("**inputs on disk**")
    for k in ("video", "audio", "scribe", "youtube_meta", "asr_fix", "speaker_fix"):
        st.write(("✅ " if stt[k] else "❌ ") + k)
    st.markdown("**stage outputs**")
    st.write(("✅ " if stt["facts"] else "❌ ") + "episode_facts.json" + (f" (stages {', '.join(stt['stages_done'])})" if stt["stages_done"] else ""))
    if not stt["speaker_fix"]:
        st.divider()
        st.markdown("**Speaker fix missing** (Stage 1A refuses to run without it)")
        if st.button("Estimate speaker-fix cost", key=f"sf_est_{vid}"):
            try:
                st.session_state[f"sf_proj_{vid}"] = runner.project_speaker_fix(vid)
            except Exception as e:
                show_exception(e)
        proj = st.session_state.get(f"sf_proj_{vid}")
        if proj:
            st.info(f"projected ₹{proj['expected_inr']:.2f} expected, ₹{proj['worst_case_inr']:.2f} worst case "
                    f"(Gemini, project ledger cap applies)")
            with st.expander("dry-run output"):
                st.code(proj["output"], language="text")
            if st.button(f"Run speaker fix now (≈₹{proj['expected_inr']:.2f})", key=f"sf_run_{vid}", type="primary"):
                with st.spinner("running shotvi.segment.speaker_fix (minutes)"):
                    try:
                        r = runner.run_speaker_fix(vid, CFG)
                        st.session_state[f"sf_done_{vid}"] = r
                        st.rerun()
                    except Exception as e:
                        show_exception(e)
    done = st.session_state.get(f"sf_done_{vid}")
    if done:
        st.success(f"speaker fix done: actual ledger delta ₹{done['ledger_delta_inr']:.2f} → {done['path']}")
    st.divider()
    st.caption(f"runs log: {P.runs_log}")

# ----------------------------------------------------------------------------------------------- tabs
tab_names = ["Review", "New flow (web series)"] + [label.split(":")[0] for _, label, _ in runner.STAGES] + ["Tags", "Golden set", "Runs log"]
all_tabs = st.tabs(tab_names)
review_tab, flow_tab, tabs = all_tabs[0], all_tabs[1], all_tabs[2:]

# ----------------------------------------------------------------------------------------------- New flow (2026-11-05)
from selection_v2 import flow_v2 as FV  # noqa: E402


def flow_step_view(F: dict, step: str):
    """What a finished step produced, as tables."""
    if step == "text":
        sp = F["episode_pass"]["speakers"]
        st.dataframe(pd.DataFrame([{"key": x["key"], "name": x.get("name"), "role": x.get("role"), "status": x.get("status"),
                                    "merged into": x.get("merged_into") or ""} for x in sp]), hide_index=True, width="stretch")
    elif step == "video":
        idp = F["identity_pass"]
        st.dataframe(pd.DataFrame([{"scene": s["id"], "start": fmt_ts(s["start"]), "end": fmt_ts(s["end"]), "lines": f"{s['first_line']}-{s['last_line']}",
                                    "where": s.get("where"), "why a scene": s.get("why"), "why it ends": s.get("end_why")} for s in idp["scenes"]]),
                     hide_index=True, width="stretch")
        st.dataframe(pd.DataFrame([{"key": x.get("key"), "name": x.get("name"), "role": x.get("role"), "look": x.get("look")}
                                   for x in idp.get("people") or []]), hide_index=True, width="stretch")
    elif step == "notes":
        sn = F["scene_notes"]
        st.markdown(f"**The episode's story.** {sn['episode']}")
        st.dataframe(pd.DataFrame([{"scene": n["scene_id"], "time": f"{fmt_ts(n['start'])}-{fmt_ts(n['end'])}", "story": n["story"],
                                    "mood": n.get("mood"), "reactions": len(n.get("reactions") or []), "seen without words": len(n.get("visual_moments") or []),
                                    "silences": len(n.get("silences") or [])} for n in sn["scenes"]]), hide_index=True, width="stretch")
    elif step in ("find", "edit"):
        cl = (F.get("clips_found") if step == "find" and F.get("clips_found") else F["clips"])["clips"]
        st.dataframe(pd.DataFrame([{"clip": c["id"], "scene": c.get("scene"), "starts": fmt_ts(c["start_time"]), "plays (s)": round(c["duration"]),
                                    **({"found (s)": round((c.get("found") or {}).get("duration") or c["duration"])} if step == "edit" else {}),
                                    "cuts": len(c.get("drops") or []), "cold open": "yes" if c.get("cold_open") else "",
                                    "hold (s)": round(c.get("hold_s") or 0, 1), "title": c.get("title"), "why": c.get("why"), "feels": c.get("emotion")}
                                   for c in cl]), hide_index=True, width="stretch")


with flow_tab:
    st.markdown(f"## New flow: {P.title() or vid}")
    st.caption("The web-series flow agreed on 3-5 Nov: text step, video step (who talks, who is on screen, scenes), scene notes, "
               "find clips, edit clips. Everything for this episode is saved in one file, separate from the old stages: "
               f"`{FV.path(vid, CFG)}`. The full explanation is in `review/flow.html`.")
    FF = None
    try:
        FF = FV.load(vid, FV.cfg_for(vid, CFG))
    except Exception as e:
        show_exception(e)
    have = FV.done(FF)
    tests = FV.test_results(vid)
    if tests["facts"]:
        with st.container(border=True):
            st.markdown("**Test results exist for this episode** (from the runs we did together). Loading them is free and "
                        "replaces this episode's flow file." + (" There is already a flow file; it will be overwritten." if FF else ""))
            st.caption("Found: " + ", ".join(k for k, v in tests.items() if v))
            if st.button("Load the test results (free)", key=f"flow_import_{vid}"):
                try:
                    FV.import_test(vid, CFG)
                    st.rerun()
                except Exception as e:
                    show_exception(e)
    order = [k for k, _ in FV.STEPS]
    for i, (step, label) in enumerate(FV.STEPS):
        with st.container(border=True):
            ready = all(have[k] for k in order[:i])
            st.markdown(f"**{i + 1}. {label}** " + (":green[done]" if have[step] else ":gray[not run]" if ready else ":gray[waiting for the step before]"))
            if ready:
                est_key = f"flow_est_{step}_{vid}"
                row = st.container(horizontal=True, vertical_alignment="center")
                if row.button("Estimate cost (free)", key=f"flow_estb_{step}_{vid}"):
                    try:
                        st.session_state[est_key] = FV.estimate(vid, step, CFG)
                    except Exception as e:
                        st.session_state[est_key] = None
                        show_exception(e)
                est = st.session_state.get(est_key)
                if est is not None:
                    if row.button(f"Run ({'re-run, ' if have[step] else ''}≈ ₹{est:.0f}; thinking can add 40%)", key=f"flow_run_{step}_{vid}", type="primary"):
                        t0 = time.perf_counter()
                        with st.spinner(f"Running {label}. This can take a few minutes; keep this tab open."):
                            try:
                                res = FV.run(vid, step, CFG)
                                st.session_state[f"flow_last_{step}_{vid}"] = {"ok": True, **res}
                            except BaseException as e:  # noqa: BLE001 - shown in full
                                st.session_state[f"flow_last_{step}_{vid}"] = {"ok": False, "s": time.perf_counter() - t0, "err": e, "tb": traceback.format_exc()}
                        st.session_state.pop(est_key, None)
                        st.rerun()
            last = st.session_state.get(f"flow_last_{step}_{vid}")
            if last:
                if last["ok"]:
                    st.success(f"finished in {last['seconds']:.0f} s, cost ≈ ₹{(last.get('cost_inr') or 0):.2f}")
                    for w in last.get("warnings") or []:
                        st.warning(w)
                else:
                    st.error(f"FAILED after {last['s']:.0f} s")
                    show_exception(last["err"], last["tb"])
            if have[step] and FF:
                with st.expander("What it produced"):
                    try:
                        flow_step_view(FF, step)
                    except Exception as e:
                        show_exception(e)
    with st.container(border=True):
        st.markdown("**6. Cut the clips and build the rating page** (free)")
        if have["find"]:
            if st.button("Cut every clip into its own file and build the page", key=f"flow_cut_{vid}"):
                with st.spinner("Cutting clips with ffmpeg"):
                    try:
                        st.session_state[f"flow_cut_res_{vid}"] = FV.cut(vid, CFG)
                    except Exception as e:
                        show_exception(e)
            res = st.session_state.get(f"flow_cut_res_{vid}")
            if res:
                st.success(f"{len(res['files'])} clip files; rating page: `{res['page']}`")
                if st.button("Open the rating page", key=f"flow_open_{vid}"):
                    import subprocess
                    subprocess.run(["open", res["page"]], check=False)
                with st.expander("Watch the clips here"):
                    for fpath in res["files"]:
                        st.caption(Path(fpath).name)
                        st.video(fpath)
        else:
            st.caption("waiting for Stage 3 find")

# ----------------------------------------------------------------------------------------------- Review (guided)

with review_tab:
    st.markdown(f"## {P.title() or vid}")
    st.markdown(
        "**What this is.** The computer watched this episode with a stopwatch and wrote down facts: where each line is "
        "said, where the pauses are and where there is a laugh. It decides nothing: the scenes and the speakers come from "
        "the model in the later steps.")
    F = facts_of(vid)
    if F is None or "1a" not in F.get("stages", {}):
        st.info("This episode has not been measured yet.")
        run_button(vid, "1a", "the measurement (Stage 1A, a few seconds, free)")
        F = facts_of(vid)
    if F is not None and "1a" in F.get("stages", {}):
        st.success(present.plain_summary(F, CFG))
        try:
            checks = present.sanity_checks(F, CFG, load_cast_lists())
            red = [c for c in checks if c["ok"] is False]
            if red:
                st.error("Automatic checks found a problem; copy this line and send it: " + " | ".join(f"{c['name']}: {c['detail']}" for c in red))
            else:
                st.caption("Automatic checks: all passed.")
        except Exception as e:
            show_exception(e)
        st.caption("Scene changes are decided by the model in Stage 1B (founder, 2026-11-03: the program no longer guesses "
                   "them), so there is nothing to review here; see the Scenes table in the Stage 1B tab.")


# ---- Stage 1A
EDGE, LAUGH, HOLD, GOLD, SILVER, SCENE = "#3987e5", "#d95926", "#199e70", "#c98500", "#9085e9", "#d55181"   # dark-surface slots


def timeline(F: dict, key: str):
    """Whole-episode timeline: the 1B scenes as vertical lines, laughs as dots, holds as bigger dots, golden-set
    moments as bars. Hover for the time and evidence; click a mark to see the transcript around it."""
    import altair as alt
    gold = golden_set.moments_of(golden_set.load(), F["episode"]["video_id"]) if golden_set.DEFAULT_PATH.exists() else []
    marks, spans = present.timeline_rows(F, CFG, gold)
    if not marks:
        st.info("nothing to draw: no scenes, laughs or holds")
        return
    dur = F["episode"]["duration"]
    mdf = pd.DataFrame(marks)
    mdf["minute"] = mdf["t"] / 60.0
    mdf["y"] = mdf["kind"].map({"scene edge": 1.0, "scene (1B)": 1.0, "laugh": 0.72, "hold": 0.45})
    pick = alt.selection_point(name="pick", fields=["t"], on="click", nearest=True, empty=False)
    x = alt.X("minute:Q", title="minutes", scale=alt.Scale(domain=[0, dur / 60.0], nice=False))
    tip = [alt.Tooltip("label:N", title="time"), alt.Tooltip("kind:N"), alt.Tooltip("line:N", title="line"),
           alt.Tooltip("detail:N", title="why")]
    color = alt.Color("kind:N", title=None, scale=alt.Scale(domain=["scene edge", "scene (1B)", "laugh", "hold"],
                                                            range=[EDGE, SCENE, LAUGH, HOLD]), legend=alt.Legend(orient="top"))
    edges = alt.Chart(mdf[mdf["kind"] == "scene edge"]).mark_rule(strokeWidth=2).encode(x=x, color=color, tooltip=tip)
    final = alt.Chart(mdf[mdf["kind"] == "scene (1B)"]).mark_rule(strokeWidth=3, strokeDash=[6, 3]).encode(x=x, color=color, tooltip=tip)
    laughs = alt.Chart(mdf[mdf["kind"] == "laugh"]).mark_circle(size=70).encode(x=x, y=alt.Y("y:Q", axis=None, scale=alt.Scale(domain=[0, 1.1])), color=color, tooltip=tip)
    holds = alt.Chart(mdf[mdf["kind"] == "hold"]).mark_circle(size=200).encode(x=x, y=alt.Y("y:Q", axis=None), color=color, tooltip=tip)
    layers = [edges, final, laughs, holds]
    if spans:
        sdf = pd.DataFrame(spans)
        sdf["m0"], sdf["m1"], sdf["y0"], sdf["y1"] = sdf["start"] / 60.0, sdf["end"] / 60.0, 0.05, 0.22
        layers.insert(0, alt.Chart(sdf).mark_rect(opacity=0.55).encode(
            x=alt.X("m0:Q", title="minutes"), x2="m1:Q", y=alt.Y("y0:Q", axis=None), y2="y1:Q",
            color=alt.Color("tier:N", title=None, scale=alt.Scale(domain=["gold", "silver"], range=[GOLD, SILVER]),
                            legend=alt.Legend(orient="top")),
            tooltip=[alt.Tooltip("label:N", title="golden set")]))
    chart = alt.layer(*layers).add_params(pick).properties(height=170, width="container").resolve_scale(color="independent")
    ev = st.altair_chart(chart, on_select="rerun", selection_mode="pick", key=key, width="stretch")
    st.caption("blue lines = Stage 1A proposed scene edges · dashed pink lines = Stage 1B confirmed scene starts · orange dots = laugh "
               "tags · green dots = holds (pauses with no music) · bars at the bottom = golden-set moments. Hover for details, click a mark.")
    try:
        sel = ev.selection.pick if ev is not None else []
    except Exception:
        sel = []
    if sel:
        t = float(sel[0]["t"])
        m = min(marks, key=lambda m: abs(m["t"] - t))
        st.markdown(f"**{m['kind']} at {m['label']}** — {m['detail']}")
        video_at(m["t"])
        context_block(F, [m["line"]] if m["line"] else [], n=5)


with tabs[0]:
    st.subheader(runner.STAGE_BY_KEY["1a"][0])
    run_button(vid, "1a", "this stage")
    F = facts_of(vid)
    if F is None or "1a" not in F.get("stages", {}):
        st.info("no Stage 1A output yet for this episode: press Run this stage")
    else:
        s1 = F["stages"]["1a"]
        # 1. plain-language summary
        st.markdown(f"#### {present.plain_summary(F, CFG)}")
        st.caption(f"{F['episode'].get('title')} · series {F['episode'].get('series') or '(not in cast_lists.yaml)'} · "
                   f"{s1['version']} · config {s1['config_sha8']} · {s1['generated_at']}")
        for w in s1.get("warnings") or []:
            st.warning(w)
        # 2. timeline
        st.markdown("### Timeline")
        try:
            timeline(F, f"tl_{vid}")
        except Exception as e:
            show_exception(e)
        # 5. sanity checks
        st.markdown("### Sanity checks (automatic, no reference needed; red = a code bug)")
        try:
            checks = present.sanity_checks(F, CFG, load_cast_lists())
            for c in checks:
                icon = "🟢" if c["ok"] else ("⚪" if c["ok"] is None else "🔴")
                st.write(f"{icon} {c['name']} — {c['detail']}")
        except Exception as e:
            show_exception(e)
        # 3. check it yourself
        st.markdown("### Check it yourself (no reference needed: the video plays at the clicked moment)")
        st.markdown(
            "1. **Laughs**: click an orange dot; the video starts 8 s earlier. Did a joke just land? Then scrub to a joke "
            "you remember and see whether there is an orange dot near it.")
        # 4. reference (only episodes with a hand-worked reference file)
        ref_path = ref_compare.reference_path(vid)
        if ref_path.exists():
            st.markdown("### Check against hand-worked values")
            st.caption("Hand-worked values exist only for episodes with a file in selection_v2/reference/; other episodes have none.")
            text = st.text_area("reference YAML (editable)", st.session_state.get(f"ref_{vid}", ref_path.read_text(encoding="utf-8")),
                                height=200, key=f"refta_{vid}")
            cA, cB, _ = st.columns([1, 1, 4])
            if cA.button("Evaluate", key=f"ref_eval_{vid}"):
                st.session_state[f"ref_{vid}"] = text
            if cB.button("Save YAML", key=f"ref_save_{vid}"):
                try:
                    ref_compare.parse_reference(text)
                    ref_path.write_text(text, encoding="utf-8")
                    st.success(f"saved {ref_path}")
                except Exception as e:
                    show_exception(e)
            try:
                ref = ref_compare.parse_reference(st.session_state.get(f"ref_{vid}", text))
                rows = ref_compare.evaluate(F, ref)
                st.write(ref_compare.summary(rows))
                rdf = pd.DataFrame(rows)
                colour = {"agree": "color: green", "disagree": "color: red", "missing": "color: orange", "error": "background-color: #fdd"}
                st.dataframe(rdf.style.map(lambda v: colour.get(v, ""), subset=["status"]), width="stretch", hide_index=True, height=420)
            except Exception as e:
                show_exception(e)

        # detailed tables, collapsed
        st.markdown("### Details")
        with st.expander("Speakers: cast list", expanded=False):
            cl = F["cast_list"]
            st.caption(f"cast list: {cl['n_config_names']} names from data/series_cast/cast_lists.yaml ({cl['series_key']}) + "
                       f"{cl['n_speaker_fix_names']} from speaker_fix" + (f"; skipped role words: {cl.get('speaker_fix_names_skipped')}" if cl.get("speaker_fix_names_skipped") else ""))
        with st.expander("Lines the speaker-fix step was unsure about (its own confidence)", expanded=False):
            fdf = pd.DataFrame(F["speaker_flags"])
            if len(fdf):
                sel = selected_rows(fdf, f"flags_{vid}", height=240)
                if sel:
                    context_block(F, [fdf.iloc[sel[0]]["line"]], n=4)
            else:
                st.write("no confidence flags (every line at 0.9)")
        with st.expander("Gap table (every pause between lines)", expanded=False):
            gmin = st.slider("show pauses ≥ (s)", 0.0, 15.0, 1.0, 0.1, key=f"gmin_{vid}")
            gdf = pd.DataFrame([{"prev": g["prev"], "next": g["next"], "t_end": fmt_ts(g["t_prev_end"]),
                                 "t_next": fmt_ts(g["t_next_start"]), "gap_s": g["gap"], "pct": g["gap_pct"],
                                 "filled_by": g["filled_by"], "before": " ".join(g["spk_before"]),
                                 "after": " ".join(g["spk_after"]), "overlap": g["overlap"]}
                                for g in F["gaps"] if g["gap"] >= gmin])
            st.caption(f"{len(gdf)} of {len(F['gaps'])} pauses · percentiles (s): {s1['summary']['gap_percentiles_s']} · click a row for context")
            sel = selected_rows(gdf, f"gaps_{vid}", height=320)
            if sel:
                r = gdf.iloc[sel[0]]
                context_block(F, [r["prev"], r["next"]])
        with st.expander("Tag events (whitelisted sound tags)", expanded=False):
            classes = sorted({e["class"] for e in F["tag_events"]})
            pick_cls = st.multiselect("classes", classes, default=classes, key=f"tagcls_{vid}")
            tdf = pd.DataFrame([{"t": fmt_ts(e["t"]), "dur_s": e["dur"], "class": e["class"], "tag": e["tag"],
                                 "after": e["after"], "after_content": e.get("after_content_line"), "inside": e.get("inside"),
                                 "line": e["line"]} for e in F["tag_events"] if e["class"] in pick_cls])
            sel = selected_rows(tdf, f"tags_{vid}", height=260)
            if sel:
                r = tdf.iloc[sel[0]]
                e = next(e for e in F["tag_events"] if e["line"] == r["line"])
                video_at(e["t"], lead_s=8.0)
                context_block(F, [x for x in (r["after"], r["line"]) if x])
        with st.expander("Normalization fixes and layers", expanded=False):
            st.write({"layers": F["layers"]})
            ndf = pd.DataFrame(F["normalization_fixes"])
            if len(ndf):
                ndf["from"], ndf["to"] = ndf["from"].astype(str), ndf["to"].astype(str)
            st.dataframe(ndf, width="stretch", hide_index=True, height=200)
        with st.expander("Summary numbers (JSON)", expanded=False):
            st.json(s1["summary"])
        with st.expander("raw episode_facts.json", expanded=False):
            st.json(F, expanded=False)
        with st.expander("all lines", expanded=False):
            st.code("\n".join(line_fmt(r) for r in F["lines"]), language="text")

# ---- Stage 1B
from selection_v2 import acceptance, stage1_episode_pass as E1B  # noqa: E402

with tabs[1]:
    st.subheader(runner.STAGE_BY_KEY["1b"][0])
    st.markdown("**What this does.** One Gemini call that **watches the whole episode** (low-resolution video with sound) and reads "
                "the transcript with the Stage 1A measurements. From watching, it says **who each raw speaker label is**, which labels "
                "are the **same person**, which labels **hide two people**, and **where each person is alone on screen** (for the "
                "reference pictures). Then it confirms the **scenes** and flags lines that may not be story dialogue.\n\n"
                "The names from the old text-only speaker fix are shown to it only as low-trust hints.")
    F = facts_of(vid)
    ready = F is not None and "1a" in F.get("stages", {}) and F["stages"]["1a"].get("version") == runner.stage1_facts.VERSION
    if not ready:
        st.warning("Stage 1A output is missing or older than the current Stage 1A; run Stage 1A first (Stage 1B refuses otherwise).")
    cE, cR, _ = st.columns([1, 1, 3])
    if cE.button("Estimate cost", key=f"est_1b_{vid}", disabled=not ready):
        try:
            with st.spinner("counting tokens (Gemini countTokens, free)"):
                st.session_state[f"est_1b_{vid}_res"] = E1B.estimate_cost(F, CFG)
        except Exception as e:
            show_exception(e)
    est = st.session_state.get(f"est_1b_{vid}_res")
    if est:
        st.info(f"{est['model']}: {est['input_tokens']:,} input tokens (text {est.get('text_tokens', est['input_tokens']):,}, "
                f"video {est.get('video_tokens', 0):,}) + ~{est['expected_output_tokens']:,} output → **₹{est['inr_expected']:.2f} expected**, "
                f"₹{est['inr_worst']:.2f} worst case; ₹{est['inr_per_usd']:g}/USD. {est['note']}")
    with cR:
        run_button(vid, "1b", "Stage 1B now (paid)") if ready else st.button("Run Stage 1B now (paid)", disabled=True, key=f"run_1b_dis_{vid}")
    F = facts_of(vid)
    if F and "1b" in F.get("stages", {}):
        s1b, ep = F["stages"]["1b"], F["episode_pass"]
        u = s1b["usage"]
        st.success(E1B.plain_summary_1b(ep))
        st.caption(f"{s1b['model']['model']} ({s1b['model']['params']}) · {s1b['n_attempts']} call(s) · {u['input_tokens']:,} in "
                   f"({u.get('cached_tokens', 0):,} cached) / {u['output_tokens']:,} out (thinking {u.get('thought_tokens', 0):,}) · "
                   f"₹{u['cost_inr']:.2f} at config rates" + (f", ₹{u['cost_inr_billed']:.2f} billed this run" if u.get("cost_inr_billed") is not None else "")
                   + f" · {s1b['duration_s']:.0f} s · {s1b['generated_at']} · glossary → {ep.get('glossary_file')}")
        for w in s1b.get("warnings") or []:
            st.warning(w)
        try:
            from selection_v2 import speaker_accuracy as SA
            if SA.has_truth(vid):
                st.info(SA.one_line(SA.evaluate(F, vid)))
        except Exception as e:
            show_exception(e)
        # the video step (founder, 2026-11-02): per line, who talks and who is on screen; the naming check decides the names
        from selection_v2 import identity_pass as IDP
        st.markdown("### Video step (who talks and who is on screen, per line) and naming check")
        st.caption("The video step watches the episode with the text step's answer and says, for every spoken line, who talks "
                   "(one, several, or cannot tell; a voice change inside the line) and who is on screen. The naming check (one "
                   "text call) then decides the final person per line. Code only checks ids and keys.")
        idp = F.get("identity_pass")
        if idp and idp.get("lines") is not None:
            u2 = idp["usage"]
            nc = F.get("naming_check") or {}
            st.success(f"video step ran {idp['generated_at']}: {len(idp['lines'])} lines answered · {u2['input_tokens']:,} in / "
                       f"{u2['output_tokens']:,} out · ₹{u2['cost_inr']:.2f}"
                       + (f" · naming check: {nc.get('n_lines_moved', 0)} line changes, {nc.get('n_disagreements', 0)} disagreements, ₹{(nc.get('usage') or {}).get('cost_inr', 0):.2f}" if nc else ""))
            for w in idp.get("warnings") or []:
                st.warning(w)
            with st.expander("video step answer: people and every line"):
                st.dataframe(pd.DataFrame(idp.get("people") or []), width="stretch", hide_index=True)
                st.dataframe(pd.DataFrame([{"line": lid, "talk": " ".join(e["talk"]) or "?", "turns": "; ".join(f"{t['at']}: {' '.join(t['talk'])}" for t in e["turns"]),
                                            "on screen": " ".join(e["see"]) or "nobody", "why": e["why"]} for lid, e in idp["lines"].items()]),
                             width="stretch", hide_index=True)
        elif idp:
            st.info("This episode has an older video-step answer (per scene and voice label); rerun the video step for the per-line answer.")
        try:
            est_i = IDP.estimate(F, CFG)
            st.caption(f"video step (whole episode watched at 480p, {est_i['model']}): about {est_i['input_tokens']:,} input tokens "
                       f"({est_i['video_tokens']:,} video) + {est_i['expected_output_tokens']:,} out ≈ ₹{est_i['inr_expected']:.2f}")
        except Exception as e:
            show_exception(e)
        if st.button("Run the identity pass now (paid, video)", key=f"run_idp_{vid}"):
            t0 = time.perf_counter()
            with st.spinner("Cutting the episode to 480p, uploading, one Gemini call; expect 3 to 6 minutes."):
                try:
                    res = runner.run_substage(vid, "identity_pass", IDP.run, CFG)
                    st.session_state[f"last_idp_{vid}"] = {"ok": True, "s": res.duration_s}
                except BaseException as e:  # noqa: BLE001
                    st.session_state[f"last_idp_{vid}"] = {"ok": False, "s": time.perf_counter() - t0, "err": e, "tb": traceback.format_exc()}
            st.rerun()
        last_i = st.session_state.get(f"last_idp_{vid}")
        if last_i:
            if last_i["ok"]:
                st.success(f"identity pass finished in {last_i['s']:.0f} s")
            else:
                st.error(f"identity pass FAILED after {last_i['s']:.0f} s")
                show_exception(last_i["err"], last_i["tb"])
        # acceptance
        ref = acceptance.load_acceptance(vid, "1b")
        if ref:
            st.markdown("### Acceptance tests")
            rows = acceptance.evaluate_1b(F, ref)
            st.write(acceptance.summary(rows))
            for r in rows:
                icon = {"agree": "🟢", "disagree": "🔴", "missing": "⚪", "error": "⚠️"}[r["status"]]
                st.write(f"{icon} **{r['expect']}** — {r['got']}")
        else:
            st.caption(f"no acceptance tests for this episode (selection_v2/reference/acceptance/{vid}.1b.yaml)")
        st.markdown("### Timeline (dashed pink = confirmed scene starts)")
        try:
            timeline(F, f"tl1b_{vid}")
        except Exception as e:
            show_exception(e)
        st.markdown("### Speakers")
        spdf = pd.DataFrame(ep["derived"]["speakers_table"])
        if len(spdf):
            st.dataframe(spdf.style.apply(lambda row: ["background-color: #4a2a1a" if row["changed"] else "" for _ in row], axis=1),
                         width="stretch", hide_index=True, height=min(520, 40 + 36 * len(spdf)))
        if ep.get("prior") == "raw":
            st.markdown("### Tier 0, from watching: who each raw label is, merges, splits, reference moments")
            cA, cB = st.columns(2)
            with cA:
                st.markdown("**cast per raw label**")
                st.dataframe(pd.DataFrame(ep.get("cast") or []), width="stretch", hide_index=True)
                st.markdown("**labels that are the same person**")
                st.dataframe(pd.DataFrame([{**m, "labels": " ".join(m["labels"])} for m in ep["derived"].get("merges") or []]), width="stretch", hide_index=True)
            with cB:
                st.markdown("**labels that hide two people (splits)**")
                st.dataframe(pd.DataFrame(ep["derived"].get("splits") or []), width="stretch", hide_index=True)
                st.markdown("**reference moments per character** (the cast sheet cuts only these)")
                st.dataframe(pd.DataFrame([{"name": r.get("name"), "times": ", ".join(r.get("times") or []), "note": r.get("note")}
                                           for r in ep.get("reference_times") or []]), width="stretch", hide_index=True)
        st.markdown("### Scenes")
        scdf = pd.DataFrame([{"id": s["id"], "start": s["start_str"], "end": s["end_str"], "label": s["label"], "register": s["register"],
                              "speakers": " ".join(s["speakers"]),
                              "first": s["first_line"], "last": s["last_line"]} for s in ep["derived"]["scenes"]])
        sel = selected_rows(scdf, f"scenes1b_{vid}", height=min(520, 40 + 36 * max(1, len(scdf))))
        if sel:
            sc = ep["derived"]["scenes"][sel[0]]
            video_at(sc["start"])
            context_block(F, [sc["first_line"], sc["last_line"]], n=3)
        cA, cB = st.columns(2)
        with cA:
            st.markdown("### Suspect resolutions")
            rdf = pd.DataFrame(ep.get("suspect_resolutions") or [])
            if len(rdf):
                sel = selected_rows(rdf, f"res1b_{vid}", height=300)
                if sel:
                    lid = rdf.iloc[sel[0]]["line"]
                    video_at(next(x["start"] for x in F["lines"] if x["id"] == lid))
                    context_block(F, [lid], n=4)
            else:
                st.write("none")
        with cB:
            st.markdown("### Possibly non-story lines (low stakes; Stage 2 confirms)")
            ndf = pd.DataFrame(ep.get("possible_non_story") or [])
            if len(ndf) and "signals" in ndf:
                ndf["signals"] = ndf["signals"].apply(lambda x: ", ".join(x) if isinstance(x, list) else x)
            if len(ndf):
                sel = selected_rows(ndf, f"ns1b_{vid}", height=300)
                if sel:
                    ids, _ = E1B.expand_lines(ndf.iloc[sel[0]]["lines"], [x["id"] for x in F["lines"]])
                    if ids:
                        video_at(next(x["start"] for x in F["lines"] if x["id"] == ids[0]))
                        context_block(F, ids[:1] + ids[-1:], n=3)
            else:
                st.write("none")
        with st.expander("Glossary returned (and the stored series glossary)"):
            st.json(ep.get("glossary"))
            gp = Path(runner.ROOT) / (ep.get("glossary_file") or "")
            if gp.exists():
                st.json(json.loads(gp.read_text(encoding="utf-8")))
        with st.expander("Exact prompt sent and raw answers"):
            dbg_path = P.run_dir / "stage1b_debug.json"
            if dbg_path.exists():
                dbg = json.loads(dbg_path.read_text(encoding="utf-8"))
                st.markdown("**system**"); st.code(dbg["system"], language="text")
                st.markdown(f"**prefix** ({dbg['prefix_chars']:,} chars, `{P.run_dir / 'prefix.txt'}`)")
                pf = P.run_dir / "prefix.txt"
                if pf.exists():
                    st.code(pf.read_text(encoding="utf-8")[:4000] + "\n... (see the file for the rest)", language="text")
                for att in dbg["attempts"]:
                    st.markdown(f"**call {att['n']}: tier {att['tier']}** · usage {att.get('usage')} · errors shown {att.get('errors_shown')}")
                    st.code(att["instructions"], language="text")
                    st.code(att.get("raw_text") or att.get("error") or "", language="json")
                    if att.get("validation_tier1") or att.get("validation_tier2"):
                        st.write({"validation_tier1": att.get("validation_tier1"), "validation_tier2": att.get("validation_tier2")})
            else:
                st.write("no debug file yet")
        with st.expander("raw episode_pass JSON"):
            st.json(ep, expanded=False)

# ---- Stage 2
from selection_v2 import stage2_beats as S2  # noqa: E402

BEAT_COL = {"entry": "#9085e9", "setup": "#3987e5", "escalation": "#3987e5", "turn": "#c98500", "setup_for_payoff": "#c98500",
            "payoff": "#d95926", "peak": "#d95926", "reaction": "#199e70", "other": "#888888"}
ND_COL = {"reaction_hold": "#199e70", "action_beat": "#3987e5", "transition": "#9085e9", "scene_setting": "#888888"}


def run_stage2(vid: str, scene_ids):
    t0 = time.perf_counter()
    with st.spinner(f"running Stage 2 on scene(s) {scene_ids or 'all remaining'} (cut, upload, call; minutes)"):
        try:
            res = runner.run_stage(vid, "2", CFG, scene_ids=scene_ids)
            st.session_state[f"last_2_{vid}"] = {"ok": True, "s": res.duration_s, "warn": res.warnings}
        except BaseException as e:  # noqa: BLE001
            st.session_state[f"last_2_{vid}"] = {"ok": False, "s": time.perf_counter() - t0, "err": e, "tb": traceback.format_exc()}


def scene_chart(sh: dict, F: dict, key: str):
    import altair as alt
    a, b = sh["start"], sh["end"]
    rows = [{"t": x["t"], "label": f"{x['type']} {x['line']}", "kind": x["type"], "text": x.get("text", "")} for x in sh.get("beats") or []]
    if sh.get("reaction", {}).get("t") is not None:
        rows.append({"t": sh["reaction"]["t"], "label": f"reaction ({sh['reaction'].get('type')})", "kind": "reaction", "text": sh["reaction"].get("note", "")})
    bars = [{"s": x["start"], "e": x["end"], "kind": x["kind"], "rec": x["recommend"] + (f" {x.get('target_s')} s" if x.get("target_s") else ""),
             "reason": x.get("reason", "")} for x in sh.get("non_dialogue") or []]
    x = alt.X("sec:Q", title="seconds into the episode", scale=alt.Scale(domain=[a, b], nice=False))
    layers = []
    if bars:
        bd = pd.DataFrame(bars); bd["sec"], bd["sec2"], bd["y0"], bd["y1"] = bd["s"], bd["e"], 0.0, 0.35
        layers.append(alt.Chart(bd).mark_rect(opacity=0.7).encode(x=x, x2="sec2:Q", y=alt.Y("y0:Q", axis=None, scale=alt.Scale(domain=[0, 1])), y2="y1:Q",
                      color=alt.Color("kind:N", title=None, scale=alt.Scale(domain=list(ND_COL), range=list(ND_COL.values())), legend=alt.Legend(orient="top")),
                      tooltip=["kind:N", "rec:N", "reason:N"]))
    if rows:
        rd = pd.DataFrame(rows); rd["sec"] = rd["t"]; rd["y"] = 0.75
        pick = alt.selection_point(name="pickb", fields=["t"], on="click", empty=False)
        layers.append(alt.Chart(rd).mark_circle(size=140).encode(x=x, y=alt.Y("y:Q", axis=None),
                      color=alt.Color("kind:N", title=None, scale=alt.Scale(domain=list(BEAT_COL), range=list(BEAT_COL.values())), legend=alt.Legend(orient="bottom")),
                      tooltip=["label:N", "text:N"]).add_params(pick))
    if not layers:
        st.info("no beats or non-dialogue stretches in this sheet")
        return
    ch = alt.layer(*layers).properties(height=150, width="container").resolve_scale(color="independent")
    ev = st.altair_chart(ch, on_select="rerun", selection_mode="pickb" if rows else None, key=key, width="stretch")
    st.caption("dots = beats (click one for the transcript) · bars = non-dialogue stretches with their recommendation")
    try:
        sel = ev.selection.pickb if (ev is not None and rows) else []
    except Exception:
        sel = []
    if sel:
        t = float(sel[0]["t"])
        bt = min(rows, key=lambda r: abs(r["t"] - t))
        lid = bt["label"].split()[-1] if bt["label"].split()[-1].startswith("L") else None
        st.markdown(f"**{bt['label']}** at {fmt_ts(bt['t'])}: {bt['text']}")
        if lid:
            video_at(bt["t"])
            context_block(F, [lid], n=3)


with tabs[2]:
    st.subheader(runner.STAGE_BY_KEY["2"][0])
    st.markdown("One Gemini call per 1B scene, **with the scene media** (low-resolution video, or audio for podcasts): setup / "
                "turn / payoff / reaction, every non-dialogue stretch classified, skippable lines, and `speakers_seen`, the final "
                "word on who is really there. Mismatches become speaker-map corrections you can revert.")
    F = facts_of(vid)
    ok1b = F is not None and "1b" in F.get("stages", {}) and F["stages"]["1b"].get("version") in S2.s1b.COMPATIBLE_VERSIONS \
        and F["stages"]["1a"].get("version") == runner.stage1_facts.VERSION
    if not ok1b:
        st.warning("Stage 2 needs current Stage 1B output (and current 1A). Run them first; Stage 2 refuses otherwise.")
    else:
        scenes = F["episode_pass"]["derived"]["scenes"]
        sheets = {s["scene_id"]: s for s in F.get("beat_sheets") or []}
        dbg_path = P.run_dir / "stage2_debug.json"
        failed = set()
        if dbg_path.exists():
            try:
                for k, v in json.loads(dbg_path.read_text(encoding="utf-8")).get("scenes", {}).items():
                    if int(k) not in sheets and any(a.get("error") or a.get("validation") for a in v.get("attempts", [])):
                        failed.add(int(k))
            except Exception:
                pass
        try:
            est = st.session_state.get(f"est2_{vid}") or S2.estimate_scenes(F, CFG)
        except Exception as e:
            show_exception(e); est = None
        cA, cB, cC = st.columns([1, 1, 3])
        if cA.button("Estimate cost (count tokens)", key=f"est2_btn_{vid}"):
            try:
                from dotenv import load_dotenv; load_dotenv(runner.ROOT / ".env")
                from shotvi.llm.gemini import GeminiProvider
                from shotvi.llm.base import ModelSpec
                prov = GeminiProvider(); spec = ModelSpec.from_dict(CFG["models"]["stage2"])
                st.session_state[f"est2_{vid}"] = S2.estimate_scenes(F, CFG, counter=lambda t: prov.count_tokens(spec, t))
                st.rerun()
            except Exception as e:
                show_exception(e)
        if est:
            st.info(f"media: {est['mode']} at {est['tokens_per_s']} tokens/s · prefix {est['prefix_tokens']:,} tokens · "
                    f"**total ≈ ₹{est['total_inr']:.2f} for {len(est['scenes'])} scenes** · {est['note']}")
        rows = []
        for sc in scenes:
            e = next((x for x in (est or {}).get("scenes", []) if x["scene_id"] == sc["id"]), {})
            stt = S2.scene_stats(F, sc, CFG)
            status = "done" if sc["id"] in sheets else ("failed" if sc["id"] in failed else "not run")
            rows.append({"scene": sc["id"], "range": f"{sc['start_str']}-{sc['end_str']}", "dur_s": e.get("dur_s"), "label": str(sc.get("label"))[:50],
                         "status": status, "thin_diarization": stt["thin"], "why_thin": "; ".join(stt.get("thin_reasons") or []),
                         "est_inr": e.get("inr"), "too_long": e.get("too_long"),
                         "cost_inr": (sheets.get(sc["id"], {}).get("usage") or {}).get("cost_inr")})
        sdf = pd.DataFrame(rows)
        st.dataframe(sdf, width="stretch", hide_index=True, height=min(520, 40 + 36 * len(sdf)))
        remaining = [sc["id"] for sc in scenes if sc["id"] not in sheets]
        default_scene = remaining[0] if remaining else scenes[0]["id"]
        c1, c2, c3 = st.columns([2, 2, 2])
        one = c1.selectbox("scene to run", [sc["id"] for sc in scenes], index=[sc["id"] for sc in scenes].index(default_scene), key=f"one2_{vid}")
        e1 = next((x for x in (est or {}).get("scenes", []) if x["scene_id"] == one), {})
        if c2.button(f"Run one scene (#{one}, ≈₹{e1.get('inr', 0):.2f})", key=f"run2one_{vid}", type="primary"):
            run_stage2(vid, [one])
            st.rerun()
        rem_cost = sum(x["inr"] for x in (est or {}).get("scenes", []) if x["scene_id"] in remaining)
        if c3.button(f"Run all remaining ({len(remaining)} scenes, ≈₹{rem_cost:.2f})", key=f"run2all_{vid}", disabled=not remaining):
            run_stage2(vid, None)
            st.rerun()
        last = st.session_state.get(f"last_2_{vid}")
        if last:
            if last["ok"]:
                st.success(f"Stage 2 finished in {last['s']:.0f} s")
            else:
                st.error(f"Stage 2 FAILED after {last['s']:.0f} s"); show_exception(last["err"], last["tb"])
        mdir_all = P.run_dir / CFG["stage2"]["media_dir"]
        n_media = sum(1 for x in mdir_all.iterdir() if x.is_file()) if mdir_all.exists() else 0
        if st.button(f"Delete media files on this computer ({n_media} files in runs/selection_v2/{vid}/media)", key=f"delmedia_{vid}", disabled=not n_media):
            try:
                from selection_v2.media import delete_local_media
                st.success(f"deleted {delete_local_media(mdir_all)} files; reruns will cut them again")
            except Exception as e:
                show_exception(e)
        F = facts_of(vid) or F
        sheets = {s["scene_id"]: s for s in F.get("beat_sheets") or []}
        if "2" in F.get("stages", {}):
            u = F["stages"]["2"]["usage"]
            st.caption(f"so far: {len(sheets)} of {len(scenes)} scenes · {u['input_tokens']:,} in ({u.get('cached_tokens', 0):,} cached) / "
                       f"{u['output_tokens']:,} out · ₹{u['cost_inr']:.2f} · corrections {F.get('speaker_map', {}).get('n_corrections', 0)}")
        try:
            from selection_v2 import speaker_accuracy as SA
            if SA.has_truth(vid) and F.get("episode_pass"):
                acc = SA.evaluate(F, vid)
                st.info(SA.one_line(acc))
                with st.expander("lines the current map gets wrong vs the true map"):
                    stg = "2" if acc["stages"].get("2") else "1b"
                    st.dataframe(pd.DataFrame(acc["wrong"].get(stg) or []), width="stretch", hide_index=True)
        except Exception as e:
            show_exception(e)
        # acceptance
        ref2 = acceptance.load_acceptance(vid, "2")
        if ref2 and sheets:
            st.markdown("### Acceptance tests (Stage 2)")
            rws = acceptance.evaluate_1b(F, ref2)
            st.write(acceptance.summary(rws))
            for r in rws:
                icon = {"agree": "🟢", "disagree": "🔴", "missing": "⚪", "error": "⚠️"}[r["status"]]
                st.write(f"{icon} **{r['expect']}** — {r['got']}")
        # corrections
        sm = F.get("speaker_map") or {}
        if sm.get("corrections"):
            st.markdown("### Speaker map corrections (from speakers_seen; 1B's map is kept, toggle to revert)")
            for c in sm["corrections"]:
                cols = st.columns([5, 1])
                cols[0].markdown(f"{'~~' if c['reverted'] else '**'}scene {c['scene_id']} · {c['key']} {c['old_name']} → {c['new_name']}"
                                 f"{' (' + c['new_key'] + ')' if c.get('new_key') else ''} · {c['n_lines']} lines · {c['voice_source']}"
                                 f"{'**' if not c['reverted'] else '~~'} — {str(c.get('evidence'))[:160]}")
                rev = cols[1].toggle("revert", value=bool(c["reverted"]), key=f"rev_{vid}_{c['id']}")
                if rev != bool(c["reverted"]):
                    c["reverted"] = rev
                    F["speaker_map"] = S2.build_speaker_map(F)
                    from selection_v2.paths import write_json
                    write_json(P.facts, F)
                    st.rerun()
        # reference faces: the cast sheet picks them (one call); the row shows what the next scene call will carry
        from selection_v2 import cast_sheet as CS, faces as FB
        st.markdown("### Reference faces")
        cs = F.get("cast_sheet")
        try:
            cse = CS.estimate(F, CFG)
        except Exception as e:
            show_exception(e); cse = None
        cA, cB = st.columns([2, 5])
        if cA.button(f"Pick reference faces (cast sheet, {cse['n_frames'] if cse else '?'} frames, ≈₹{cse['inr'] if cse else '?'})", key=f"cast_{vid}", type="primary" if not cs else "secondary"):
            ok = False
            with st.spinner("cutting candidate frames and asking Gemini who is in each (one call)"):
                try:
                    runner.run_substage(vid, "2:cast_sheet", CS.run, CFG)
                    ok = True
                except Exception as e:  # noqa: BLE001 - shown in full; the rerun below is outside this block
                    show_exception(e, traceback.format_exc())
            if ok:
                st.rerun()
        if cs:
            u = cs["usage"]
            cB.caption(f"cast sheet {cs['generated_at']}: {len(cs['frames'])} candidate frames, kept {sum(len(v) for v in cs['chosen'].values())} for "
                       f"{len(cs['chosen'])} keys · ₹{u['cost_inr']:.2f} · {'VERIFIED and saved' if cs.get('verified') else 'not yet saved to the series bank'}")
        names_map, _ = S2.map_names(F)
        refm = []
        try:
            mdir = P.run_dir / CFG["stage2"]["media_dir"]
            bank = FB.lookup(F["episode"].get("series_key"), names_map, CFG)
            for k, frs in bank.items():
                refm += [{**fr, "key": k, "name": names_map.get(k), "source": "bank"} for fr in frs]
            for k, frs in ((cs or {}).get("chosen") or {}).items():
                if k not in bank:
                    refm += [{**fr, "key": k, "name": names_map.get(k), "source": "cast sheet"} for fr in frs]   # the current map wins over the frame's own key/name
            for k, frs in S2.manual_references(F, CFG).items():
                refm = [m for m in refm if m["key"] != k] + [{"key": k, "name": names_map.get(k), **fr, "source": "pinned",
                                                              "path": str(mdir / f"ref_{k}_{fr['t']:.1f}.jpg")} for fr in frs]
        except Exception as e:
            show_exception(e)
        looks = {}
        try:
            looks = S2.all_looks(F, CFG)
        except Exception as e:
            show_exception(e)
        if looks:
            with st.expander(f"Look descriptions ({len(looks)}; these go into every scene call next to the speaker map)"):
                for k in sorted(looks):
                    st.write(f"**{k} {names_map.get(k)}**: {looks[k]}")
        if cs:
            with st.expander("Every candidate frame and the model's verdict (who is visible, how many people)"):
                cdf = pd.DataFrame([{k2: v for k2, v in f.items() if k2 not in ("path",)} for f in cs["frames"]])
                st.dataframe(cdf, width="stretch", hide_index=True, height=min(500, 40 + 36 * len(cdf)))
        if not refm:
            st.info("no reference faces yet: press Pick reference faces (the automatic midpoint frames are no longer used as references)")
        else:
            missing_keys = [f"{k} ({names_map.get(k)})" for k in sorted(names_map) if k not in {m["key"] for m in refm}]
            st.caption(f"{len(refm)} frames for {len({m['key'] for m in refm})} characters; sources: "
                       f"{dict(pd.Series([m['source'] for m in refm]).value_counts())}"
                       + (f"; no usable frame for {missing_keys}" if missing_keys else ""))
            cols = st.columns(4)
            for i, m in enumerate(refm):
                pth = Path(m["path"])
                cap = (f"{m['source']} · {m['key']} {m['name']} · {m.get('t_str')} {m.get('line') or ''}"
                       + (f" · {m.get('n_people_visible')} person(s)" if m.get("n_people_visible") else "") + f" · {m.get('why', '')}")
                if pth.exists():
                    cols[i % 4].image(str(pth), caption=cap, width="stretch")
                else:
                    cols[i % 4].caption(cap + " (file missing)")
            sk = F["episode"].get("series_key")
            st.caption(f"Check every picture: is it the named person, alone? If one is wrong, pin times by hand in "
                       f"selection_v2/reference/faces/{vid}.yaml and relaunch. Then save: nothing enters data/series_faces/{sk or '?'}/ "
                       f"without this button.")
            if st.button("Save these faces and looks as verified for the series", key=f"savefaces_{vid}", disabled=not sk):
                try:
                    have = [m for m in refm if Path(m["path"]).exists() and m["source"] != "bank"]
                    looks_all = {**((cs or {}).get("looks") or {}), **{k: {"look": v} for k, v in S2.manual_looks(F, CFG).items()}}
                    bank = FB.save_verified(sk, vid, have, CFG, int(CFG["stage2"].get("faces_bank_max_per_name", 4)), looks=looks_all)
                    if cs:
                        F["cast_sheet"]["verified"] = True
                        from selection_v2.paths import write_json
                        write_json(P.facts, F)
                    st.success(f"saved {len(have)} frames and the looks for {len(bank['faces'])} characters to {FB.bank_dir(sk, CFG)}")
                    saved_ok = True
                except Exception as e:
                    saved_ok = False
                    show_exception(e)
                if saved_ok:
                    st.rerun()
        # per-scene view
        if sheets:
            st.markdown("### Scene view")
            pick_id = st.selectbox("scene", sorted(sheets), key=f"view2_{vid}")
            sh = sheets[pick_id]
            st.markdown(f"**Scene {sh['scene_id']} {sh['start_str']}-{sh['end_str']}** · {sh.get('label')} · register **{sh.get('register')}** · "
                        f"self-contained {sh.get('self_contained')} · confidence {sh.get('confidence')} · needs default-res video {sh.get('needs_video_default_res')}"
                        + (" · **thin diarization**" if sh.get("thin_diarization") else ""))
            st.markdown(f"setup: {sh.get('setup')}")
            po, rx = sh.get("payoff") or {}, sh.get("reaction") or {}
            st.markdown(f"payoff: **{po.get('line')} {po.get('t_str')}** {po.get('text')} — {po.get('note')}  ·  reaction: {rx.get('type')} by {rx.get('by')} at {rx.get('t_str')}")
            try:
                scene_chart(sh, F, f"sc2chart_{vid}_{pick_id}")
            except Exception as e:
                show_exception(e)
            cA, cB = st.columns(2)
            with cA:
                st.markdown("**speakers seen** (mismatches highlighted)")
                ssdf = pd.DataFrame([{k: v for k, v in x.items() if k != "line_ids"} for x in sh.get("speakers_seen") or []])
                if len(ssdf):
                    st.dataframe(ssdf.style.apply(lambda row: ["background-color: #4a2a1a" if row.get("mismatch") else "" for _ in row], axis=1),
                                 width="stretch", hide_index=True)
                st.markdown("**dynamic**"); st.write(sh.get("dynamic"))
                st.markdown("**delivery notes**"); st.write(sh.get("delivery_notes"))
                st.markdown("**context required / callbacks**"); st.write({"context_required": sh.get("context_required"), "callbacks": sh.get("callbacks")})
            with cB:
                st.markdown("**non-dialogue stretches**")
                st.dataframe(pd.DataFrame([{k: v for k, v in x.items() if k not in ("shot_cuts",)} for x in sh.get("non_dialogue") or []]), width="stretch", hide_index=True)
                st.markdown("**skippable lines**")
                st.dataframe(pd.DataFrame(sh.get("skippable_lines") or []), width="stretch", hide_index=True)
                st.markdown("**non-story confirmations / device audio**")
                st.write({"non_story_confirm": sh.get("non_story_confirm"), "other_device_audio": sh.get("other_device_audio")})
                st.markdown("**clip hints**"); st.write(sh.get("clip_hints"))
            with st.expander("Exact prompt sent and raw JSON for this scene"):
                if dbg_path.exists():
                    dbg = json.loads(dbg_path.read_text(encoding="utf-8"))
                    st.markdown(f"**system**"); st.code(dbg.get("system", ""), language="text")
                    st.markdown(f"**prefix** `{P.run_dir / 'prefix_stage2.txt'}` ({dbg.get('prefix_chars', 0):,} chars; the 1B prefix + the 1B results)")
                    for att in (dbg.get("scenes", {}).get(str(pick_id)) or {}).get("attempts", []):
                        st.markdown(f"**call {att['n']}** · media {att.get('media')} · usage {att.get('usage')} · errors shown {att.get('errors_shown')}")
                        st.code(att.get("scene_facts", ""), language="text")
                        st.code(att.get("instructions", ""), language="text")
                        st.code(att.get("raw_text") or att.get("error") or "", language="json")
                        if att.get("validation"):
                            st.write({"validation": att["validation"]})
            with st.expander("raw beat sheet JSON"):
                st.json(sh, expanded=False)

# ---- Stage 3
from selection_v2 import stage3_candidates as S3  # noqa: E402


def candidate_transcript_html(F: dict, c: dict) -> str:
    """The candidate's transcript: kept lines plain, skipped lines struck through, shortened / removed stretches marked
    where they start, lines just outside the candidate greyed."""
    import html as _h
    from selection_v2.stage1_facts import parse_ts
    segs = c["segments"]
    skipped = set(c["removed"]["lines_skipped"])
    markers = []
    for g in c["removed"]["gaps_shortened"]:
        a = parse_ts(g["range"].split("-")[0])[0]
        markers.append((a, f"— stretch {g['range']} ({g.get('kind')}) shortened to {g['to_s']} s, cut at {fmt_ts(g['cut_at'])} —"))
    for g in c["removed"]["gaps_removed"]:
        a = parse_ts(g["range"].split("-")[0])[0]
        markers.append((a, f"— stretch {g['range']} ({g.get('kind')}) removed —"))
    out = ["<pre style='font-size:13px;line-height:1.5;white-space:pre-wrap'>"]
    rows = [r for r in F["lines"] if r["end"] > c["start"] - 3 and r["start"] < c["end"] + 3]
    for r in rows:
        txt = _h.escape(line_fmt(r))
        inside = r["start"] >= c["start"] - 1e-3 and r["end"] <= c["end"] + 1e-3
        kept = any(a - 1e-3 <= r["start"] and r["end"] <= b + 1e-3 for a, b in segs)
        if r["id"] in skipped:
            out.append(f"<s style='opacity:.6'>{txt}</s>   ← skipped")
        elif not inside:
            out.append(f"<span style='opacity:.4'>{txt}</span>")
        elif r["kind"] == "event" and not kept:
            out.append(f"<s style='opacity:.6'>{txt}</s>   ← inside a shortened / removed stretch")
        else:
            out.append(txt)
        if r["kind"] == "speech":
            for a, text in markers:
                if abs(a - r["end"]) <= 0.35:
                    out.append(f"<b style='color:#d95926'>{_h.escape(text)}</b>")
    out.append("</pre>")
    return "\n".join(out)


def candidates_timeline(F: dict, key: str):
    import altair as alt
    cands = F.get("candidates") or []
    if not cands:
        st.info("no candidates yet")
        return
    scenes = (((F.get("episode_pass") or {}).get("derived")) or {}).get("scenes") or []
    lanes = {"comedy": 3, "emotional": 2, "hook": 1, "loop": 0}
    rows = []
    for c in cands:
        for i, (a, b) in enumerate(c["segments"]):
            rows.append({"id": c["id"], "type": c["type"], "variant": c["variant"], "m0": a / 60, "m1": b / 60, "lane": lanes.get(c["type"], 0),
                         "scene": c["scene_id"], "len": f"{c['final_duration']:.0f} s (raw {c['raw_duration']:.0f} s)", "span": f"{c['start_str']}-{c['end_str']}",
                         "seg": f"{i + 1}/{len(c['segments'])}", "y": lanes.get(c["type"], 0) * 10 + (hash(c["id"]) % 7)})
    cdf = pd.DataFrame(rows)
    sdf = pd.DataFrame([{"m": sc["start"] / 60, "label": f"scene {sc['id']} {sc['start_str']}"} for sc in scenes])
    dur = F["episode"]["duration"] / 60
    x = alt.X("m0:Q", title="minutes", scale=alt.Scale(domain=[0, dur], nice=False))
    bars = alt.Chart(cdf).mark_bar(height=6).encode(
        x=x, x2="m1:Q", y=alt.Y("y:Q", axis=None, scale=alt.Scale(domain=[-2, 40])),
        color=alt.Color("type:N", scale=alt.Scale(domain=list(lanes), range=["#3987e5", "#9085e9", "#c98500", "#199e70"]), legend=alt.Legend(orient="top")),
        tooltip=[alt.Tooltip("id:N"), alt.Tooltip("type:N"), alt.Tooltip("variant:N"), alt.Tooltip("span:N", title="span"), alt.Tooltip("len:N", title="length"), alt.Tooltip("seg:N", title="segment")])
    edges = alt.Chart(sdf).mark_rule(strokeDash=[6, 3], color=SCENE, strokeWidth=2).encode(x=alt.X("m:Q"), tooltip=[alt.Tooltip("label:N")])
    st.altair_chart((edges + bars).properties(height=260, width="container").configure_view(strokeWidth=0), key=key)
    st.caption("bars = candidate segments (one row per candidate, a trimmed candidate shows gaps) · lanes top to bottom: comedy, emotional, hook, loop · "
               "dashed pink = Stage 1B scene starts")


with tabs[3]:
    st.subheader("Stage 3: candidate generation (code, free)")
    st.markdown("For every scene with a beat sheet: the moments cut into candidates with exact boundaries on gap-table pauses, "
                "typed (comedy / emotional / hook / loop) and in several lengths. The untrimmed full arc is always kept; trimmed "
                "variants carry a kept-segment list and record what was removed. Caps and every number: `pipeline.yaml → stage3`.")
    F = facts_of(vid)
    ok2 = F is not None and F.get("beat_sheets")
    if not ok2:
        st.warning("Stage 3 needs Stage 2 beat sheets. Run Stage 2 first (at least one scene).")
    else:
        n_sc = len(F["episode_pass"]["derived"]["scenes"]); n_sh = len(F["beat_sheets"])
        if n_sh < n_sc:
            st.warning(f"beat sheets for {n_sh} of {n_sc} scenes: the scenes without one get no candidates")
        run_button(vid, "3", "Stage 3 now (free)")
        F = facts_of(vid)
    if F and "3" in F.get("stages", {}):
        st3 = F["stages"]["3"]; cnt = st3.get("counts") or {}
        st.success(f"{cnt.get('kept', 0)} candidates kept of {cnt.get('generated', 0)} generated ({cnt.get('after_dedupe', 0)} after merging exact duplicates, "
                   f"{cnt.get('dropped_by_caps', 0)} dropped by the caps) · by type {cnt.get('by_type')} · untrimmed arcs over the platform max: "
                   f"{cnt.get('untrimmed_over_platform_max', 0)} · {st3['generated_at']} · {st3['duration_s']:.2f} s")
        for w in st3.get("warnings") or []:
            st.warning(w)
        ref3 = acceptance.load_acceptance(vid, "3")
        if ref3:
            st.markdown("### Acceptance tests")
            rws = acceptance.evaluate_1b(F, ref3)
            st.write(acceptance.summary(rws))
            for r in rws:
                icon = {"agree": "🟢", "disagree": "🔴", "missing": "⚪", "error": "⚠️"}[r["status"]]
                st.write(f"{icon} **{r['expect']}** — {r['got']}")
        else:
            st.caption(f"no acceptance tests for this episode (selection_v2/reference/acceptance/{vid}.3.yaml)")
        st.markdown("### Timeline (candidates as bars under the scene edges)")
        try:
            candidates_timeline(F, f"tl3_{vid}")
        except Exception as e:
            show_exception(e)
        st.markdown("### Candidates (click a row for its transcript; removed parts struck through)")
        cands = F.get("candidates") or []
        from selection_v2 import preview as PV
        n_done = sum(1 for c in cands if PV.is_current(P.run_dir, c))
        cR1, cR2 = st.columns([1, 3])
        if cR1.button(f"Render all {len(cands)} preview clips (ffmpeg, free)", key=f"render_all_{vid}"):
            bar = st.progress(0.0, text="rendering")
            res = PV.render_all(Path(P.mp4), P.run_dir, cands, int(CFG["stage2"]["media_max_height"]),
                                progress=lambda i, n, cid: bar.progress(i / max(n, 1), text=f"rendering {cid} ({i + 1}/{n})"))
            bar.progress(1.0, text="done")
            errs = {k: v for k, v in res.items() if str(v).startswith("ERROR")}
            if errs:
                st.error(f"{len(errs)} clip(s) failed"); st.write(errs)
            st.rerun()
        cR2.caption(f"{n_done} of {len(cands)} preview clips rendered in {P.run_dir / 'clips'} (480p, trims applied, segments joined). "
                    "Click a row: its clip plays below the transcript, or a button renders just that one.")
        cdf = pd.DataFrame([{"id": c["id"], "scene": c["scene_id"], "type": c["type"], "variant": c["variant"], "start": c["start_str"], "end": c["end_str"],
                             "raw_s": round(c["raw_duration"], 1), "final_s": round(c["final_duration"], 1), "vs_target": c.get("length_vs_target"),
                             "beats": len(c["beats_included"]), "trims": (f"{len(c['removed']['gaps_shortened'])} shortened, {len(c['removed']['gaps_removed'])} removed, "
                                                                          f"{len(c['removed']['lines_skipped'])} lines skipped") if c.get("trim_level") != "none" else "untrimmed",
                             "end": c["end_str"], "end_note": c.get("end_note"), "register": c.get("register"), "flags": ", ".join(k for k, v in (c.get("flags") or {}).items() if v is True)}
                            for c in cands])
        sel = selected_rows(cdf, f"cands_{vid}", height=min(620, 40 + 36 * max(1, len(cdf))))
        if sel:
            c = cands[sel[0]]
            st.markdown(f"**{c['id']}** · {c['type']} / {c['variant']} · {c['start_str']}–{c['end_str']} · raw {c['raw_duration']:.1f} s → final {c['final_duration']:.1f} s "
                        f"· target {c['target_s']} ({c.get('length_vs_target')}) · beats {', '.join(c['beats_included'])} · reaction {c.get('reaction')} · {c.get('note')}")
            st.caption(f"segments: {', '.join(f'{fmt_ts(a)}-{fmt_ts(b)}' for a, b in c['segments'])} · extra lines at start {c.get('extra_lines', {}).get('start')} / end {c.get('extra_lines', {}).get('end')} · {c.get('end_note')}")
            if c.get("context_required"):
                st.caption("context required: " + " | ".join(c["context_required"]))
            st.markdown(candidate_transcript_html(F, c), unsafe_allow_html=True)
            if PV.is_current(P.run_dir, c):
                st.markdown(f"**▶ preview of {c['id']} as cut** ({c['final_duration']:.1f} s, {len(c['segments'])} segment(s) joined)")
                st.video(str(PV.clip_path(P.run_dir, c["id"])))
            else:
                if st.button(f"Render {c['id']} preview (ffmpeg, free)", key=f"render_{vid}_{c['id']}"):
                    try:
                        with st.spinner(f"cutting {len(c['segments'])} segment(s) and joining them"):
                            PV.render(Path(P.mp4), P.run_dir, c, int(CFG["stage2"]["media_max_height"]))
                    except Exception as e:
                        show_exception(e)
                    st.rerun()
                st.caption("source video from the candidate's start (untrimmed) until the preview is rendered:")
                video_at(c["start"], lead_s=0.0)
        with st.expander(f"dropped by the caps ({len(st3.get('dropped') or [])}), merged duplicates ({len(st3.get('merged') or [])}), not generated ({len(st3.get('not_generated') or [])}), invalid ({len(st3.get('invalid') or [])})"):
            for name in ("dropped", "merged", "not_generated", "invalid"):
                if st3.get(name):
                    st.markdown(f"**{name}**"); st.dataframe(pd.DataFrame(st3[name]).astype(str), width="stretch", hide_index=True)
        with st.expander("raw candidates JSON"):
            st.json(cands, expanded=False)

# ---- Stage 4
from selection_v2 import stage4_scoring as S4  # noqa: E402

with tabs[4]:
    st.subheader("Stage 4: scoring and vetoes (Gemini per scene, text only)")
    st.markdown("One call per scene scoring all of its candidates together: seven marks 1–10 with evidence (hook, payoff, "
                "open_question, standalone, pace, delivery, series_pull), fan_clip and a verdict for every twin pair. Critic-trimmed "
                "siblings are scored in a second, text-only call. "
                "Code applies the vetoes and the combined score; thresholds and weights: `pipeline.yaml → stage4`.")
    F = facts_of(vid)
    try:
        S4.check_stage3(F); ok3 = True
    except Exception as e:
        ok3 = False
        st.warning(f"Stage 4 refuses to run: {e}")
    if ok3:
        cands = F["candidates"]
        scored = {s["id"]: s for s in ((F.get("scores") or {}).get("candidates") or [])}
        current = (F.get("scores") or {}).get("stage3_generated_at") == F["stages"]["3"]["generated_at"]
        if scored and not current:
            st.warning("the scores on disk were made for an older Stage 3 run; rerun Stage 4 (every scene) after a new Stage 3")
        auto_mode = S4.media_mode(F, CFG)
        mchoice = st.radio("media for the Stage 4 call", [f"auto ({auto_mode})", "video", "none"], horizontal=True, key=f"media4_{vid}",
                           help="auto = config stage4.media_by_content_type (video for scripted series, none for podcasts); the scene cut and upload from Stage 2 are reused while they exist")
        media4 = None if mchoice.startswith("auto") else mchoice
        mode4 = S4.media_mode(F, CFG, media4)
        try:
            est = st.session_state.get(f"est4_{vid}_{mode4}") or S4.estimate(F, CFG, run_dir=P.run_dir, media=mode4)
        except Exception as e:
            show_exception(e); est = None
        cA, cB = st.columns([1, 3])
        if cA.button("Estimate cost (count tokens)", key=f"est4_btn_{vid}"):
            try:
                from dotenv import load_dotenv; load_dotenv(runner.ROOT / ".env")
                from shotvi.llm.gemini import GeminiProvider
                from shotvi.llm.base import ModelSpec
                prov = GeminiProvider(); spec = ModelSpec.from_dict(CFG["models"].get("stage4") or CFG["models"]["default"])
                st.session_state[f"est4_{vid}_{mode4}"] = S4.estimate(F, CFG, counter=lambda t: prov.count_tokens(spec, t), run_dir=P.run_dir, media=mode4)
                st.rerun()
            except Exception as e:
                show_exception(e)
        if est:
            cB.info(f"media **{est['media']}** · prefix {est['prefix_tokens']:,} tokens · {est['n_candidates']} candidates in {len(est['scenes'])} scenes · "
                    f"**total ≈ ₹{est['total_inr']:.2f}** (₹{est.get('total_inr_if_cache_hits', est['total_inr']):.2f} if the prefix cache hits)" + (f" (of which scene video ₹{est['media_inr']:.2f})" if est["media"] == "video" else "") + f" · {est['note']}")
        this_run = next((r for r in S4.runs_of(F) if r["run_key"] == S4.run_key(mode4, F)), None)
        scored_scenes = set((this_run or {}).get("scenes_scored") or [])
        rows = []
        for e in (est or {}).get("scenes", []):
            rows.append({"scene": e["scene_id"], "candidates": e["n_candidates"], "scene_tokens": e["scene_tokens"], "media_tokens": e.get("media_tokens", 0),
                         "expected_out": e["expected_output_tokens"], "est_inr": e["inr"], "status": f"scored ({mode4})" if e["scene_id"] in scored_scenes else "not scored"})
        if rows:
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=min(500, 40 + 36 * len(rows)))
        remaining = [r["scene"] for r in rows if r["status"] != "scored"]
        c1, c2, c3 = st.columns([2, 1, 1])
        one = c1.selectbox("scene to score", [r["scene"] for r in rows] or [None], key=f"pick4_{vid}")
        e1 = next((r for r in rows if r["scene"] == one), {})
        def _run4(ids, label):
            t0 = time.perf_counter()
            with st.spinner(f"Stage 4 ({mode4}): {label}; keep this tab open"):
                try:
                    res = runner.run_stage(vid, "4", CFG, scene_ids=ids, media=mode4)
                    st.session_state[f"last_4_{vid}"] = {"ok": True, "s": res.duration_s, "warn": res.warnings}
                except BaseException as e:  # noqa: BLE001
                    st.session_state[f"last_4_{vid}"] = {"ok": False, "s": time.perf_counter() - t0, "err": e, "tb": traceback.format_exc()}
            st.rerun()
        if c2.button(f"Score one scene (#{one}, ≈₹{e1.get('est_inr', 0):.2f})", key=f"run4one_{vid}", type="primary"):
            _run4([one], f"scene {one}")
        rem_cost = sum(r["est_inr"] for r in rows if r["scene"] in remaining)
        if c3.button(f"Score all remaining ({len(remaining)} scenes, ≈₹{rem_cost:.2f})", key=f"run4all_{vid}", disabled=not remaining):
            _run4(None, f"{len(remaining)} scenes")
        # founder, 2026-10-28: after a Stage 3 rerun, rescore only the changed candidates (+ the sibling each is judged against)
        # and carry the rest over from the ACTIVE run by content (scene, type, variant, segments)
        base4 = S4.active_run(F)
        if base4 and not current:
            try:
                carried4, changed4, _ = S4.carry_over(F, base4)
                est_ch = S4.estimate(F, CFG, run_dir=P.run_dir, media=mode4, changed_only=True)
                st.info(f"changed since the active run {base4['run_key']}: **{len(changed4)} candidate(s)** in {len(est_ch['scenes'])} scene(s); "
                        f"{len(carried4)} carried over · rescoring sends {est_ch['n_candidates']} candidates (changed + their siblings) ≈ **₹{est_ch['total_inr']:.2f}** (₹{est_ch.get('total_inr_if_cache_hits', est_ch['total_inr']):.2f} if the prefix cache hits)"
                        + (f" (scene video ₹{est_ch['media_inr']:.2f})" if est_ch["media"] == "video" else ""))
                with st.expander("changed candidates"):
                    st.dataframe(pd.DataFrame([{"id": c["id"], "scene": c["scene_id"], "type": c["type"], "variant": c["variant"], "span": f"{c['start_str']}-{c['end_str']}",
                                                "len_s": round(c["final_duration"], 1), "segments": len(c["segments"])} for c in changed4]), width="stretch", hide_index=True)
                if st.button(f"Rescore changed candidates only ({est_ch['n_candidates']} candidates, {len(est_ch['scenes'])} scenes, ≈₹{est_ch['total_inr']:.2f}; the rest carried)",
                             key=f"run4changed_{vid}", type="primary", disabled=not changed4):
                    t0 = time.perf_counter()
                    with st.spinner(f"Stage 4 ({mode4}): rescoring {est_ch['n_candidates']} changed candidates; keep this tab open"):
                        try:
                            res = runner.run_stage(vid, "4", CFG, scene_ids=None, media=mode4, changed_only=True)
                            st.session_state[f"last_4_{vid}"] = {"ok": True, "s": res.duration_s, "warn": res.warnings}
                        except BaseException as e:  # noqa: BLE001
                            st.session_state[f"last_4_{vid}"] = {"ok": False, "s": time.perf_counter() - t0, "err": e, "tb": traceback.format_exc()}
                    st.rerun()
            except Exception as e:
                show_exception(e)
        last = st.session_state.get(f"last_4_{vid}")
        if last:
            if last["ok"]:
                st.success(f"Stage 4 finished in {last['s']:.1f} s")
                for w in last.get("warn") or []:
                    st.warning(w)
            else:
                st.error(f"Stage 4 FAILED after {last['s']:.1f} s"); show_exception(last["err"], last["tb"])
        F = facts_of(vid)
        scored = {s["id"]: s for s in ((F.get("scores") or {}).get("candidates") or [])}
    if ok3 and scored:
        runs4 = S4.runs_of(F)
        st.markdown("### Runs (A = text only, B = with scene video); downstream stages use the ACTIVE one")
        for r in runs4:
            cr1, cr2 = st.columns([4, 1])
            stale = r.get("stage3_generated_at") != F["stages"]["3"]["generated_at"]
            cr1.write(f"{'✅ active' if r['run_key'] == F.get('scores_active') else '▫️'} **{r['run_key']}** · media {r['media']} · "
                      f"{len(r.get('scenes_scored') or [])}/{r.get('n_scenes')} scenes · statuses {r.get('statuses')} · ₹{(r.get('usage') or {}).get('cost_inr', 0):.2f} · {r.get('generated_at')}"
                      + (" · STALE (older Stage 3)" if stale else ""))
            if r["run_key"] != F.get("scores_active") and cr2.button("mark active", key=f"act4_{vid}_{r['run_key']}"):
                try:
                    from selection_v2.paths import write_json as _wj
                    newF = S4.set_active(F, r["run_key"]); runner.validate_facts(newF); _wj(P.facts, newF)
                except Exception as e:
                    show_exception(e)
                st.rerun()
        if len(runs4) >= 2:
            st.markdown("### Compare runs")
            keys4 = [r["run_key"] for r in runs4]
            da = next((k for k in keys4 if k.startswith("none@")), keys4[0]); db_ = next((k for k in keys4 if k.startswith("video@")), keys4[-1])
            ca_, cb_ = st.columns(2)
            ka = ca_.selectbox("run A", keys4, index=keys4.index(da), key=f"cmpA_{vid}"); kb = cb_.selectbox("run B", keys4, index=keys4.index(db_), key=f"cmpB_{vid}")
            try:
                cmp = S4.compare_runs(next(r for r in runs4 if r["run_key"] == ka), next(r for r in runs4 if r["run_key"] == kb))
                c1_, c2_ = st.columns(2)
                c1_.markdown(f"**rejected in A only** ({len(cmp['rejected_a_only'])})"); c1_.dataframe(pd.DataFrame(cmp["rejected_a_only"]).astype(str), width="stretch", hide_index=True)
                c2_.markdown(f"**rejected in B only** ({len(cmp['rejected_b_only'])})"); c2_.dataframe(pd.DataFrame(cmp["rejected_b_only"]).astype(str), width="stretch", hide_index=True)
                c1_.markdown("**top ten, A**"); c1_.dataframe(pd.DataFrame(cmp["top_a"]), width="stretch", hide_index=True)
                c2_.markdown("**top ten, B**"); c2_.dataframe(pd.DataFrame(cmp["top_b"]), width="stretch", hide_index=True)
                st.markdown("**B: removed parts that should be restored (removed_needed)**")
                st.dataframe(pd.DataFrame([{"id": x["id"], **it} for x in cmp["b_removed_needed"] for it in x["items"]]).astype(str), width="stretch", hide_index=True)
                st.markdown("**B: kept stretches a viewer would swipe past (kept_dead)**")
                st.dataframe(pd.DataFrame([{"id": x["id"], **it} for x in cmp["b_kept_dead"] for it in x["items"]]).astype(str), width="stretch", hide_index=True)
            except Exception as e:
                show_exception(e)
        sc4 = F["scores"]; st4 = F["stages"].get("4") or {}
        u = st4.get("usage") or {}
        st.success(f"ACTIVE run {sc4.get('run_key')} (media {sc4.get('media')}): {len(sc4.get('candidates') or [])} candidates scored in {len(sc4.get('scenes_scored') or [])} of {sc4.get('n_scenes')} scenes · "
                   f"statuses {sc4.get('statuses')} · ₹{(sc4.get('usage') or {}).get('cost_inr', 0):.2f} · {sc4.get('generated_at')}")
        scored = {s["id"]: s for s in sc4["candidates"]}
        ref4 = acceptance.load_acceptance(vid, "4")
        if ref4:
            st.markdown("### Acceptance tests")
            rws = acceptance.evaluate_1b(F, ref4)
            st.write(acceptance.summary(rws))
            for r in rws:
                icon = {"agree": "🟢", "disagree": "🔴", "missing": "⚪", "error": "⚠️"}[r["status"]]
                st.write(f"{icon} **{r['expect']}** — {r['got']}")
        cand_by = {c["id"]: c for c in F["candidates"]}
        st.markdown("### Scores (click a row for the evidence and the transcript as cut)")
        def dim(s, d):
            return s["scores"].get(d)
        sdf = pd.DataFrame([{"id": s["id"], "scene": s["scene_id"], "type": s["type"], "variant": s["variant"], "len_s": round(s["final_duration"], 1),
                             "hook": dim(s, "hook"), "payoff": dim(s, "payoff"), "open_q": dim(s, "open_question") if "open_question" in s["scores"] else dim(s, "loop_strength"),
                             "standalone": dim(s, "standalone"), "pace": dim(s, "pace"), "delivery": dim(s, "delivery"), "series_pull": dim(s, "series_pull"),
                             "combined": s["combined"], "status": s["status"], "vetoes": ", ".join(v["rule"] for v in s["vetoes"]) or "",
                             "flags": ", ".join(k for k, v in s["flags"].items() if v) + (f" widened_to {s['widened_to']}" if s["widened_to"] else ""),
                             "fan_clip": s["fan_clip"], "sibling verdict": (s.get("verdict_vs_sibling") or "")[:90]}
                            for s in sc4["candidates"]])
        sel4 = selected_rows(sdf, f"scores_{vid}", height=min(640, 40 + 36 * max(1, len(sdf))))
        if sel4:
            s = sc4["candidates"][sel4[0]]; c = cand_by.get(s["id"])
            st.markdown(f"**{s['id']}** · {s['type']} / {s['variant']} · {c['start_str']}–{c['end_str']} · {s['final_duration']:.1f} s · combined **{s['combined']}** · "
                        f"status **{s['status']}** · weights {s['weights_used']}")
            for d in ("hook", "payoff", "open_question", "loop_strength", "standalone", "pace", "delivery", "series_pull"):
                if d in s["scores"]:
                    st.write(f"**{d} {s['scores'][d]}** — {s['evidence'].get(d)}")
            if s["dead_stretches"]:
                st.write({"dead stretches": s["dead_stretches"]})
            if s["vetoes"]:
                st.write({"vetoes": s["vetoes"]})
            if s.get("verdict_vs_sibling"):
                st.write(f"**vs sibling {s['sibling']}:** {s['verdict_vs_sibling']}")
            if s.get("removed_needed"):
                st.write({"removed, but needed": s["removed_needed"]})
            if s.get("kept_dead"):
                st.write({"kept, but dead": s["kept_dead"]})
            st.markdown(candidate_transcript_html(F, c), unsafe_allow_html=True)
        st.markdown("### Rejected, by veto reason")
        rej = [s for s in sc4["candidates"] if s["status"] in ("rejected", "fan_clip_only", "widened")]
        if rej:
            groups = {}
            for s in rej:
                for v in s["vetoes"]:
                    groups.setdefault(v["rule"], []).append((s, v))
            for rule, items in sorted(groups.items()):
                with st.expander(f"{rule}: {len(items)} candidate(s)"):
                    for s, v in items:
                        c = cand_by[s["id"]]
                        st.write(f"**{s['id']}** {s['type']}/{s['variant']} {c['start_str']}–{c['end_str']} ({s['final_duration']:.0f} s) · status {s['status']}"
                                 + (f" · widened to {v.get('widened_to')}" if v.get("widened_to") else "") + f" — {s['evidence'].get(v['dimension'])}")
        else:
            st.caption("nothing vetoed")
        st.markdown("### Twin pairs (untrimmed vs trimmed) with the model's verdict")
        pairs = [(s, scored.get(s["sibling"])) for s in sc4["candidates"] if s.get("sibling") and s["id"] < s["sibling"] and scored.get(s["sibling"])]
        for a, b in pairs:
            ca, cb = st.columns(2)
            for col, s in ((ca, a), (cb, b)):
                col.markdown(f"**{s['id']}** {s['variant']} · {s['final_duration']:.0f} s · combined {s['combined']} · {s['status']}")
                col.caption(" · ".join(f"{d} {s['scores'][d]}" for d in s["scores"]))
            st.caption(f"verdict ({a['id']}): {a.get('verdict_vs_sibling')}" + (f" · ({b['id']}): {b.get('verdict_vs_sibling')}" if b.get("verdict_vs_sibling") else ""))
        with st.expander("exact prompt and raw JSON per scene"):
            dbg_path = P.run_dir / "stage4_debug.json"
            if dbg_path.exists():
                dbg4 = json.loads(dbg_path.read_text(encoding="utf-8"))
                st.markdown(f"**system**"); st.code(dbg4.get("system", ""), language="text")
                st.caption(f"prefix: {P.run_dir / 'prefix_stage2.txt'} ({dbg4.get('prefix_chars', 0):,} chars), sent first and unchanged")
                pick4 = st.selectbox("call (media:scene)", sorted(dbg4.get("scenes", {}), key=lambda k: (k.split(":")[0], int(k.split(":")[-1]))), key=f"dbg4_{vid}")
                for att in (dbg4["scenes"].get(pick4) or {}).get("attempts", []):
                    st.markdown(f"**call {att['n']}** · usage {att.get('usage')} · errors shown {att.get('errors_shown')}")
                    st.code(att.get("scene_text", ""), language="text"); st.code(att.get("instructions", ""), language="text")
                    st.code(att.get("raw_text") or att.get("error") or "", language="json")
                    if att.get("validation"):
                        st.write({"validation": att["validation"]})
        with st.expander("raw scores JSON"):
            st.json(sc4, expanded=False)

# ---- Stage 5
from selection_v2 import stage5_critic as S5  # noqa: E402

with tabs[5]:
    EDITOR5 = str(CFG["stage5"].get("mode", "critic")) == "editor"
    if EDITOR5:
        # founder, 2026-10-30: the editor's pick replaces the cold critic as the gate (stage5.mode editor)
        from selection_v2 import stage5_editor as ED
        st.subheader("Stage 5: editor's pick (one call: full episode context + every shortlisted version as one low-res reel)")
        st.markdown("Shortlist: the top moments by their best version's Stage 4 combined, plus every situation / thread scoring high, up to 2 "
                    "versions each (closest to the channel's typical length within 0.5 of the best, and the best-scored one), plus the "
                    "episode's own channel Shorts unlabelled. The model, as this channel's editor, picks 6 posts in order and 3 alternates; code "
                    "applies the set rules and replaces a violating pick (or a channel Short) with the next alternate. Config: `pipeline.yaml → stage5.editor`.")
        F = facts_of(vid)
        try:
            sl5 = ED.shortlist(F, CFG); est5e = ED.estimate(F, CFG, sl5, P.run_dir)
        except Exception as e:
            show_exception(e); sl5, est5e = None, None
        if sl5:
            st.info(f"**≈ ₹{est5e['total_inr']:.2f}** · {est5e['n_moments']} moments, {est5e['n_versions']} versions, reel ≈ {est5e['reel_s']} s · "
                    f"typical length {sl5['typical']['target_s']:.0f} s ({sl5['typical']['source']}) · {est5e['note']}")
            st.dataframe(pd.DataFrame([{"moment": m["moment_id"], "scene": m["scene_id"], "listed because": m["why_listed"], "best S4": m["best"],
                                        "versions": "; ".join(f"{v['version_id']} {v['id']} {v['variant']} {v['final_duration']:.0f} s"
                                                              + (f" S4 {v['combined']}" if v["combined"] is not None else "") for v in m["versions"])}
                                       for m in sl5["moments"]]), width="stretch", hide_index=True, height=min(600, 40 + 36 * max(1, len(sl5["moments"]))))
            if st.button(f"Run the editor's pick (≈₹{est5e['total_inr']:.2f})", key=f"run5ed_{vid}", type="primary"):
                t0 = time.perf_counter()
                with st.spinner("Stage 5: rendering the versions, building the reel, one editor call; keep this tab open"):
                    try:
                        res = runner.run_stage(vid, "5", CFG)
                        st.session_state[f"last_5ed_{vid}"] = {"ok": True, "s": res.duration_s, "warn": res.warnings}
                    except BaseException as e:  # noqa: BLE001
                        st.session_state[f"last_5ed_{vid}"] = {"ok": False, "s": time.perf_counter() - t0, "err": e, "tb": traceback.format_exc()}
                st.rerun()
        last5e = st.session_state.get(f"last_5ed_{vid}")
        if last5e:
            if last5e["ok"]:
                st.success(f"Stage 5 finished in {last5e['s']:.0f} s")
                for w in last5e.get("warn") or []:
                    st.warning(w)
            else:
                st.error(f"Stage 5 FAILED after {last5e['s']:.0f} s"); show_exception(last5e["err"], last5e["tb"])
        F = facts_of(vid); ed5 = F.get("editor") or {}
        if ed5.get("posts"):
            st.markdown(f"### The editor's posts ({len(ed5['posts'])}) · ₹{(ed5.get('usage') or {}).get('cost_inr', 0):.2f} · {ed5.get('generated_at')}")
            cands5 = {c["id"]: c for c in F["candidates"]}
            st.dataframe(pd.DataFrame([{"post": i + 1, "editor rank": p.get("editor_rank"), "from": p["source"], "moment": p["moment_id"], "version": p["version_id"],
                                        "clip": p["candidate_id"], "variant": cands5[p["candidate_id"]]["variant"], "len_s": round(cands5[p["candidate_id"]]["final_duration"], 1),
                                        "span": f"{cands5[p['candidate_id']]['start_str']}-{cands5[p['candidate_id']]['end_str']}", "why": p["why"],
                                        "hook seen": (p.get("hook") or {}).get("seen"), "hook heard": (p.get("hook") or {}).get("heard"),
                                        "newcomer needs": p.get("newcomer_needs"), "fan": p["fan_clip"], "cut fix": p.get("cut_fix")}
                                       for i, p in enumerate(ed5["posts"])]), width="stretch", hide_index=True)
            if ed5.get("replaced"):
                st.warning("Replaced by the set rules: " + "; ".join(f"rank {x['rank']} {x['moment_id']} ({x['rule']}) → {x.get('replaced_by') or 'nothing left'}" for x in ed5["replaced"]))
            if ed5.get("channel_short_picks"):
                st.info("The editor picked the channel's own Short(s) (never shipped): " + ", ".join(f"{x['moment_id']} {x['candidate_id']}" for x in ed5["channel_short_picks"]))
            if ed5.get("unused_alternates"):
                st.caption("Unused alternates: " + "; ".join(f"{a['moment_id']} {a['candidate_id']}: {a['why']}" for a in ed5["unused_alternates"]))
    if not EDITOR5:
        st.subheader("Stage 5: cold-viewer critic (fresh Gemini per clip, no context)")
        st.markdown("The first time an actual clip exists. Each selected candidate is rendered as the real cut (Stage 3 segments, hard cuts, "
                "480p) and shown to a fresh call that has NO transcript, beat sheet, scores or prefix: it answers eight open questions as a "
                "Telugu viewer scrolling Shorts. Three independent runs per clip; code compares the answers to what Stages 2–4 expected. "
                "Thresholds: `pipeline.yaml → stage5`.")
    F = facts_of(vid)
    try:
        run4_active = S5.check_inputs(F); ok5 = True
    except Exception as e:
        ok5 = False
        st.warning(f"Stage 5 refuses to run: {e}")
    if ok5 and not EDITOR5:
        try:
            sel5 = S5.plan(F, CFG); est5 = S5.estimate(F, CFG, sel5)
        except Exception as e:
            show_exception(e); sel5, est5 = [], None
        cr = F.get("critic") or {}
        done5 = {z["id"]: z for z in (cr.get("clips") or [])} if cr.get("run_key") == run4_active["run_key"] else {}
        if cr and cr.get("run_key") != run4_active["run_key"]:
            st.warning("the critic results on disk belong to another Stage 4 run; this selection starts fresh")
        st.markdown(f"### Clip list ({len(sel5)} clips from the active Stage 4 run {run4_active['run_key']}; shown before running)")
        if est5:
            st.info(f"**total ≈ ₹{est5['total_inr']:.2f}** for {est5['n_run']} clips to run × {est5['runs_per_clip']} runs; {est5['n_carry']} carried (same cut as a stored record, "
                    f"re-compared with the current rules, ₹0) · {est5['note']}")
            cands5 = {c["id"]: c for c in F["candidates"]}
            prev5 = {z["id"]: z for z in (cr.get("clips") or [])}
            ldf = pd.DataFrame([{"id": r["id"], "role": r["role"], "scene": cands5[r["id"]]["scene_id"], "type": r["type"], "variant": r["variant"],
                                 "span": f"{cands5[r['id']]['start_str']}-{cands5[r['id']]['end_str']}", "len_s": round(r["final_duration"], 1),
                                 "stage4": r["combined"], "action": r.get("action"), "est_inr": r["inr"],
                                 "status": (done5[r["id"]]["verdict"] + f" {done5[r['id']]['n_pass']}/{done5[r['id']]['n_runs']}" + (" (carried)" if done5[r["id"]].get("carried_from") else "")) if r["id"] in done5
                                 else (f"stored as {r['carry_from']}: " + prev5[r["carry_from"]]["verdict"] + f" {prev5[r['carry_from']]['n_pass']}/{prev5[r['carry_from']]['n_runs']} (old rules)" if r.get("carry_from") in prev5 else "not run")}
                                for r in est5["clips"]])
            st.dataframe(ldf, width="stretch", hide_index=True, height=min(600, 40 + 36 * max(1, len(ldf))))
        ids5 = [r["id"] for r in sel5]
        default5 = ids5[0] if ids5 else None
        c1, c2, c3 = st.columns([2, 1, 1])
        one5 = c1.selectbox("clip to run", ids5 or [None], index=(ids5.index(default5) if default5 in ids5 else 0), key=f"pick5_{vid}")
        e5 = next((r for r in (est5 or {}).get("clips", []) if r["id"] == one5), {})
        remaining5 = [i for i in ids5 if i not in done5 and next((r.get("action") for r in sel5 if r["id"] == i), "run") == "run"]
        carry5 = [i for i in ids5 if i not in done5 and next((r.get("action") for r in sel5 if r["id"] == i), None) == "carry"]
        if carry5 and not remaining5:
            st.caption(f"{len(carry5)} clip(s) will be carried and re-compared when the next run happens (a run of one clip or an extra id is enough)")
        def _run5(ids, label):
            t0 = time.perf_counter()
            with st.spinner(f"Stage 5: {label} (render, upload, 3 critic calls per clip); keep this tab open"):
                try:
                    res = runner.run_stage(vid, "5", CFG, clip_ids=ids)
                    st.session_state[f"last_5_{vid}"] = {"ok": True, "s": res.duration_s, "warn": res.warnings}
                except BaseException as e:  # noqa: BLE001
                    st.session_state[f"last_5_{vid}"] = {"ok": False, "s": time.perf_counter() - t0, "err": e, "tb": traceback.format_exc()}
            st.rerun()
        if c2.button(f"Run one clip ({one5}, ≈₹{e5.get('inr', 0):.2f})", key=f"run5one_{vid}", type="primary", disabled=not one5):
            _run5([one5], one5)
        extra5 = st.text_input("extra clip ids outside the selection (passing Stage 4 candidates, comma-separated, e.g. an acceptance case)", key=f"extra5_{vid}")
        if extra5.strip() and st.button("Run these extra clips", key=f"run5extra_{vid}"):
            _run5([x.strip() for x in extra5.split(",") if x.strip()], f"extra {extra5}")
        rem5 = sum(r["inr"] for r in (est5 or {}).get("clips", []) if r["id"] in remaining5)
        extra_ids5 = [x.strip() for x in extra5.split(",") if x.strip()]
        if c3.button(f"Run all remaining ({len(remaining5)} clips, ≈₹{rem5:.2f})" + (f" + extra {', '.join(extra_ids5)}" if extra_ids5 else ""), key=f"run5all_{vid}",
                     disabled=not (remaining5 or extra_ids5)):
            # 2026-10-28: the extra ids typed above are included here too (the founder typed C9a and pressed this button; it was not run)
            _run5(remaining5 + [i for i in extra_ids5 if i not in remaining5] if extra_ids5 else None, f"{len(remaining5)} clips" + (f" + {extra_ids5}" if extra_ids5 else ""))
        # founder, 2026-10-29: re-compare every stored answer (selection + archive) with the current rules, ₹0
        if (F.get("critic") or {}).get("clips") and st.button("Re-compare stored answers with the current rules (₹0, no calls)", key=f"recmp5_{vid}"):
            try:
                from selection_v2.paths import write_json
                F2, flips5 = S5.recompare_all(F, CFG)
                runner.validate_facts(F2); write_json(P.facts, F2)
                st.session_state[f"flips5_{vid}"] = flips5
                st.rerun()
            except Exception as e:
                show_exception(e)
        if st.session_state.get(f"flips5_{vid}") is not None:
            fl = st.session_state[f"flips5_{vid}"]
            st.info(f"re-compared: {len(fl)} verdict flip(s)" if fl else "re-compared: no verdict changed")
            if fl:
                st.dataframe(pd.DataFrame([{k: v for k, v in x.items() if k != "found_by_text"} | {"found_by_text": "; ".join(t for t in x["found_by_text"] if t)} for x in fl]),
                             width="stretch", hide_index=True)
        last5 = st.session_state.get(f"last_5_{vid}")
        if last5:
            if last5["ok"]:
                st.success(f"Stage 5 finished in {last5['s']:.0f} s")
                for w in last5.get("warn") or []:
                    st.warning(w)
            else:
                st.error(f"Stage 5 FAILED after {last5['s']:.0f} s"); show_exception(last5["err"], last5["tb"])
        F = facts_of(vid); cr = F.get("critic") or {}
        done5 = {z["id"]: z for z in (cr.get("clips") or [])} if cr.get("run_key") == run4_active["run_key"] else {}
        if done5:
            st.success(f"{len(done5)} clips judged · verdicts {cr.get('verdicts')} · ₹{(cr.get('usage') or {}).get('cost_inr', 0):.2f} · {cr.get('generated_at')}")
            ref5 = acceptance.load_acceptance(vid, "5")
            if ref5:
                st.markdown("### Acceptance tests")
                rws = acceptance.evaluate_1b(F, ref5)
                st.write(acceptance.summary(rws))
                for r in rws:
                    icon = {"agree": "🟢", "disagree": "🔴", "missing": "⚪", "error": "⚠️"}[r["status"]]
                    st.write(f"{icon} **{r['expect']}** — {r['got']}")
            st.markdown("### Per clip (pick one)")
            pick5 = st.selectbox("judged clip", list(done5), key=f"judged5_{vid}")
            z = done5[pick5]; c = cands5[z["id"]]
            st.markdown(f"**{z['id']}** · {z['type']} / {z['variant']} · {c['start_str']}–{c['end_str']} · {z['final_duration']:.1f} s · "
                        f"verdict **{z['verdict']}** ({z['n_pass']}/{z['n_runs']}) · Stage 4 combined {z['stage4_combined']} · ₹{z['usage']['cost_inr']:.2f}"
                        + (f" · carried from {z['carried_from']} (answers kept, re-compared)" if z.get("carried_from") else ""))
            ccx = z.get("caption_context") or {}
            if ccx:
                st.markdown(("⚠️ **needs caption context**" if ccx.get("needs_caption_context") else "framing ok") + f" · {ccx.get('runs_disagree', 0)}/{z['n_runs']} runs' Q2 framing disagrees with the setup "
                            f"(heuristic: no shared content word, or 'did not understand') · setup: _{ccx.get('setup', '')[:160]}_")
                for i, fr in enumerate(ccx.get("framings") or []):
                    st.caption(f"run {i + 1}: {'disagrees' if fr['disagrees'] else 'agrees'} (shared: {', '.join(fr['shared']) or 'none'}; understood {fr['understood']}) — {fr['thought_it_was'][:140]}")
            cv, ct = st.columns([1, 1])
            if Path(z["clip_path"]).exists():
                cv.video(z["clip_path"])
            else:
                cv.caption(f"clip file missing: {z['clip_path']}")
            import altair as alt
            exp = z["expected"]; L = z["final_duration"]
            tl = pd.DataFrame([{"x0": 0.0, "x1": L, "y": "clip", "label": "clip"}] +
                              ([{"x0": exp["clip_start"], "x1": exp["clip_end"], "y": "expected", "label": f"expected {exp.get('line')} {exp.get('clip_start')}-{exp.get('clip_end')} s"}] if exp.get("clip_start") is not None else []) +
                              [{"x0": max(0.0, float(t) - 0.4), "x1": float(t) + 0.4, "y": f"run {i + 1}", "label": f"run {i + 1} located {t:.0f} s"} for i, t in enumerate(z["located_s"])] +
                              [{"x0": float(s["from_s"]), "x1": float(s["to_s"]), "y": f"swipe r{i + 1}", "label": f"swipe {s['from_s']}-{s['to_s']}: {s['why'][:40]}"} for i, sw in enumerate(z["swipe"]) for s in sw])
            ch = alt.Chart(tl).mark_bar(height=10).encode(x=alt.X("x0:Q", title="clip seconds", scale=alt.Scale(domain=[0, L])), x2="x1:Q", y=alt.Y("y:N", title=None),
                                                           color=alt.Color("y:N", legend=None), tooltip=["label:N"]).properties(height=180, width="container")
            ct.altair_chart(ch, key=f"tl5_{vid}_{z['id']}")
            cols = st.columns(z["n_runs"])
            for i, run in enumerate(z["runs"]):
                a = run["answers"]
                with cols[i]:
                    st.markdown(f"**run {run['run']}: {'pass' if run['pass'] else 'fail'}**" + (f" — {'; '.join(run['reasons'])}" if run["reasons"] else ""))
                    st.write(f"1. {a['what_is_happening']}")
                    st.write(f"2. {'understood' if a['first_3s']['understood'] else 'not understood'}: {a['first_3s']['thought_it_was']}")
                    st.write(f"3. {a['best_moment']['second']:.0f} s ({a['best_moment']['kind']}): {a['best_moment']['what']}")
                    st.write("4. " + ("; ".join(f"{s['from_s']}-{s['to_s']} s: {s['why']}" for s in a["swipe"]) if a["swipe"] else "no swipe"))
                    st.write(f"5. {'yes' if a['want_next']['yes'] else 'no'}: {a['want_next']['why']}")
                    st.write(f"6. {'yes' if a['want_more_people']['yes'] else 'no'}: {a['want_more_people']['why']}")
                    st.write(f"7. {a['confusion']['missing']}: {a['confusion']['what']}")
                    st.write(f"8. “{a['caption']}”")
            st.markdown("### Two versions per family (trimmed vs untrimmed)")
            fams = {}
            for zz in done5.values():
                fams.setdefault(zz["family"], []).append(zz)
            for fam, members in fams.items():
                if len(members) < 2:
                    continue
                cc = st.columns(len(members))
                for col, zz in zip(cc, sorted(members, key=lambda q: q["role"] != "twin")):
                    col.markdown(f"**{zz['id']}** {zz['variant']} · {zz['final_duration']:.0f} s · **{zz['verdict']}** {zz['n_pass']}/{zz['n_runs']} · Stage 4 {zz['stage4_combined']}")
                    col.caption("captions: " + " | ".join(zz["captions"]))
            with st.expander("exact prompt and raw JSON per run"):
                dbg_path = P.run_dir / "stage5_debug.json"
                if dbg_path.exists():
                    dbg5 = json.loads(dbg_path.read_text(encoding="utf-8"))
                    st.markdown("**system (persona)**"); st.code(dbg5.get("system", ""), language="text")
                    pick5d = st.selectbox("clip", sorted(dbg5.get("clips", {})), key=f"dbg5_{vid}")
                    for att in (dbg5["clips"].get(pick5d) or {}).get("attempts", []):
                        st.markdown(f"**run {att.get('run')} call {att['n']}** · usage {att.get('usage')} · errors shown {att.get('errors_shown')}")
                        st.code(att.get("prompt", ""), language="text"); st.code(att.get("raw_text") or att.get("error") or "", language="json")
            with st.expander("raw critic JSON"):
                st.json(cr, expanded=False)

# ---- Stage 6
from selection_v2 import stage6_select as S6  # noqa: E402

with tabs[6]:
    st.subheader("Stage 6: final selection (code picks the set, one text call writes the words)")
    st.markdown("Per moment (scene + payoff line + kind) the primary is the **shortest PASS** version (a BORDERLINE-only moment ships its shortest "
                "BORDERLINE); alternates are the other PASS / BORDERLINE versions more than 5 s apart; a FAIL is never shipped. Moments are ranked by "
                "the critics' strength (3/3 > 2/3 > borderline), then Stage 4 combined; the set rules and backfill are in `pipeline.yaml → stage6`.")
    F = facts_of(vid)
    try:
        S6.check_inputs(F)
        ok6 = True
    except Exception as e:
        ok6 = False
        st.warning(f"Stage 6 refuses to run: {e}")
    if ok6:
        try:
            plan6 = S6.plan_set(F, CFG)
            est6 = S6.estimate(F, CFG, plan6["main"] + plan6["bonus"], run_dir=P.run_dir)
        except Exception as e:
            show_exception(e); plan6, est6 = None, None
        if plan6:
            st.markdown(f"### Plan (code only, ₹0): {len(plan6['main'])} main, {len(plan6['bonus'])} bonus loops")
            for rq in plan6["requirements"]:
                (st.success if rq["ok"] else st.error)(f"{'✓' if rq['ok'] else '✗'} {rq['name']}")
            for w in plan6["warnings"]:
                st.warning(w)
            st.dataframe(pd.DataFrame([{"id": m["id"], "scene": m["scene_id"], "kind": m["kind"], "primary": m["primary"]["id"], "variant": m["primary"]["variant"],
                                        "len_s": round(m["primary"]["final_duration"]), "critic": f"{m['primary']['verdict']} {m['primary']['n_pass']}/{m['primary']['n_runs']}",
                                        "stage4": m["combined"], "series_pull": m["series_pull"], "fan": m["fan_clip"], "recurring": m["recurring"],
                                        "alternates": ", ".join(f"{a['id']} {a['final_duration']:.0f}s {a['verdict']}" for a in m["alternates"]), "label": m["label"]}
                                       for m in plan6["main"] + plan6["bonus"]]), width="stretch", hide_index=True)
            if est6:
                st.info(f"text call ≈ **₹{est6['total_inr']:.2f}** (₹{est6['total_inr_if_cache_hits']:.2f} if the prefix cache hits) · {est6['n_moments']} moments · "
                        f"prefix {est6['prefix_tokens']:,} + prompt {est6['prompt_tokens']:,} tokens · {est6['note']}")
            if st.button(f"Run Stage 6 (text call ≈ ₹{(est6 or {}).get('total_inr', 0):.2f}, then package)", key=f"run6_{vid}", type="primary"):
                t0 = time.perf_counter()
                with st.spinner("Stage 6: text call, renders for alternates not rendered yet, package; keep this tab open"):
                    try:
                        res = runner.run_stage(vid, "6", CFG)
                        st.session_state[f"last_6_{vid}"] = {"ok": True, "s": res.duration_s, "warn": res.warnings}
                    except BaseException as e:  # noqa: BLE001
                        st.session_state[f"last_6_{vid}"] = {"ok": False, "s": time.perf_counter() - t0, "err": e, "tb": traceback.format_exc()}
                st.rerun()
    last6 = st.session_state.get(f"last_6_{vid}")
    if last6:
        if last6["ok"]:
            st.success(f"Stage 6 finished in {last6['s']:.0f} s")
            for w in last6.get("warn") or []:
                st.warning(w)
        else:
            st.error(f"Stage 6 FAILED after {last6['s']:.0f} s"); show_exception(last6["err"], last6["tb"])
    F = facts_of(vid)
    fin = (F or {}).get("final")
    if fin:
        sp = S6.episode_spend(CFG, vid)
        st.markdown(f"### Posting set · {fin['generated_at']} · episode spend so far **₹{sp['total_inr']:.2f}** across all stages")
        st.caption("by stage (ledger deltas from the runs log): " + ", ".join(f"{k} ₹{v:.2f}" for k, v in sorted(sp["by_stage"].items())))
        page = Path(fin["package"]["review_html"])
        if page.exists():
            st.download_button(f"Download review.html ({fin['package'].get('review_html_mb', '?')} MB, self-contained)", page.read_bytes(), file_name=f"{vid}_review.html", mime="text/html")
        for grp, title in (("main", "Main posts"), ("bonus", "Bonus loops")):
            st.markdown(f"#### {title}")
            cols = st.columns(3)
            for i, m in enumerate(fin[grp]):
                with cols[i % 3]:
                    p6 = m["primary"]
                    st.markdown(f"**{m['id']}** · {m['kind']} · {p6['type']}" + (" · fan clip" if m.get("fan_clip") else "") + (" · ⚠️ needs caption context" if m.get("needs_caption_context") else ""))
                    clip = Path(fin["package"]["dir"]) / (p6.get("file") or "")
                    if clip.is_file():
                        st.video(str(clip))
                    st.markdown(f"**{m.get('hook_text', '')}**")
                    st.caption("👥 " + (m.get("people_line") or "(people line appears after the next Stage 6 run or re-generate)"))
                    if m.get("people_in_text"):
                        st.caption("text refers to: " + "; ".join(f"{x['name']} ({x['relationship']})" for x in m["people_in_text"]))
                    st.write(m.get("caption", ""))
                    st.caption(f"{m.get('title', '')} · {' '.join(m.get('hashtags') or [])} · CTA {m.get('cta_target')}")
                    if m.get("context_line"):
                        st.caption(f"context line: {m['context_line']}")
                    st.caption(f"{p6['id']} {p6['variant']} {p6['final_duration']:.0f} s · critic {p6['verdict']} {p6['n_pass']}/{p6['n_runs']} located {p6['located_s']} · "
                               f"Stage 4 {p6['stage4']['combined']} · series_pull {m.get('series_pull')} · Q9 vs dynamic: {m.get('q9_vs_dynamic')} ({m.get('q9_note', '')})")
                    if m.get("alternates") and st.toggle(f"alternates ({len(m['alternates'])})", key=f"alt6_{vid}_{m['id']}"):
                        for a6 in m["alternates"]:
                            st.caption(f"{a6['id']} {a6['variant']} {a6['final_duration']:.0f} s · {a6['verdict']} {a6['n_pass']}/{a6['n_runs']}")
                            ac = Path(fin["package"]["dir"]) / (a6.get("file") or "")
                            if ac.is_file():
                                st.video(str(ac))
        # founder, 2026-10-29: creator's picks (top rejected moments by Stage 4), marked promote so the creator can override
        st.markdown("### Creator's picks (not in the set; promote to override)")
        picks6 = fin.get("creator_picks") or []
        if not picks6:
            st.caption("none (run Stage 6 again to compute them)")
        pcols = st.columns(3)
        for i, pk in enumerate(picks6):
            with pcols[i % 3]:
                v6 = pk["variant"]
                st.markdown(f"**Scene {pk['scene_id']}** · {pk['kind']} · {v6['id']} {v6['variant']} {v6['final_duration']:.0f} s · critic {v6['verdict']} "
                            f"{v6['n_pass']}/{v6['n_runs']} · Stage 4 {pk['combined']}")
                pc = Path(fin["package"]["dir"]) / (v6.get("file") or "")
                if pc.is_file():
                    st.video(str(pc))
                st.caption(f"out because: {pk['reason']}")
                for x in pk.get("critic_story") or []:
                    st.caption("· " + x)
                prom = st.toggle("promote", value=bool(pk.get("promote")), key=f"promote6_{vid}_{pk['key']}")
                if prom != bool(pk.get("promote")):
                    from selection_v2.paths import write_json
                    F2 = facts_of(vid); F2["final"]["creator_picks"][i]["promote"] = prom
                    runner.validate_facts(F2); write_json(P.facts, F2)
                    fj = Path(F2["final"]["package"]["final_json"])
                    if fj.exists():
                        fjd = json.loads(fj.read_text(encoding="utf-8")); fjd["creator_picks"] = F2["final"]["creator_picks"]; write_json(fj, fjd)
                    st.rerun()
        # founder, 2026-10-29: re-generate the words of chosen moments only (one text call; clips and set unchanged)
        ids6 = [m["id"] for m in fin["main"] + fin["bonus"]]
        bad6 = [m["id"] for m in fin["main"] + fin["bonus"] if S6.hook_quotes_clip(m.get("hook_text") or "", F, m["primary"])]
        if bad6:
            st.warning(f"hook_text quotes a line from its own clip: {', '.join(bad6)} (rule of 2026-10-29); re-generate below")
        rg = st.multiselect("re-generate the words of these moments (one text call)", ids6, default=bad6, key=f"regen6_{vid}")
        if rg and st.button(f"Re-generate words for {', '.join(rg)}", key=f"regen6btn_{vid}"):
            t0 = time.perf_counter()
            with st.spinner("one text call; the package is rewritten"):
                try:
                    res = runner.run_substage(vid, "6:words", S6.regenerate, CFG, moment_ids=rg)
                    st.session_state[f"last_6_{vid}"] = {"ok": True, "s": res.duration_s, "warn": res.warnings}
                except BaseException as e:  # noqa: BLE001
                    st.session_state[f"last_6_{vid}"] = {"ok": False, "s": time.perf_counter() - t0, "err": e, "tb": traceback.format_exc()}
            st.rerun()
        st.markdown("### Rejection report")
        with st.expander("rejection_report.md", expanded=False):
            st.markdown(fin.get("rejection_report_md") or "")
        st.markdown("### Acceptance tests")
        ref6 = acceptance.load_acceptance(vid, "6")
        if ref6:
            rows6 = acceptance.evaluate_1b(F, ref6)
            st.dataframe(pd.DataFrame(rows6), width="stretch", hide_index=True)
        else:
            st.caption("no reference/acceptance file for this episode and stage")
        dbg6p = P.run_dir / "stage6_debug.json"
        if dbg6p.exists():
            with st.expander("text call: prompt and raw JSON"):
                dbg6 = json.loads(dbg6p.read_text(encoding="utf-8"))
                st.markdown("**system**"); st.code(dbg6.get("system", ""), language="text")
                for att in dbg6.get("attempts") or []:
                    st.markdown(f"**call {att['n']}** · usage {att.get('usage')} · validation {att.get('validation')}")
                    st.code(att.get("prompt", ""), language="text"); st.code(att.get("raw_text") or att.get("error") or "", language="json")

# ---- Tags
with tabs[len(runner.STAGES)]:
    st.subheader("Every Scribe tag across scribe_dataset/ and the class it maps to")
    st.caption("classes and patterns: selection_v2/config/pipeline.yaml → tags (first class in `order` with a substring hit wins)")
    try:
        rows = tag_tally(CFG)
        tdf = pd.DataFrame(rows)
        only = st.multiselect("filter classes", sorted(tdf["class"].unique()), default=[], key="tag_filter")
        if only:
            tdf = tdf[tdf["class"].isin(only)]
        st.write(f"{len(rows)} distinct tags; by class: {tdf['class'].value_counts().to_dict()}")
        st.dataframe(tdf, width="stretch", hide_index=True, height=700)
    except Exception as e:
        show_exception(e)

# ---- Eval
with tabs[len(runner.STAGES) + 1]:
    st.subheader("Golden set")
    st.info("Recall is computed from Stage 3 onwards (candidates) and Stage 6 (final set). For now this tab only lists the "
            "known-good moments per episode: channel Shorts, founder-approved clips, hand-entered heatmap peaks.")
    st.caption(f"{golden_set.DEFAULT_PATH} · rebuild from disk: python -m selection_v2.eval.import_sources --all")
    try:
        gs = golden_set.load()
        ep = gs["episodes"].get(vid)
        has_gold, has_shorts = P.gold.exists(), P.channel_shorts_data.exists()
        if not ep:
            if has_gold or has_shorts:
                st.info(f"no entry for {vid} in {golden_set.DEFAULT_PATH.name} yet, but "
                        f"{'data/gold/' + vid + '.json' if has_gold else ''}{' and ' if has_gold and has_shorts else ''}"
                        f"{'data/channel_shorts/' + vid + '.json' if has_shorts else ''} exist on disk: press Import from disk.")
            else:
                st.info(f"no entry for {vid} in {golden_set.DEFAULT_PATH.name}: expected, there is no data/gold/{vid}.json and no "
                        f"data/channel_shorts/{vid}.json to import from. Add moments by hand (see the file's docstring format).")
        if has_gold or has_shorts:
            if st.button("Import from disk" if not ep else "Re-import from disk", key=f"gs_import_{vid}"):
                try:
                    from selection_v2.eval.import_sources import import_episode
                    ep = import_episode(vid, gs, CFG)
                    errs = golden_set.validate(gs)
                    if errs:
                        raise ValueError("; ".join(errs))
                    golden_set.save(gs)
                    st.success(f"imported {len(ep['moments'])} moments ({sum(m['tier'] == 'gold' for m in ep['moments'])} gold) "
                               f"from {ep['n_sources']} into {golden_set.DEFAULT_PATH}")
                except Exception as e:
                    show_exception(e)
        if ep:
            st.write({"sources": ep.get("n_sources"), "moments": len(ep["moments"]),
                      "gold": sum(m["tier"] == "gold" for m in ep["moments"]),
                      "silver": sum(m["tier"] == "silver" for m in ep["moments"])})
            mdf = pd.DataFrame([{"id": m["id"], "tier": m["tier"], "type": m["type"], "start": fmt_ts(m["start"]),
                                 "end": fmt_ts(m["end"]), "len_s": round(m["end"] - m["start"], 1),
                                 "sources": ", ".join(f"{s['kind']}" + (f"({s.get('confidence')})" if s.get("confidence") else "")
                                                      + (f" {s.get('views'):,}v" if s.get("views") else "") for s in m["sources"]),
                                 "label": m["label"][:60]} for m in ep["moments"]])
            sel = selected_rows(mdf, f"gold_{vid}", height=360)
            F = facts_of(vid)
            if sel and F:
                m = ep["moments"][sel[0]]
                st.markdown(f"**transcript inside {m['id']} ({fmt_ts(m['start'])}–{fmt_ts(m['end'])})**")
                st.code("\n".join(line_fmt(r) for r in F["lines"] if r["end"] > m["start"] - 2 and r["start"] < m["end"] + 2),
                        language="text")
            rep = recall.report(vid, facts_of(vid), gs)
            if rep["n_candidates"]:
                st.markdown("**recall of golden-set moments inside the Stage 3 candidates**")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("gold recall", f"{rep['recall_candidates_gold']}" if rep["recall_candidates_gold"] is not None else "n/a")
                m2.metric("silver recall", f"{rep['recall_candidates_silver']}" if rep["recall_candidates_silver"] is not None else "n/a")
                m3.metric("all", f"{rep['recall_candidates_all']}" if rep["recall_candidates_all"] is not None else "n/a")
                m4.metric("mean boundary IoU", f"{rep['mean_iou_candidates']}" if rep["mean_iou_candidates"] is not None else "n/a")
            else:
                st.markdown("**recall report** (needs Stage 3 candidates; run Stage 3)")
            st.code(recall.format_report(rep), language="text")
    except Exception as e:
        show_exception(e)

# ---- Runs log
with tabs[len(runner.STAGES) + 2]:
    st.subheader("runs.csv")
    if P.runs_log.exists():
        try:
            ldf = pd.read_csv(P.runs_log)
            st.dataframe(ldf.iloc[::-1], width="stretch", hide_index=True, height=500)
            st.download_button("download runs.csv", P.runs_log.read_bytes(), "runs.csv", key="dl_runs")
        except Exception as e:
            show_exception(e)
    else:
        st.info(f"no runs yet ({P.runs_log})")
