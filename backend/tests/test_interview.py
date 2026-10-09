"""Run with:  pytest backend/tests -q   (from the project root)

The newer interview: "this or that" taps fill in the style when there are few messages,
and "how would they reply?" answers are used as replies when your message matches.
"""
import json
import random
from pathlib import Path

from backend import engine, style


def test_questions_file_is_valid():
    data = json.loads((Path(__file__).resolve().parent.parent / "questions.json").read_text())
    qs = [q for s in data["sections"] for q in s["questions"]]
    ids = [q["id"] for q in qs]
    assert len(ids) == len(set(ids))
    assert all(q["type"] in {"short", "long", "choice", "reply"} for q in qs)
    assert all(q.get("options") for q in qs if q["type"] == "choice")
    assert all(q.get("prompt") for q in qs if q["type"] == "reply")
    assert {"q1", "q3", "q5", "q8", "q11", "q13", "q21"} <= set(ids)   # read by style.py / personality.py


def test_taps_fill_in_style_without_chats():
    st = style.build_style([], {"x_case": "all lowercase, no periods", "x_laugh": "HAHAHA",
                                "x_emoji_fav": "😭 🫶", "q12": "Some emojis", "x_never": "okay po, I love you"})
    assert st["lowercase_start"] > 0.9 and st["laugh"] == "HAHAHA"
    assert st["top_emojis"] == ["😭", "🫶"] and st["emoji_rate_eff"] == 0.2
    assert st["never_say"] == ["okay po", "i love you"]
    assert style.apply_style("Okay. I will.", st, random.Random(1)).startswith("okay. i will")


def test_how_would_they_reply_answers_are_used():
    person = {"id": 99, "name": "Sam", "data_version": 1, "answers": {"q1": "Sam"}, "platform": ""}
    msgs = [
        {"screenshot_id": 1, "speaker": "user", "text": "pagod ako 😩", "shot_platform": "practice"},
        {"screenshot_id": 1, "speaker": "person", "text": "pahinga ka muna", "shot_platform": "practice"},
        {"screenshot_id": 1, "speaker": "user", "text": "good night", "shot_platform": "practice"},
        {"screenshot_id": 1, "speaker": "person", "text": "night night 🌙", "shot_platform": "practice"},
    ]
    index = engine.PersonIndex(person, msgs)
    assert len(index.practice) == 2
    r = index.retrieval_reply("pagod na ako", [], random.Random(0))
    assert r.source == "practice" and r.bubbles == ["pahinga ka muna"]
    r = index.retrieval_reply("goodnight love", [], random.Random(0))   # same intent, different words
    assert r.source == "practice" and r.bubbles == ["night night 🌙"]


def test_fixes_from_the_chat_and_merging(tmp_path=None, monkeypatch=None):
    import os, tempfile, importlib
    os.environ["IMY_DATA_DIR"] = tempfile.mkdtemp()
    from backend import auth, db as db_mod, main
    importlib.reload(db_mod); importlib.reload(main)
    main.init_db()
    # Everyone's data belongs to an account now. Calling the API straight from a test skips the
    # sign-in cookie, so say who's asking, the way the middleware normally would.
    auth.CURRENT.set(auth.create_user("test@example.com", "testing12345", True))
    a = main.create_person(main.NewPersonIn(answers={"q1": "Sam", "q3": "bebe"}, consent=True))
    b = main.create_person(main.NewPersonIn(answers={"q1": "Sam", "q6": "makulit, loyal, chill"}, consent=True))
    main.teach(a["id"], main.TeachIn(prompt="pagod ako", reply="pahinga ka muna bebe"))
    out = main.teach(a["id"], main.TeachIn(prompt="ingat", reply="ikaw din\nchat ka pag uwi"))
    assert out["taught"] == 2
    r = main.send_chat(a["id"], main.ChatIn(message="pagod na ako"))
    assert [x["text"] for x in r["bubbles"]] == ["pahinga ka muna bebe"] and r["bubbles"][-1]["debug"]["source"] == "practice"
    main.teach(b["id"], main.TeachIn(prompt="hi", reply="uy"))
    merged = main.merge_person(a["id"], main.MergeIn(from_id=b["id"]))
    assert merged["answers"]["q6"] == "makulit, loyal, chill" and merged["answers"]["q3"] == "bebe"
    assert [p["id"] for p in main.list_persons()] == [a["id"]]
    assert sum(1 for s in main.list_screenshots(a["id"]) if s["platform"] == "correction") == 2
