"""Who is calling: the Supabase sign-in token (``Authorization: Bearer <access token>``) checked against the
project's public signing keys (JWKS, ES256), so the API needs no secret to verify logins."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException

from .db import ROOT


@dataclass
class User:
    id: str
    email: str | None


def _env(name: str) -> str:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"{name} is not set (.env)")
    return v


@lru_cache(maxsize=1)
def _jwks() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{_env('SUPABASE_URL').rstrip('/')}/auth/v1/.well-known/jwks.json", cache_keys=True,
                           lifespan=3600)


def issuer() -> str:
    return f"{_env('SUPABASE_URL').rstrip('/')}/auth/v1"


def verify(token: str, keys=None) -> User:
    """The signed-in user, or ``HTTPException(401)``. ``keys`` (a PyJWKClient-like object) is for tests."""
    try:
        key = (keys or _jwks()).get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["ES256", "RS256"], audience="authenticated", issuer=issuer(),
                            options={"require": ["exp", "sub"]})
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Your session has expired. Sign in again.")
    except (jwt.InvalidTokenError, jwt.PyJWKClientError) as e:
        raise HTTPException(401, f"Sign in to continue. ({type(e).__name__})")
    if claims.get("role") != "authenticated" or claims.get("is_anonymous"):
        raise HTTPException(401, "Sign in to continue.")
    return User(id=claims["sub"], email=claims.get("email"))


def current_user(authorization: str = Header(default="")) -> User:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in to continue.")
    return verify(authorization.split(" ", 1)[1].strip())
