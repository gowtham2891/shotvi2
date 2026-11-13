"""backend.api + backend.auth. Offline: link parsing and token checks (a key made up here). Database (opt-in,
network, KLIPVI_DB_TESTS=1): two throwaway users, deleted afterwards."""
import os
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from backend import api as A
from backend import auth as AU

ISS = "https://example.supabase.co/auth/v1"


class Keys:
    """Stands in for PyJWKClient."""
    def __init__(self, key):
        self.key = key

    def get_signing_key_from_jwt(self, token):
        return self


@pytest.fixture
def keypair(monkeypatch):
    priv = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setattr(AU, "issuer", lambda: ISS)
    monkeypatch.setattr(AU, "_jwks", lambda: Keys(priv.public_key()))
    return priv


def token(priv, sub=None, **over):
    claims = {"sub": sub or str(uuid.uuid4()), "email": "t@example.com", "aud": "authenticated", "role": "authenticated",
              "iss": ISS, "exp": int(time.time()) + 600, **over}
    return jwt.encode(claims, priv, algorithm="ES256")


def test_youtube_links():
    for u in ("https://www.youtube.com/watch?v=QWIvgoTQGKk&t=10", "youtu.be/QWIvgoTQGKk", "https://m.youtube.com/watch?v=QWIvgoTQGKk",
              "https://youtube.com/shorts/QWIvgoTQGKk", "QWIvgoTQGKk"):
        assert A.youtube_id(u) == "QWIvgoTQGKk", u
    for u in ("https://vimeo.com/123", "https://evil.com/watch?v=QWIvgoTQGKk", "hello", ""):
        assert A.youtube_id(u) is None, u


def test_valid_token(keypair):
    u = AU.verify(token(keypair, sub="abc"))
    assert u.id == "abc" and u.email == "t@example.com"


@pytest.mark.parametrize("over", [{"exp": int(time.time()) - 5}, {"aud": "other"}, {"iss": "https://evil/auth/v1"},
                                  {"role": "anon"}, {"is_anonymous": True}])
def test_bad_tokens_are_refused(keypair, over):
    with pytest.raises(HTTPException) as e:
        AU.verify(token(keypair, **over))
    assert e.value.status_code == 401


def test_token_signed_by_another_key_is_refused(keypair):
    other = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(HTTPException):
        AU.verify(token(other))


# ----------------------------------------------------------------------------------------------- database
db = pytest.mark.skipif(os.environ.get("KLIPVI_DB_TESTS") != "1", reason="set KLIPVI_DB_TESTS=1 (network)")


@pytest.fixture
def users(keypair):
    from backend.db import connect
    ids = [uuid.uuid4(), uuid.uuid4()]
    with connect() as c:
        for i, u in enumerate(ids):
            c.execute("insert into auth.users (id, email, aud, role) values (%s, %s, 'authenticated', 'authenticated')",
                      (u, f"klipvi.test{i}.{u.hex[:6]}@example.com"))
        wss = [c.execute("select workspace_id from public.members where user_id = %s", (u,)).fetchone()["workspace_id"]
               for u in ids]
    try:
        yield [(str(u), str(w), token(keypair, sub=str(u))) for u, w in zip(ids, wss)]
    finally:
        with connect() as c:
            for u, w in zip(ids, wss):
                c.execute("delete from public.usage where workspace_id = %s", (w,))
                c.execute("delete from public.workspaces where id = %s", (w,))
                c.execute("delete from auth.users where id = %s", (u,))


