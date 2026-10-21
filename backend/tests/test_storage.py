"""Uploaded pictures in Supabase Storage (storage.py), against a stand-in Supabase that speaks the
same HTTP, and the local-folder fallback used on your own computer.

Run with:  pytest backend/tests -q   (from the project root)
"""
import importlib
import io
import json
import os
import tempfile
import zipfile
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.tests.fakes import FakeSupabase


def png(color=(200, 80, 60), size=(60, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def upload(name: str, data: bytes):
    return SimpleNamespace(filename=name, file=io.BytesIO(data))


def fresh(monkeypatch, sb=None):
    """A brand new imy; with `sb`, files go to that stand-in Supabase."""
    os.environ["IMY_DATA_DIR"] = tempfile.mkdtemp()
    if sb:
        monkeypatch.setenv("SUPABASE_URL", sb.url)
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", sb.key)
    else:
        monkeypatch.setenv("SUPABASE_URL", "")
    from backend import account_export, auth, db as db_mod, main, storage
    importlib.reload(db_mod)
    importlib.reload(auth)
    importlib.reload(account_export)
    importlib.reload(main)
    storage._bucket_ready.clear()
    main.init_db()
    user = auth.create_user("a@example.com", "testing12345", True, terms=True)
    auth.CURRENT.set(auth.load_session(auth.start_session(user["id"])))
    pid = main.create_person(main.NewPersonIn(answers={"q1": "Mia"}, consent=True))["id"]
    return main, storage, db_mod, user, pid


def test_put_get_delete_against_supabase(monkeypatch):
    with FakeSupabase() as sb:
        main, storage, *_ = fresh(monkeypatch, sb)
        assert storage.kind() == "supabase"
        storage.put("7/a.png", b"one", "image/png")
        storage.put("7/media/b.jpg", b"two")
        storage.put("7/a.png", b"three", "image/png")                   # same name: replaced
        assert sb.buckets["imy-uploads"]["public"] is False             # created for you, and private
        assert storage.get("7/a.png") == b"three" and sb.types["imy-uploads/7/a.png"] == "image/png"
        assert storage.get("7/nope.png") is None
        assert storage.get("../etc/passwd") is None and storage.get("7/../8/x") is None
        with pytest.raises(storage.StorageError):
            storage.put("not-a-person/x.png", b"x")
        storage.put("8/c.png", b"other person")
        storage.delete_folder("7")                                       # everything under 7/, at any depth
        assert sb.files() == ["8/c.png"]
        storage.delete(["8/c.png", "8/already-gone.png"])
        assert sb.files() == [] and storage.check() == ""
        auth = importlib.import_module("backend.auth")
        auth.CURRENT.set(None)


def test_a_wrong_key_is_reported_plainly(monkeypatch):
    with FakeSupabase() as sb:
        main, storage, *_ = fresh(monkeypatch, sb)
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "wrong")
        storage._bucket_ready.clear()
        assert "401" in storage.check()
        importlib.import_module("backend.auth").CURRENT.set(None)


def test_pictures_live_in_supabase_not_on_the_server(monkeypatch):
    with FakeSupabase() as sb:
        main, storage, db_mod, user, pid = fresh(monkeypatch, sb)
        from backend import llm
        monkeypatch.setattr(llm, "enabled", lambda: True)
        monkeypatch.setattr(llm, "can_see", lambda: False)
        monkeypatch.setattr(llm, "read_screenshot", lambda img, names: {
            "platform": "messenger", "raw_text": "{}", "needs_review": False,
            "meta": {"platform": "messenger", "mode": "dark"},
            "messages": [{"speaker": "person", "text": "hi bebe"}, {"speaker": "user", "text": "hello"}]})

        # a screenshot, a profile picture, and a photo shared in the chat
        [shot] = main.upload_screenshots(pid, [upload("chat.png", png())])
        assert [m["text"] for m in shot["messages"]] == ["hi bebe", "hello"] and shot["url"].startswith(f"/uploads/{pid}/")
        again = main.upload_screenshots(pid, [upload("same.png", png())])[0]
        assert again["duplicate"] is True                                # the same picture isn't stored twice
        person = main.set_avatar(pid, upload("me.jpg", png((10, 120, 200), (400, 300))), "person")
        [photo] = main.upload_media(pid, [upload("cake.png", png((250, 200, 120)))])
        files = sb.files()
        assert len(files) == 3 and all(f.startswith(f"{pid}/") for f in files)
        assert any(f.startswith(f"{pid}/avatar/") for f in files) and any(f.startswith(f"{pid}/media/") for f in files)
        assert not any(p.is_file() for p in db_mod.UPLOAD_DIR.rglob("*"))   # nothing on the server's disk

        # served through imy, only to the account they belong to
        resp = main.serve_upload(person["avatar"].removeprefix("/uploads/"))
        assert resp.media_type == "image/jpeg" and Image.open(io.BytesIO(resp.body)).size == (320, 320)
        assert main.serve_upload(photo["url"].removeprefix("/uploads/")).body == sb.objects[f"imy-uploads/{photo['url'].removeprefix('/uploads/')}"]
        auth = importlib.import_module("backend.auth")
        other = auth.create_user("b@example.com", "testing12345", True, terms=True)
        auth.CURRENT.set(auth.load_session(auth.start_session(other["id"])))
        with pytest.raises(main.HTTPException):
            main.serve_upload(shot["url"].removeprefix("/uploads/"))
        auth.CURRENT.set(auth.load_session(auth.start_session(user["id"])))

        # "Download my data" fetches them from Supabase into the zip
        from backend import account_export
        path = account_export.build(user["id"])
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
        path.unlink()
        assert sum("/files/" in n for n in names) == 3

        # re-reading a screenshot fetches it back from Supabase
        assert main.reread_screenshot(shot["id"])["messages"][0]["text"] == "hi bebe"

        # removing a screenshot, then the person, removes their files
        main.delete_screenshot(shot["id"])
        assert len(sb.files()) == 2
        main.delete_person(pid)
        assert sb.files() == []
        auth.CURRENT.set(None)


def test_deleting_an_account_removes_every_file(monkeypatch):
    with FakeSupabase() as sb:
        main, storage, db_mod, user, pid = fresh(monkeypatch, sb)
        main.set_avatar(pid, upload("p.png", png()), "me")
        main.upload_media(pid, [upload("x.png", png())])
        storage.put("999/not-theirs.png", b"someone else")
        assert len(sb.files()) == 3
        main.wipe_account(user["id"])
        assert sb.files() == ["999/not-theirs.png"]
        importlib.import_module("backend.auth").CURRENT.set(None)


def test_without_supabase_files_go_in_the_local_folder(monkeypatch):
    main, storage, db_mod, user, pid = fresh(monkeypatch)
    assert storage.kind() == "local"
    [photo] = main.upload_media(pid, [upload("cake.png", png())])
    stored = photo["url"].removeprefix("/uploads/")
    assert (db_mod.UPLOAD_DIR / stored).is_file() and main.serve_upload(stored).body
    main.delete_person(pid)
    assert not (db_mod.UPLOAD_DIR / stored).exists()
    importlib.import_module("backend.auth").CURRENT.set(None)


def test_screenshots_need_the_ai_and_say_so(monkeypatch):
    main, storage, db_mod, user, pid = fresh(monkeypatch)
    with pytest.raises(main.HTTPException) as err:            # no Groq key: a clear message, not a crash
        main.upload_screenshots(pid, [upload("chat.png", png())])
    assert err.value.status_code == 503 and "GROQ_API_KEY" in err.value.detail and "chat file" in err.value.detail
    assert not any(p.is_file() for p in db_mod.UPLOAD_DIR.rglob("*"))   # and nothing was kept
    importlib.import_module("backend.auth").CURRENT.set(None)
