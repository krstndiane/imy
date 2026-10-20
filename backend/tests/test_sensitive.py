"""Private details are hidden before imy keeps or learns anything, and you choose which parts of a
chat it learns from.

Run with:  pytest backend/tests -q   (from the project root)
"""
import importlib
import json
import os
import tempfile

import pytest

from backend import sensitive

HIDE = [
    ("password ko sa fb ay Mahal1234!", "password"),
    ("yung wifi password natin ay pldtwifi8812", "password"),
    ("pw: sunflower", "password"),
    ("Password: Tag-araw!", "password"),
    ("otp ko 482913 pls send", "code"),
    ("482913 is your verification code", "code"),
    ("pin ng atm ko 1234", "code"),
    ("visa 4539-1488-0343-6467 exp 08/27", "card"),
    ("bdo account ko 0012 3456 7890", "account"),
    ("send mo sa gcash 0917 123 4567", "account"),
    ("sss no ko 34-1234567-8", "id"),
    ("text mo ko 09171234567", "phone"),
    ("email ko diane@gmail.com", "email"),
    ("username: kirs6103", "login"),
    ("key ko sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123", "key"),
]

KEEP = [   # everyday Taglish that must stay exactly as written
    "pw ka ba bukas?", "pwd magpunta dito mamaya", "nakalimutan ko password ko", "password is wrong daw",
    "pass muna ako", "mag 2024 na pala", "nasa room 304 ako", "1000 pesos lang", "kain tayo sa bdo mall",
    "may account ka ba sa ig?", "my password is still the same", "bukas 10am ha",
]


@pytest.mark.parametrize("text,kind", HIDE)
def test_private_details_are_hidden(text, kind):
    masked, found = sensitive.mask(text)
    assert found == {kind: 1}
    assert sensitive.marker(kind) in masked
    assert sensitive.mask(masked) == (masked, {})          # hiding twice changes nothing


@pytest.mark.parametrize("text", KEEP)
def test_ordinary_messages_are_left_alone(text):
    assert sensitive.mask(text) == (text, {})


def test_a_secret_sent_in_its_own_bubble():
    msgs = [{"speaker": "person", "text": "password ko:"}, {"speaker": "person", "text": "Mahal1234!"},
            {"speaker": "user", "text": "anong otp"}, {"speaker": "person", "text": "482913"},
            {"speaker": "user", "text": "pw ka ba bukas?"}, {"speaker": "person", "text": "busy"}]
    masked, found = sensitive.mask_messages(msgs)
    assert [m["text"] for m in masked] == ["password ko:", "[password hidden]", "anong otp",
                                           "[one-time code or PIN hidden]", "pw ka ba bukas?", "busy"]
    assert found == {"password": 1, "code": 1}


def test_describe_in_words():
    assert sensitive.describe({}) == ""
    assert sensitive.describe({"password": 1}) == "1 password"
    assert sensitive.describe({"phone": 2, "password": 2, "account": 1}) == \
        "2 passwords, 1 account number and 2 phone numbers"


# ---------------------------------------------------------------- inside imy
def fresh():
    os.environ["IMY_DATA_DIR"] = tempfile.mkdtemp()
    from backend import auth, db as db_mod, main
    importlib.reload(db_mod)
    importlib.reload(auth)
    importlib.reload(main)
    main.init_db()
    user = auth.create_user("a@example.com", "testing12345", True, terms=True)
    auth.CURRENT.set(auth.load_session(auth.start_session(user["id"])))
    pid = main.create_person(main.NewPersonIn(answers={"q1": "Mia"}, consent=True))["id"]
    return main, pid


def _export(main, msgs):
    """An uploaded export as import-file leaves it: a token for a saved list of chats."""
    main.IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    token = "ab" * 16
    (main.IMPORT_DIR / f"{token}.json").write_text(json.dumps(
        [{"id": "c1", "title": "Mia", "platform": "messenger", "messages": msgs}]))
    return token


