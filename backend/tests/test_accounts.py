"""Accounts: passwords, sessions, and the rule that you only ever see your own people.

Run with:  pytest backend/tests -q   (from the project root)
"""
import importlib
import os
import tempfile

import pytest


def fresh():
    """A brand new, empty imy in a temporary folder."""
    os.environ["IMY_DATA_DIR"] = tempfile.mkdtemp()
    from backend import auth, db as db_mod, main
    importlib.reload(db_mod)
    importlib.reload(auth)
    importlib.reload(main)
    main.init_db()
    return auth, main


def as_user(auth, email="a@example.com", password="testing12345"):
    """Sign someone in the way the middleware would, so API calls know whose they are."""
    user = auth.create_user(email, password, True)
    auth.CURRENT.set(user)
    return user


# ---------------------------------------------------------------- passwords
def test_passwords_are_hashed_not_stored():
    from backend import auth
    stored = auth.hash_password("mahalkita123")
    assert "mahalkita123" not in stored and stored.startswith("scrypt$")
    assert auth.check_password("mahalkita123", stored)
    assert not auth.check_password("mahalkita124", stored)
    assert auth.hash_password("same") != auth.hash_password("same")      # a different salt each time
    for junk in ("", "not-a-hash", "scrypt$bad"):
        assert not auth.check_password("x", junk)


def test_sign_up_rules():
    auth, _ = fresh()
    for email in ("nope", "no@dots", "", "a b@example.com"):
        with pytest.raises(auth.AuthError):
            auth.create_user(email, "testing12345", True)
    with pytest.raises(auth.AuthError):
        auth.create_user("a@example.com", "short", True)
    with pytest.raises(auth.AuthError):                                  # must confirm 18+
        auth.create_user("a@example.com", "testing12345", False)
    auth.create_user("A@Example.com", "testing12345", True)
    with pytest.raises(auth.AuthError):                                  # same email, different case
        auth.create_user("a@example.com", "testing12345", True)


def test_sign_in_is_vague_about_what_was_wrong():
    auth, _ = fresh()
    auth.create_user("a@example.com", "testing12345", True)
    messages = set()
    for email, password in [("a@example.com", "wrong"), ("nobody@example.com", "testing12345")]:
        with pytest.raises(auth.AuthError) as got:
            auth.verify_user(email, password)
        messages.add(str(got.value))
    assert len(messages) == 1                                            # the same answer either way


def test_sessions_come_and_go():
    auth, _ = fresh()
    user = auth.create_user("a@example.com", "testing12345", True)
    token = auth.start_session(user["id"])
    assert auth.load_session(token)["email"] == "a@example.com"
    assert auth.load_session("made-up-token") is None
    assert auth.load_session("") is None
    auth.end_session(token)
    assert auth.load_session(token) is None


def test_the_cookie_token_is_not_kept():
    auth, _ = fresh()
    user = auth.create_user("a@example.com", "testing12345", True)
    token = auth.start_session(user["id"])
    from backend.db import db
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM sessions")]
    assert rows and all(token not in str(r) for r in rows)               # only a hash of it is stored


def test_changing_the_password_ends_other_sessions():
    auth, _ = fresh()
    user = auth.create_user("a@example.com", "testing12345", True)
    phone, laptop = auth.start_session(user["id"]), auth.start_session(user["id"])
    with pytest.raises(auth.AuthError):
        auth.change_password(user["id"], "wrong", "brandnew1234")
    auth.change_password(user["id"], "testing12345", "brandnew1234")
    assert auth.load_session(phone) is None and auth.load_session(laptop) is None
    assert auth.verify_user("a@example.com", "brandnew1234")


# ---------------------------------------------------------------- whose data is whose
def test_you_only_see_your_own_people():
    auth, main = fresh()
    as_user(auth, "diane@example.com")
    mine = main.create_person(main.NewPersonIn(answers={"q1": "Lawrence"}, consent=True))
    assert len(main.list_persons()) == 1

    as_user(auth, "someone@example.com")                                 # a different account
    assert main.list_persons() == []
    for call in (lambda: main.get_person(mine["id"]),
                 lambda: main.delete_person(mine["id"]),
                 lambda: main.get_chat(mine["id"]),
                 lambda: main.list_media(mine["id"]),
                 lambda: main.send_chat(mine["id"], main.ChatIn(message="hi")),
                 lambda: main.update_profile(mine["id"], main.ProfileIn(nick_them="stolen"))):
        with pytest.raises(main.HTTPException) as got:
            call()
        assert got.value.status_code == 404                              # same answer as "no such person"


