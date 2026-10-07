"""Versioned prompt files in ``prompts/<name>.md``.

File format::

    ---
    name: discover
    version: 3
    ---
    ### SYSTEM
    ...
    ### USER
    ... $placeholders (string.Template; write $$ for a literal dollar)

Every prompt change must bump ``version``. The run records ``<version>+<sha8>`` so an unbumped edit is still visible.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from string import Template

PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    system: str
    user: str
    sha8: str
    meta: dict = field(default_factory=dict)   # other front-matter keys (e.g. answer_fields: extra answer fields)

    @property
    def tag(self) -> str:
        return f"{self.version}+{self.sha8}"

    def render(self, **kw) -> str:
        return Template(self.user).substitute(**kw)


_cache: dict[str, Prompt] = {}


def load_prompt(name: str) -> Prompt:
    if name in _cache:
        return _cache[name]
    raw = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n(.*)", raw, re.S)
    if not m:
        raise ValueError(f"prompt {name}: missing front matter")
    meta = dict(line.split(":", 1) for line in m.group(1).strip().splitlines())
    meta = {k.strip(): v.strip() for k, v in meta.items()}
    body = m.group(2)
    sm = re.search(r"### SYSTEM\n(.*?)\n### USER\n(.*)", body, re.S)
    if not sm:
        raise ValueError(f"prompt {name}: needs '### SYSTEM' and '### USER' sections")
    p = Prompt(meta["name"], meta["version"], sm.group(1).strip(), sm.group(2).strip(),
               hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8],
               {k: v for k, v in meta.items() if k not in ("name", "version")})
    _cache[name] = p
    return p
