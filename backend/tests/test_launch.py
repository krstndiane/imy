"""The launch checklist, as tests: daily AI limits, Terms and consent, the privacy and terms pages,
the secure cookie, the admin tool, backups that can be proven and restored, and preflight.

Run with:  pytest backend/tests -q   (from the project root)
"""
import importlib
import os
import tempfile
import threading
import zipfile
from pathlib import Path

import pytest


def fresh():
    os.environ["IMY_DATA_DIR"] = tempfile.mkdtemp()
    from backend import auth, db as db_mod, main, usage
    importlib.reload(db_mod)
    importlib.reload(auth)
    importlib.reload(main)
    main.init_db()
    usage._site.update(day="", n=0)          # the site total is cached per day; start clean
    return auth, main


def sign_in(auth, email="a@example.com"):
    user = auth.create_user(email, "testing12345", True, terms=True)
    auth.CURRENT.set(user)
    return user


def fake_ai(monkeypatch, answer="hi bebe", fail=False):
    """Pretend an AI provider is on and answers instantly, without the internet."""
    from backend import llm

    def chat(model, system, turns):
        if fail:
            raise llm.LLMError("429: slow down", 429)
        return answer
    monkeypatch.setattr(llm, "_candidates", lambda: [("groq", "fake-model")])
    monkeypatch.setattr(llm, "_vision_candidates", lambda: [("groq", "fake-eyes")])
    monkeypatch.setattr(llm, "CALLS", {"groq": (lambda: ["fake-model"], chat)})
    monkeypatch.setattr(llm, "_groq_see", lambda model, url, prompt: "a cake on a table")
    monkeypatch.setattr(llm, "providers", lambda: ["groq"])
    return llm


# ---------------------------------------------------------------- daily AI limit
def test_each_account_gets_its_daily_share(monkeypatch):
    auth, _ = fresh()
    from backend import usage
    llm = fake_ai(monkeypatch)
    monkeypatch.setenv("IMY_AI_DAILY_PER_ACCOUNT", "2")
    monkeypatch.setenv("IMY_AI_DAILY_SITE", "0")
    a = sign_in(auth, "a@example.com")
    assert llm.chat("sys", [], "hi", 3) == ["hi bebe"]
    assert llm.chat("sys", [], "kumain ka na", 3) == ["hi bebe"]
    with pytest.raises(llm.OverLimit) as err:
        llm.chat("sys", [], "uy", 3)
    assert "2 AI replies" in llm.explain(err.value)
    assert usage.summary(a["id"]) == {"used": 2, "limit": 2}
    assert llm.see("data:image/png;base64,AAAA") == ""          # photos share the same allowance
    assert llm.ask("sys", "notes please") == ""                 # background notes wait too
    sign_in(auth, "b@example.com")                              # someone else still has theirs
    assert llm.chat("sys", [], "hi", 3) == ["hi bebe"]
    assert llm.see("data:image/png;base64,AAAA") == "a cake on a table"


def test_the_whole_site_can_have_a_ceiling(monkeypatch):
    auth, _ = fresh()
    llm = fake_ai(monkeypatch)
    monkeypatch.setenv("IMY_AI_DAILY_PER_ACCOUNT", "0")          # no per-account limit...
    monkeypatch.setenv("IMY_AI_DAILY_SITE", "3")                 # ...but three for everyone together
    for email in ("a@example.com", "b@example.com", "c@example.com"):
        sign_in(auth, email)
        llm.chat("sys", [], "hi", 3)
    sign_in(auth, "d@example.com")
    with pytest.raises(llm.OverLimit) as err:
        llm.chat("sys", [], "hi", 3)
    assert "allowance for today" in str(err.value)


def test_failed_ai_calls_are_not_counted(monkeypatch):
    auth, _ = fresh()
    from backend import usage
    llm = fake_ai(monkeypatch, fail=True)
    monkeypatch.setenv("IMY_AI_DAILY_PER_ACCOUNT", "5")
    a = sign_in(auth)
    with pytest.raises(llm.LLMError):
        llm.chat("sys", [], "hi", 3)
    assert usage.used(a["id"]) == 0


def test_memory_notes_know_whose_chat_they_are(monkeypatch):
    """The notes run in a background thread; it must still count against the right account."""
    auth, _ = fresh()
    from backend import llm, notes
    a = sign_in(auth)
    seen, done = [], threading.Event()
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(notes, "update", lambda pid, name: (seen.append(auth.current_id()), done.set()))
    notes.update_later(1, "Law")
    assert done.wait(5)
    assert seen == [a["id"]]


