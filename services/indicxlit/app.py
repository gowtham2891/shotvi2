"""
IndicXlit transliteration microservice (isolated container).

Speaks the exact same contract as the main app's POST /transliterate:
    { "text": "kalpavruksham", "lang": "te" }  →  { "suggestions": ["కల్పవృక్షం", ...] }
The main app proxies to this service and falls back to [] if it's down;
nothing in the frontend knows this container exists.

The model is loaded ONCE at import (module level, before uvicorn accepts
traffic) so every request is a warm ~100ms inference, never a cold load.
"""

from fastapi import FastAPI
from pydantic import BaseModel

from engine import ENGINE_LOCK, load_engine

TOPK = 5

ENGINE = load_engine("te")

app = FastAPI(title="IndicXlit transliteration service", version="1.0.0")


class TransliterateRequest(BaseModel):
    text: str
    lang: str = "te"


class TransliterateResponse(BaseModel):
    suggestions: list[str] = []


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/transliterate", response_model=TransliterateResponse)
def transliterate(payload: TransliterateRequest) -> TransliterateResponse:
    word = (payload.text or "").strip()
    if not word:
        return TransliterateResponse(suggestions=[])
    try:
        with ENGINE_LOCK:
            suggestions = ENGINE.translit_word(word, lang_code=payload.lang, topk=TOPK)
    except NotImplementedError:
        # Engine loaded only Telugu; any other lang code → empty, not 500.
        return TransliterateResponse(suggestions=[])
    return TransliterateResponse(
        suggestions=[s for s in suggestions if isinstance(s, str) and s.strip()]
    )
