"""Run with:  pytest backend/tests -q   (from the project root)

Tests for importers.py using tiny fake exports shaped like the real ones.
"""
import io
import json
import zipfile

from backend import importers


def _zip(files: dict) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    buf.seek(0)
    return buf


WA_IPHONE = (
    "‎[10/8/26, 9:00:01 PM] Sam: ‎Messages and calls are end-to-end encrypted.\n"
    "[10/8/26, 9:01:00 PM] Ana: hii love\n"
    "[10/8/26, 9:01:30 PM] Sam: hello love ko\n"
    "kain ka na?\n"
    "[10/8/26, 9:02:00 PM] Sam: ‎image omitted\n"
    "[10/9/26, 8:00:00 AM] Ana: good morning\n"
    "[10/9/26, 8:05:00 AM] Sam: morning bebe <This message was edited>\n"
)

WA_ANDROID_DAY_FIRST = (
    "25/12/2025, 22:15 - Mia: merry xmas!!\n"
    "25/12/2025, 22:16 - Me: <Media omitted>\n"
    "25/12/2025, 22:17 - Me: merry christmas mia\n"
)


def test_whatsapp_iphone_txt():
    [c] = importers.read_export("WhatsApp Chat - Sam.txt", io.BytesIO(WA_IPHONE.encode()))
    assert c.platform == "whatsapp"
    texts = [(m.sender, m.text) for m in c.messages]
    assert ("Sam", "hello love ko\nkain ka na?") in texts       # multi-line message kept together
    assert ("Sam", "morning bebe") in texts                      # "edited" tag removed
    assert not any("omitted" in t or "encrypted" in t for _, t in texts)
    assert c.messages[0].ts < c.messages[-1].ts


def test_whatsapp_zip_and_day_first_dates():
    [c] = importers.read_export("WhatsApp Chat - Mia.zip", _zip({"_chat.txt": WA_ANDROID_DAY_FIRST}))
    assert [m.text for m in c.messages] == ["merry xmas!!", "merry christmas mia"]
    assert c.messages[0].ts is not None   # 25/12 parsed as day 25, month 12


def _fb_thread(title, msgs):
    # Facebook writes "ñ" as the broken text "Ã±" and lists the newest message first.
    return json.dumps({
        "participants": [{"name": "Sam"}, {"name": "Ana"}], "title": title,
        "messages": [
            {"sender_name": s, "timestamp_ms": ts, "content": t} for s, ts, t in msgs
        ],
    })


def test_messenger_download_zip():
    newest_first = [
        ("Sam", 3000, "pahinga ka bebe"),
        ("Ana", 2000, "pagod ako"),
        ("Sam", 1500, "Ana sent a photo."),
        ("Sam", 1000, "maÃ±ana na lang"),
    ]
    files = {
        "your_facebook_activity/messages/inbox/sam_123/message_1.json": _fb_thread("Sam", newest_first),
        "your_facebook_activity/messages/inbox/mia_456/message_1.json": json.dumps({
            "participants": [{"name": "Mia"}, {"name": "Ana"}], "title": "Mia",
            "messages": [{"sender_name": "Ana", "timestamp_ms": 5, "content": "hi"}]}),
    }
    convs = importers.read_export("facebook-ana.zip", _zip(files))
    sam = next(c for c in convs if c.title == "Sam")
    assert sam.platform == "messenger"
    assert [m.text for m in sam.messages] == ["mañana na lang", "pagod ako", "pahinga ka bebe"]
    assert importers.owner_name(convs) == "Ana"          # the one who's in every chat
    assert importers.guess_roles(sam, ["Sam"], "Ana") == ("Sam", "Ana")


def test_messenger_parts_are_merged():
    files = {
        "messages/inbox/x_1/message_1.json": _fb_thread("X", [("X", 2000, "two")]),
        "messages/inbox/x_1/message_2.json": _fb_thread("X", [("X", 1000, "one")]),
    }
    [c] = importers.read_export("export.zip", _zip(files))
    assert [m.text for m in c.messages] == ["one", "two"]


def test_telegram_result_json():
    data = {"name": "Mia", "type": "personal_chat", "messages": [
        {"type": "service", "action": "phone_call"},
        {"type": "message", "from": "Mia", "date_unixtime": "100", "text": ["see ", {"type": "bold", "text": "you"}, " soon"]},
        {"type": "message", "from": "Ana", "date_unixtime": "200", "text": "okiee"},
        {"type": "message", "from": "Mia", "date_unixtime": "300", "text": "", "photo": "photos/1.jpg"},
    ]}
    [c] = importers.read_export("result.json", io.BytesIO(json.dumps(data).encode()))
    assert c.platform == "telegram"
    assert [(m.sender, m.text) for m in c.messages] == [("Mia", "see you soon"), ("Ana", "okiee")]


def test_html_export_gives_helpful_error():
    try:
        importers.read_export("messages.zip", _zip({"messages/inbox/a/message_1.html": "<html></html>"}))
    except importers.ExportError as exc:
        assert "JSON" in str(exc)
    else:
        raise AssertionError("expected ExportError")


def test_split_sessions_on_long_silence():
    msgs = [{"ts": t, "text": str(t)} for t in (0, 60, 120, 60 * 60 * 10, 60 * 60 * 10 + 30)]
    sessions = importers.split_sessions(msgs, gap_hours=6)
    assert [len(s) for s in sessions] == [3, 2]
    assert [len(s) for s in importers.split_sessions([{"ts": None}] * 5, max_len=2)] == [2, 2, 1]