def test_usage_shows_in_the_account_menu_and_goes_with_the_account(monkeypatch):
    auth, main = fresh()
    llm = fake_ai(monkeypatch)
    monkeypatch.setenv("IMY_AI_DAILY_PER_ACCOUNT", "50")
    sign_in(auth)
    llm.chat("sys", [], "hi", 3)
    me = main.who_am_i()["user"]
    assert me["ai_today"] == {"used": 1, "limit": 50}
    main.delete_account(main.DeleteAccountIn(password="testing12345", confirm="DELETE"))
    from backend.db import db
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_usage WHERE user_id = ?", (me["id"],)).fetchone()[0] == 0


# ---------------------------------------------------------------- Terms and consent
def test_sign_up_needs_the_terms_and_records_them():
    auth, main = fresh()
    with pytest.raises(Exception) as err:
        main.sign_up(main.SignUpIn(email="a@example.com", password="testing12345", is_adult=True, agrees=False))
    assert getattr(err.value, "status_code", None) == 400
    main.sign_up(main.SignUpIn(email="a@example.com", password="testing12345", is_adult=True, agrees=True))
    from backend.db import db
    with db() as conn:
        row = conn.execute("SELECT terms_version, terms_at FROM users WHERE email = 'a@example.com'").fetchone()
    assert row["terms_version"] == auth.TERMS_VERSION and row["terms_at"]


def test_adding_someone_needs_consent_and_records_it():
    auth, main = fresh()
    sign_in(auth)
    with pytest.raises(Exception) as err:
        main.create_person(main.NewPersonIn(answers={"q1": "Law"}))
    assert getattr(err.value, "status_code", None) == 400
    p = main.create_person(main.NewPersonIn(answers={"q1": "Law"}, consent=True))
    from backend.db import db
    with db() as conn:
        assert conn.execute("SELECT consent_at FROM persons WHERE id = ?", (p["id"],)).fetchone()[0]


# ---------------------------------------------------------------- the cookie and the pages
class FakeRequest:
    def __init__(self, scheme="http", proto=None):
        self.headers = {"x-forwarded-proto": proto} if proto else {}
        self.url = type("U", (), {"scheme": scheme})()


def test_cookie_is_secure_whenever_the_visit_is_https(monkeypatch):
    from backend import auth
    monkeypatch.delenv("IMY_SECURE_COOKIES", raising=False)
    monkeypatch.delenv("IMY_ORIGINS", raising=False)
    assert auth.secure_cookies(FakeRequest("https")) is True
    assert auth.secure_cookies(FakeRequest("http", proto="https")) is True      # behind the host's proxy
    assert auth.secure_cookies(FakeRequest("http")) is False                    # your own computer
    monkeypatch.setenv("IMY_SECURE_COOKIES", "1")
    assert auth.secure_cookies(FakeRequest("http")) is True
    monkeypatch.setenv("IMY_SECURE_COOKIES", "0")
    assert auth.secure_cookies(FakeRequest("https")) is False


def test_privacy_and_terms_show_the_contact_address(monkeypatch):
    _, main = fresh()
    for page in (main.privacy_page, main.terms_page):
        monkeypatch.setenv("IMY_CONTACT_EMAIL", "hello@imy.ph")
        body = page().body.decode()
        assert 'mailto:hello@imy.ph' in body and "{{CONTACT_EMAIL}}" not in body
        monkeypatch.setenv("IMY_CONTACT_EMAIL", '"><script>alert(1)</script>')
        assert "<script>alert(1)" not in page().body.decode()                   # escaped, never run
        monkeypatch.delenv("IMY_CONTACT_EMAIL")
        body = page().body.decode()
        assert "{{CONTACT_EMAIL}}" not in body and "hasn't added a contact address" in body


