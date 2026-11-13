"""
Build-time model bake: download the IndicXlit checkpoint + rescoring dicts
into the image and prove the engine actually produces Telugu, so `docker
compose up` never does a cold multi-hundred-MB download and a broken install
fails the BUILD, not the first user request.
"""

from engine import load_engine

engine = load_engine("te")
suggestions = engine.translit_word("kalpavruksham", lang_code="te", topk=5)
assert suggestions and any(s.strip() for s in suggestions), (
    "IndicXlit preload produced no suggestions — model bake failed"
)
print("preload OK:", suggestions)
