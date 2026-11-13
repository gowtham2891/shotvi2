"""
IndicXlit engine loader — the ONLY place the model is constructed.

Import order is load-bearing: the torch.load monkeypatch below MUST be
installed before ai4bharat/fairseq ever touch a checkpoint. Modern torch
defaults weights_only=True, which rejects fairseq's pickled checkpoints;
forcing weights_only=False restores the pre-2.6 behavior these models need.
This container holds nothing but this model, so the relaxed load is confined
here and never leaks into the main app's environment.
"""

import threading

import torch

_original_torch_load = torch.load


def _torch_load_full(*args, **kwargs):
    kwargs["weights_only"] = False
    return _original_torch_load(*args, **kwargs)


torch.load = _torch_load_full

from ai4bharat.transliteration import XlitEngine  # noqa: E402  (patch must precede)

# fairseq inference is not guaranteed thread-safe; FastAPI runs sync handlers
# in a threadpool, so all translit calls serialize through this lock.
ENGINE_LOCK = threading.Lock()


def load_engine(lang: str = "te") -> "XlitEngine":
    """Build the en→Indic transformer engine (downloads models on first run)."""
    return XlitEngine(lang, beam_width=4, rescore=True, src_script_type="en")