@db
def test_me_episodes_minutes_and_isolation(users, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import pipeline as P
    from backend.db import connect
    monkeypatch.setattr(P, "source_length_s", lambda vid, url: 21 * 60 + 52)
    (u1, w1, t1), (u2, w2, t2) = users
    h1, h2 = {"Authorization": f"Bearer {t1}"}, {"Authorization": f"Bearer {t2}"}
    cl = TestClient(A.app)

    me = cl.get("/me", headers=h1).json()
    assert [w["id"] for w in me["workspaces"]] == [w1] and me["workspaces"][0]["role"] == "owner"
    assert cl.get("/me").status_code == 401

    r = cl.post(f"/workspaces/{w1}/episodes", json={"source_url": "https://youtu.be/QWIvgoTQGKk"}, headers=h1)
    assert r.status_code == 402 and r.json()["detail"]["needs"] == 22          # a new workspace has 0 minutes
    with connect() as c:
        c.execute("update public.workspaces set minutes_left = 30 where id = %s", (w1,))
    r = cl.post(f"/workspaces/{w1}/episodes", json={"source_url": "https://youtu.be/QWIvgoTQGKk"}, headers=h1)
    assert r.status_code == 201, r.text
    ep = r.json()
    assert ep["minutes"] == 22 and ep["minutes_state"] == "held" and ep["status"] == "waiting"
    assert cl.get("/me", headers=h1).json()["workspaces"][0]["minutes_free"] == 8
    assert cl.post(f"/workspaces/{w1}/episodes", json={"source_url": "vimeo.com/1"}, headers=h1).status_code == 422

    assert [e["id"] for e in cl.get(f"/workspaces/{w1}/episodes", headers=h1).json()] == [ep["id"]]
    assert cl.get(f"/episodes/{ep['id']}", headers=h1).json()["clips"] == []
    # the other user sees none of it
    assert cl.get(f"/workspaces/{w1}/episodes", headers=h2).status_code == 404
    assert cl.get(f"/episodes/{ep['id']}", headers=h2).status_code == 404
    assert cl.post(f"/workspaces/{w1}/episodes", json={"source_url": "QWIvgoTQGKk"}, headers=h2).status_code == 404
    with connect() as c:     # do not leave a job for a real worker
        c.execute("delete from public.jobs where episode_id = %s", (ep["id"],))


@db
def test_signed_video_links(users, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import storage
    from backend.db import connect
    monkeypatch.setenv("KLIPVI_STORAGE_DIR", str(tmp_path))
    (u1, w1, t1), (u2, w2, t2) = users
    with connect() as c:
        c.execute("update public.workspaces set plan = 'internal' where id = %s", (w1,))
        ep = c.execute("insert into public.episodes (workspace_id, source_url, video_id, title) values "
                       "(%s, 'https://youtu.be/x', 'xxxxxxxxxxx', 'Test Ep') returning id", (w1,)).fetchone()["id"]
        clip = c.execute("insert into public.clips (episode_id, number, rank, title, start_s, end_s, length_s) values "
                         "(%s, 'c01', 1, 'Chai', 1, 31, 30) returning id", (ep,)).fetchone()["id"]
        key = f"episodes/{ep}/c01_crop.mp4"
        storage.path(key).parent.mkdir(parents=True, exist_ok=True)
        storage.path(key).write_bytes(b"\x00" * 1000)
        rid = c.execute("insert into public.renders (clip_id, style, settings_hash, status, file, size_mb) values "
                        "(%s, 'crop', 'plain', 'ready', %s, 0.001) returning id", (clip, key)).fetchone()["id"]
    cl = TestClient(A.app)
    h1, h2 = {"Authorization": f"Bearer {t1}"}, {"Authorization": f"Bearer {t2}"}
    assert cl.get(f"/renders/{rid}/link", headers=h2).status_code == 404
    link = cl.get(f"/renders/{rid}/link?download=true", headers=h1).json()["url"]
    r = cl.get(link)
    assert r.status_code == 200 and len(r.content) == 1000 and "attachment" in r.headers["content-disposition"]
    assert cl.get(link.replace("dl=1", "dl=0")).status_code == 403                    # tampered
    assert cl.get(link.split("exp=")[0] + "exp=1&" + link.split("&", 1)[1]).status_code == 403   # expired / changed
    assert cl.get(f"/episodes/{ep}", headers=h1).json()["clips"][0]["renders"][0]["status"] == "ready"


@db
def test_caption_render_requests(users, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.db import connect
    (u1, w1, t1), (u2, w2, t2) = users
    with connect() as c:
        ep = c.execute("insert into public.episodes (workspace_id, source_url, video_id, title) values "
                       "(%s, 'https://youtu.be/x', 'xxxxxxxxxxx', 'Test Ep') returning id", (w1,)).fetchone()["id"]
        clip = c.execute("insert into public.clips (episode_id, number, rank, title, start_s, end_s, length_s) values "
                         "(%s, 'c01', 1, 'Chai', 1, 31, 30) returning id", (ep,)).fetchone()["id"]
        tpl = c.execute("select id from public.caption_templates where name = 'pop' and workspace_id is null").fetchone()["id"]
    cl = TestClient(A.app)
    h1, h2 = {"Authorization": f"Bearer {t1}"}, {"Authorization": f"Bearer {t2}"}
    body = {"style": "crop", "template_id": str(tpl), "settings": {"pos_y": 0.7}}
    assert cl.post(f"/clips/{clip}/renders", json=body, headers=h1).status_code == 409        # plain video not ready
    with connect() as c:
        c.execute("insert into public.renders (clip_id, style, settings_hash, status, file) values "
                  "(%s, 'crop', 'plain', 'ready', 'x')", (clip,))
    r1 = cl.post(f"/clips/{clip}/renders", json=body, headers=h1)
    assert r1.status_code == 200 and r1.json()["status"] == "waiting" and r1.json()["settings"]["animation"] == "pop"
    assert r1.json()["settings"]["pos_y"] == 0.7
    assert cl.post(f"/clips/{clip}/renders", json=body, headers=h1).json()["id"] == r1.json()["id"]   # reused
    assert cl.post(f"/clips/{clip}/renders", json={**body, "settings": {"font": "../x.ttf"}}, headers=h1).status_code == 422
    assert cl.post(f"/clips/{clip}/renders", json={"style": "sideways", "template_id": str(tpl)}, headers=h1).status_code == 422
    assert cl.post(f"/clips/{clip}/renders", json=body, headers=h2).status_code == 404
    assert cl.get(f"/renders/{r1.json()['id']}", headers=h1).json()["status"] == "waiting"
    assert cl.get(f"/renders/{r1.json()['id']}", headers=h2).status_code == 404