def test_screenshots_and_messages_are_protected_too():
    auth, main = fresh()
    as_user(auth, "diane@example.com")
    p = main.create_person(main.NewPersonIn(answers={"q1": "Lawrence"}, consent=True))
    main.import_text(p["id"], main.TextImportIn(text="Lawrence: hoy\nMe: hi\n", person_name="Lawrence"))
    shot = main.list_screenshots(p["id"])[0]
    msg = shot["messages"][0]
    chat = main.send_chat(p["id"], main.ChatIn(message="hello"))["bubbles"][0]

    as_user(auth, "someone@example.com")
    for call in (lambda: main.flip_screenshot(shot["id"]),
                 lambda: main.delete_screenshot(shot["id"]),
                 lambda: main.reread_screenshot(shot["id"]),
                 lambda: main.edit_message(msg["id"], main.MessageEdit(text="changed")),
                 lambda: main.delete_message(msg["id"]),
                 lambda: main.merge_message_up(msg["id"]),
                 lambda: main.react_to_message(chat["id"], main.ReactIn(emoji="❤️")),
                 lambda: main.unsend_message(chat["id"]),
                 lambda: main.serve_upload(f"{p['id']}/anything.png")):
        with pytest.raises(main.HTTPException) as got:
            call()
        assert got.value.status_code == 404


def test_merging_cannot_pull_in_someone_elses_person():
    auth, main = fresh()
    as_user(auth, "diane@example.com")
    hers = main.create_person(main.NewPersonIn(answers={"q1": "Lawrence"}, consent=True))
    as_user(auth, "someone@example.com")
    theirs = main.create_person(main.NewPersonIn(answers={"q1": "Someone"}, consent=True))
    with pytest.raises(main.HTTPException) as got:
        main.merge_person(theirs["id"], main.MergeIn(from_id=hers["id"]))
    assert got.value.status_code == 404


def test_uploaded_files_cannot_escape_the_uploads_folder():
    auth, main = fresh()
    as_user(auth, "diane@example.com")
    for sneaky in ("../imy.db", "1/../../imy.db", "", "notanumber/x.png"):
        with pytest.raises(main.HTTPException) as got:
            main.serve_upload(sneaky)
        assert got.value.status_code == 404


def test_the_first_account_adopts_people_from_before_accounts_existed():
    auth, main = fresh()
    from backend.db import db, now
    with db() as conn:        # a person from an older copy of imy, owned by nobody
        conn.execute("INSERT INTO persons (name, answers, platform, created_at, updated_at) "
                     "VALUES ('Old Friend','{}','messenger',?,?)", (now(), now()))
    auth.CURRENT.set(auth.create_user("diane@example.com", "testing12345", True))
    assert [p["name"] for p in main.list_persons()] == ["Old Friend"]

    auth.CURRENT.set(auth.create_user("later@example.com", "testing12345", True))
    assert main.list_persons() == []                                     # only the first account adopts


def test_deleting_the_account_removes_everything():
    auth, main = fresh()
    user = as_user(auth, "diane@example.com")
    p = main.create_person(main.NewPersonIn(answers={"q1": "Lawrence"}, consent=True))
    main.import_text(p["id"], main.TextImportIn(text="Lawrence: hoy\nMe: hi\n", person_name="Lawrence"))
    main.send_chat(p["id"], main.ChatIn(message="hello"))
    main.delete_account(main.DeleteAccountIn(password="testing12345", confirm="DELETE"))

    from backend.db import db
    with db() as conn:
        for table in ("users", "sessions", "persons", "screenshots", "messages", "chat_messages", "media"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    with pytest.raises(auth.AuthError):
        auth.verify_user("diane@example.com", "testing12345")
    _ = user
