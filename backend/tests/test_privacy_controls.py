"""The account's own controls: the outside-AI switch, "Download my data", and agreeing to changed Terms.

Run with:  pytest backend/tests -q   (from the project root)
"""
import importlib
import json
import os
import tempfile
import zipfile

import pytest


def fresh():
    """A brand new, empty imy in a temporary folder (account_export keeps its own copy of the
    data folder, so it is reloaded too)."""
    os.environ["IMY_DATA_DIR"] = tempfile.mkdtemp()
    from backend import account_export, auth, db as db_mod, main
    importlib.reload(db_mod)
    importlib.reload(auth)
    importlib.reload(account_export)
    importlib.reload(main)
    main.init_db()
    return auth, main


def sign_in(auth, email="a@example.com", terms=True):
    """Sign someone in the way the middleware would: from a session, so it carries the same fields."""
    user = auth.create_user(email, "testing12345", True, terms=terms)
    me = auth.load_session(auth.start_session(user["id"]))
    auth.CURRENT.set(me)
    return me


# ---------------------------------------------------------------- the outside-AI switch
def ai_on(monkeypatch):
    """Pretend an AI service is set up, whatever the real .env says (it is loaded when imy is imported)."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("IMY_LLM", "1")
    monkeypatch.setenv("IMY_AI_MODE", "always")
    monkeypatch.setenv("IMY_AI_DAILY_PER_ACCOUNT", "0")
    monkeypatch.setenv("IMY_AI_DAILY_SITE", "0")


def test_switching_the_outside_ai_off_stops_every_call(monkeypatch):
    auth, main = fresh()
    from backend import llm, usage
    from PIL import Image
    ai_on(monkeypatch)
    me = sign_in(auth)

    assert llm.enabled() and llm.can_see() and usage.blocked() == ""
    status = llm.status()
    assert "groq" in status["providers"] and status["providers"] == llm.providers()
    assert status["available"] and not status["opted_out"]

    auth.set_cloud_ai(me["id"], False)
    assert not llm.enabled() and not llm.can_see()
    assert "turned off" in usage.blocked("reply")
    with pytest.raises(llm.OverLimit):                       # a reply is refused before anything is sent
        llm.chat("system", [], "hello", 3)
    assert llm.ask("system", "question") == ""               # background notes wait
    assert llm.see(Image.new("RGB", (8, 8))) == ""             # a photo isn't described
    with pytest.raises(llm.OverLimit):                       # and a screenshot isn't read
        llm.read_screenshot(Image.new("RGB", (8, 8)), [])
    status = llm.status()
    assert status["providers"] == [] and status["available"] and status["opted_out"]

    auth.set_cloud_ai(me["id"], True)                        # and it can be switched back on
    assert llm.enabled() and usage.blocked() == ""


def test_the_switch_belongs_to_one_account_and_is_remembered(monkeypatch):
    auth, main = fresh()
    from backend import llm
    ai_on(monkeypatch)
    a = sign_in(auth, "a@example.com")
    b = auth.create_user("b@example.com", "testing12345", True)
    auth.set_cloud_ai(a["id"], False)

    auth.CURRENT.set(auth.load_session(auth.start_session(b["id"])))
    assert llm.enabled()                                      # b is unaffected
    token = auth.start_session(a["id"])                       # a signs in again later, on another device
    assert auth.load_session(token)["cloud_ai"] is False
    auth.CURRENT.set(None)
    assert auth.cloud_ai_allowed()                            # nobody signed in (scripts, tests): unchanged


def test_the_api_reports_and_changes_the_choice():
    auth, main = fresh()
    sign_in(auth)
    assert main.who_am_i()["user"]["cloud_ai"] is True
    assert main.choose_cloud_ai(main.AiChoiceIn(cloud_ai=False)) == {"cloud_ai": False}
    assert main.who_am_i()["user"]["cloud_ai"] is False


# ---------------------------------------------------------------- agreeing to changed Terms
def test_terms_changes_ask_for_agreement_again(monkeypatch):
    auth, main = fresh()
    sign_in(auth, "new@example.com", terms=True)
    assert main.who_am_i()["user"]["terms_current"] is True

    monkeypatch.setattr(auth, "TERMS_VERSION", "2099-01-01")  # the Terms change
    me = auth.load_session(auth.start_session(auth.current_id()))
    auth.CURRENT.set(me)
    assert main.who_am_i()["user"]["terms_current"] is False
    main.agree_to_terms()
    assert main.who_am_i()["user"]["terms_current"] is True
    # the new version is what's kept with the account
    token = auth.start_session(me["id"])
    assert auth.load_session(token)["terms_version"] == "2099-01-01"


def test_an_account_from_before_the_terms_is_asked_too():
    auth, main = fresh()
    sign_in(auth, "old@example.com", terms=False)             # no version recorded
    assert main.who_am_i()["user"]["terms_current"] is False


# ---------------------------------------------------------------- Download my data
def seed_person(name, user_id, with_files=True):
    """A person with a screenshot, a shared photo and a short chat, as the app would store them."""
    from backend.db import db, now
    with db() as conn:
        pid = conn.execute(
            "INSERT INTO persons (name, answers, use_llm, data_version, created_at, updated_at, user_id, nick_them, memory) "
            "VALUES (?, ?, 0, 0, ?, ?, ?, 'love', 'birthday is June 3')",
            (name, json.dumps({"q1": "kind"}), now(), now(), user_id)).lastrowid
    from backend import storage
    storage.put(f"{pid}/shot.png", b"\x89PNG-" + name.encode())
    storage.put(f"{pid}/pic.jpg", b"jpeg-" + name.encode())
    with db() as conn:
        sid = conn.execute(
            "INSERT INTO screenshots (person_id, filename, stored_path, platform, raw_text, created_at) VALUES (?,?,?,?,?,?)",
            (pid, "IMG 001.png", f"{pid}/shot.png", "messenger", "hello there", now())).lastrowid
        conn.execute("INSERT INTO messages (person_id, screenshot_id, position, speaker, text) VALUES (?,?,?,?,?)",
                     (pid, sid, 0, "person", "kumain ka na?"))
        mid = conn.execute(
            "INSERT INTO media (person_id, stored_path, filename, kind, mime, bytes, description, sender, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)", (pid, f"{pid}/pic.jpg", "pic.jpg", "image", "image/jpeg", 9, "a cake", "user", now())).lastrowid
        conn.execute("INSERT INTO chat_messages (person_id, role, text, created_at) VALUES (?,?,?,?)", (pid, "user", "miss you", now()))
        conn.execute("INSERT INTO chat_messages (person_id, role, text, created_at, kind, media_id) VALUES (?,?,?,?,?,?)",
                     (pid, "person", "me too", now(), "media", mid))
    return pid


def test_download_my_data_has_my_things_and_only_mine():
    auth, main = fresh()
    from backend import account_export
    from backend.db import UPLOAD_DIR, db, now
    me = sign_in(auth, "a@example.com")
    other = auth.create_user("b@example.com", "testing12345", True)
    seed_person("Mia", me["id"])
    theirs = seed_person("Secret Other", other["id"])
    with db() as conn:
        conn.execute("INSERT INTO feedback (user_id, kind, message, created_at) VALUES (?,?,?,?)", (me["id"], "idea", "dark mode please", now()))
        conn.execute("INSERT INTO active_days (day, user_id) VALUES ('2026-10-12', ?)", (me["id"],))
        # a row pointing into someone else's folder must never be zipped
        conn.execute("INSERT INTO screenshots (person_id, filename, stored_path, platform, raw_text, created_at) "
                     "SELECT id, 'evil.png', ?, 'x', '', ? FROM persons WHERE name = 'Mia'", (f"{theirs}/shot.png", now()))

    path = account_export.build(me["id"])
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        names = z.namelist()
        everything = b"".join(z.read(n) for n in names)
        account = json.loads(z.read("imy-my-data/account.json"))
        conversation = next(z.read(n).decode() for n in names if n.endswith("conversation.txt"))
        person = json.loads(next(z.read(n) for n in names if n.endswith("person.json")))
    path.unlink()

    assert account["email"] == "a@example.com" and "password_hash" not in account
    assert b"scrypt" not in everything                                       # no password hash anywhere
    assert any(n.endswith("-IMG 001.png") for n in names) and any(n.endswith("-pic.jpg") for n in names)
    assert "You: miss you" in conversation and "Mia: me too [attachment: pic.jpg]" in conversation
    assert person["name"] == "Mia" and person["what_you_call_them"] == "love" and person["memory_notes"] == "birthday is June 3"
    assert b"Secret Other" not in everything and b"jpeg-Secret Other" not in everything   # nothing of b's
    assert not any(n.endswith("-evil.png") for n in names)


def test_download_endpoint_sends_a_zip_and_cleans_up():
    auth, main = fresh()
    me = sign_in(auth)
    seed_person("Mia", me["id"])
    response = main.download_my_data()
    assert response.media_type == "application/zip"
    assert response.headers["content-disposition"].startswith("attachment") and ".zip" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert os.path.exists(response.path)
    response.background.func()                                               # what runs after the download is sent
    assert not os.path.exists(response.path)


def test_download_an_account_with_nobody_set_up():
    auth, main = fresh()
    from backend import account_export
    me = sign_in(auth)
    path = account_export.build(me["id"])
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    path.unlink()
    assert "imy-my-data/account.json" in names and "imy-my-data/README.txt" in names
    assert not any("/people/" in n for n in names)