# ---------------------------------------------------------------- admin tool
def test_admin_remove_previews_then_deletes():
    auth, main = fresh()
    from backend import admin
    from backend.db import UPLOAD_DIR, db
    sign_in(auth, "test@example.com")
    p = main.create_person(main.NewPersonIn(answers={"q1": "Law"}, consent=True))
    main.import_text(p["id"], main.TextImportIn(text="Law: hoy\nMe: hi\nLaw: kain na\n", person_name="Law"))
    (UPLOAD_DIR / str(p["id"])).mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / str(p["id"]) / "x.jpg").write_bytes(b"x")
    assert admin.remove("nobody@example.com", True) == 1
    assert admin.remove("test@example.com", False) == 0                         # a preview deletes nothing
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert admin.remove("TEST@example.com", True) == 0
    with db() as conn:
        for table in ("users", "persons", "messages", "screenshots"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert not (UPLOAD_DIR / str(p["id"])).exists()


# ---------------------------------------------------------------- backups
def test_a_backup_can_be_proven_and_restored():
    auth, main = fresh()
    from backend import backup, db as db_mod
    importlib.reload(backup)                                   # pick up this test's data folder
    sign_in(auth)
    p = main.create_person(main.NewPersonIn(answers={"q1": "Law"}, consent=True))
    main.import_text(p["id"], main.TextImportIn(text="Law: hoy\nMe: hi\nLaw: kain na\n", person_name="Law"))
    (db_mod.UPLOAD_DIR / str(p["id"])).mkdir(parents=True, exist_ok=True)
    (db_mod.UPLOAD_DIR / str(p["id"]) / "x.jpg").write_bytes(b"photo")
    made = backup.run()
    proof = backup.check(made["file"])
    assert proof["ok"] and proof["rows"]["messages"] == 3 and proof["uploads"] == 1
    into = Path(tempfile.mkdtemp()) / "restored"
    back = backup.restore(made["file"], into)
    assert back["ok"] and (into / "imy.db").exists() and (into / "uploads" / str(p["id"]) / "x.jpg").read_bytes() == b"photo"
    assert "error" in backup.restore(made["file"], into)       # never over an existing database
    raw = bytearray(Path(made["file"]).read_bytes())
    raw[len(raw) // 3] ^= 0xFF
    broken = Path(tempfile.mkdtemp()) / "broken.zip"
    broken.write_bytes(raw)
    assert backup.check(broken)["ok"] is False                  # damage is reported, not crashed on
    sneaky = Path(tempfile.mkdtemp()) / "sneaky.zip"
    with zipfile.ZipFile(sneaky, "w") as z:
        z.write(db_mod.DB_PATH, "imy.db")
        z.writestr("../../outside.txt", "x")
    assert "unsafe" in backup.restore(sneaky, Path(tempfile.mkdtemp()) / "r")["error"]


# ---------------------------------------------------------------- preflight
def test_preflight_fails_without_a_contact_address(monkeypatch):
    fresh()
    from backend import backup, preflight
    importlib.reload(backup)
    importlib.reload(preflight)
    monkeypatch.delenv("IMY_CONTACT_EMAIL", raising=False)
    monkeypatch.setenv("IMY_BACKUP_DAILY", "1")
    assert preflight.main([]) == 1
    assert any(level == "FAIL" and "IMY_CONTACT_EMAIL" in what for level, what, _ in preflight.results)
    preflight.results.clear()
    monkeypatch.setenv("IMY_CONTACT_EMAIL", "hello@imy.ph")
    preflight.main([])
    assert not any(level == "FAIL" and "CONTACT" in what for level, what, _ in preflight.results)


# ---------------------------------------------------------------- rate limits can't be dodged
def test_rate_limit_uses_the_address_the_proxy_saw(monkeypatch):
    _, main = fresh()

    class Req:
        def __init__(self, xff=None):
            self.headers = {"x-forwarded-for": xff} if xff else {}
            self.client = type("C", (), {"host": "10.1.1.1"})()
    monkeypatch.setenv("IMY_PROXY_HOPS", "1")
    assert main.visitor(Req("6.6.6.6, 203.0.113.9")) == "203.0.113.9"   # the visitor typed 6.6.6.6
    assert main.visitor(Req("203.0.113.9")) == "203.0.113.9"
    assert main.visitor(Req()) == "10.1.1.1"
    monkeypatch.setenv("IMY_PROXY_HOPS", "2")                           # e.g. Cloudflare in front of the host
    assert main.visitor(Req("6.6.6.6, 203.0.113.9, 172.16.0.1")) == "203.0.113.9"
    monkeypatch.setenv("IMY_PROXY_HOPS", "0")                           # no proxy: never trust the header
    assert main.visitor(Req("6.6.6.6")) == "10.1.1.1"