DAY = 86400
CHAT = [   # three talks, a day apart: [sender, text, timestamp]
    ["Mia", "good morning!", 1 * DAY], ["Me", "morning", 1 * DAY + 60],
    ["Mia", "away tayo kahapon ha", 2 * DAY], ["Me", "sorry na", 2 * DAY + 60],
    ["Mia", "wifi password natin ay pldtwifi8812", 3 * DAY], ["Me", "thanks", 3 * DAY + 60],
]


def test_you_choose_which_parts_of_a_chat_imy_learns_from():
    main, pid = fresh()
    pick = dict(token=_export(main, CHAT), conversation_id="c1", person_name="Mia", me_name="Me")

    preview = main.import_preview(pid, main.ImportPickIn(**pick))
    assert [t["index"] for t in preview["talks"]] == [0, 1, 2]
    assert preview["talks"][1]["preview"] == "away tayo kahapon ha"
    assert preview["talks"][2]["hidden"] == "1 password" and preview["hidden"] == "1 password"
    assert "pldtwifi8812" not in json.dumps(preview)          # the preview never shows it either

    with pytest.raises(main.HTTPException):                    # picking nothing is refused
        main.import_commit(pid, main.ImportCommitIn(**pick, talks=[]))

    done = main.import_commit(pid, main.ImportCommitIn(**pick, talks=[0, 2]))   # skip the argument
    assert done["sessions"] == 2 and done["person_messages"] == 2 and done["hidden"] == "1 password"
    shots = main.list_screenshots(pid)
    texts = [m["text"] for s in shots for m in s["messages"]]
    assert "away tayo kahapon ha" not in texts
    assert "wifi password natin ay [password hidden]" in texts
    assert [s["hidden"] for s in shots] == ["", "1 password"]


def test_every_way_in_hides_private_details():
    main, pid = fresh()
    shot = main.import_text(pid, main.TextImportIn(text="Mia: gcash ko 09171234567\nMe: ok", person_name="Mia"))
    assert shot["messages"][0]["text"] == "gcash ko [account number hidden]" and shot["hidden"] == "1 account number"
    with main.db() as conn:
        raw = conn.execute("SELECT raw_text FROM screenshots WHERE id = ?", (shot["id"],)).fetchone()[0]
    assert "0917" not in raw                                   # the pasted original isn't kept either

    edited = main.edit_message(shot["messages"][1]["id"], main.MessageEdit(text="pin ko 4821"))
    assert edited["text"] == "pin ko [one-time code or PIN hidden]" and edited["hidden"]

    main.teach(pid, main.TeachIn(prompt="anong email mo", reply="mia.santos@gmail.com"))
    with main.db() as conn:
        taught = [r[0] for r in conn.execute("SELECT text FROM messages WHERE person_id = ?", (pid,))]
    assert "[email address hidden]" in taught and not any("@gmail" in t for t in taught)


def test_chats_saved_before_the_check_are_cleaned_once():
    main, pid = fresh()
    with main.db() as conn:                                    # saved by an older imy, before hiding existed
        sid = conn.execute("INSERT INTO screenshots (person_id, filename, platform, raw_text, created_at) "
                           "VALUES (?, 'old', 'text-import', 'Mia: pw: Secret123', '2026-01-01')", (pid,)).lastrowid
        conn.execute("INSERT INTO messages (person_id, screenshot_id, position, speaker, text) "
                     "VALUES (?, ?, 0, 'person', 'pw: Secret123')", (pid, sid))
        conn.execute("DELETE FROM upkeep")
    assert sensitive.hide_existing() == 2                      # the message and the saved original text
    shot = next(s for s in main.list_screenshots(pid) if s["id"] == sid)
    assert shot["messages"][0]["text"] == "pw: [password hidden]" and shot["hidden"] == "1 password"
    assert sensitive.hide_existing() == 0                      # remembered: it doesn't run again
