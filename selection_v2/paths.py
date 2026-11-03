"""Where everything lives, reusing ``shotvi.run_episode.Paths`` for the inputs (video, transcript, shots, asr_fix,
speaker_fix) and adding the v2 outputs under ``runs/selection_v2/``."""
from __future__ import annotations

import json
from pathlib import Path

from shotvi.run_episode import Paths as _InputPaths
from shotvi.run_episode import ROOT

SCRIBE_DIR = ROOT / "scribe_dataset"


class EpisodePaths(_InputPaths):
    """``EpisodePaths(vid, cfg)``: the existing input paths plus ``facts`` (episode_facts.json) and ``run_dir``."""

    def __init__(self, vid: str, cfg: dict):
        super().__init__(vid, ROOT / cfg["paths"]["runs_dir"] / vid)
        self.cfg = cfg

    @property
    def run_dir(self) -> Path:
        return self.out

    @property
    def facts(self) -> Path:
        return self.out / "episode_facts.json"

    @property
    def runs_log(self) -> Path:
        return ROOT / self.cfg["paths"]["runs_log"]

    @property
    def channel_shorts_dir(self) -> Path:
        return self.src / "channel_shorts"

    @property
    def channel_shorts_data(self) -> Path:
        return ROOT / "data" / "channel_shorts" / f"{self.vid}.json"

    @property
    def gold(self) -> Path:
        return ROOT / "data" / "gold" / f"{self.vid}.json"

    def title(self) -> str:
        if self.meta.exists():
            try:
                return json.loads(self.meta.read_text(encoding="utf-8")).get("title") or ""
            except (OSError, ValueError):
                return ""
        return ""

    def status(self) -> dict:
        """Which inputs and stage outputs exist on disk (for the sidebar)."""
        facts = load_json(self.facts) if self.facts.exists() else None
        stages = sorted((facts or {}).get("stages", {}).keys())
        return {
            "video": self.mp4.exists(), "audio": self.wav.exists(), "scribe": self.transcript.exists(),
            "youtube_meta": self.meta.exists(), "asr_fix": self.corrections.exists(),
            "speaker_fix": self.speakers.exists(), "facts": self.facts.exists(), "stages_done": stages,
        }


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, obj: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def episodes_on_disk() -> list[str]:
    """Video ids under scribe_dataset/ that have a Scribe transcript."""
    out = []
    if not SCRIBE_DIR.exists():
        return out
    for d in sorted(SCRIBE_DIR.iterdir()):
        if d.is_dir() and (d / f"{d.name}_scribe_transcript.json").exists():
            out.append(d.name)
    return out
