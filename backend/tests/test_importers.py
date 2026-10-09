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


# ------------------------------------------------------------------ formats recognised by content
TG_HTML = """<!DOCTYPE html><html><head><title>Exported Data</title></head><body>
<div class="page_header"><div class="text bold">Mia</div></div>
<div class="history">
<div class="message service" id="message-1"><div class="body details">5 January 2024</div></div>
<div class="message default clearfix" id="message1">
 <div class="pull_left userpic_wrap"><div class="userpic">M</div></div>
 <div class="body"><div class="pull_right date details" title="05.01.2024 22:15:03 UTC+08:00">22:15</div>
  <div class="from_name">Mia</div><div class="text">hello<br>kamusta ka</div></div></div>
<div class="message default clearfix joined" id="message2">
 <div class="body"><div class="pull_right date details" title="05.01.2024 22:15:40 UTC+08:00">22:15</div>
  <div class="text">miss na kita</div></div></div>
<div class="message default clearfix" id="message3">
 <div class="body"><div class="pull_right date details" title="05.01.2024 22:16:00 UTC+08:00">22:16</div>
  <div class="from_name">Ana</div><div class="text">miss you too &lt;3</div></div></div>
</div></body></html>"""


def test_telegram_html_default_export():
    [c] = importers.read_export("messages.html", io.BytesIO(TG_HTML.encode()))
    assert c.platform == "telegram"
    assert [(m.sender, m.text) for m in c.messages] == [
        ("Mia", "hello\nkamusta ka"), ("Mia", "miss na kita"), ("Ana", "miss you too <3")]
    assert c.messages[0].ts is not None


FB_HTML = """<html><head><title>Lawrence Panes</title></head><body><div class="_a706">
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder"><div class="_3-95 _2pim _a6-h _a6-i">Lawrence Panes</div>
<div class="_3-95 _a6-p"><div><div></div><div>tara gym bukas</div><div></div></div></div>
<div class="_3-94 _a6-o">Jan 05, 2024 10:16:00 pm</div></div>
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder"><div class="_3-95 _2pim _a6-h _a6-i">Diane</div>
<div class="_3-95 _a6-p"><div><div></div><div>sige ba</div><div></div></div></div>
<div class="_3-94 _a6-o">Jan 05, 2024 10:15:00 pm</div></div>
</div></body></html>"""


def test_facebook_html_download_is_read_oldest_first():
    zipped = _zip({"your_facebook_activity/messages/inbox/lawrencepanes_123/message_1.html": FB_HTML})
    [c] = importers.read_export("facebook-diane.zip", zipped)
    assert c.platform == "messenger" and c.title == "Lawrence Panes"
    assert [(m.sender, m.text) for m in c.messages] == [("Diane", "sige ba"), ("Lawrence Panes", "tara gym bukas")]


def test_messenger_secure_storage_json_camelcase():
    data = {"participants": ["Diane", "Lawrence Panes"], "threadName": "Lawrence Panes", "messages": [
        {"isUnsent": False, "media": [], "reactions": [], "senderName": "Lawrence Panes", "text": "uy", "timestamp": 1790000000000, "type": "text"},
        {"isUnsent": True, "senderName": "Diane", "text": "oops", "timestamp": 1790000001000},
        {"isUnsent": False, "senderName": "Diane", "text": "hii", "timestamp": 1790000002000, "type": "text"},
    ]}
    [c] = importers.read_export("secure_storage/lawrence.json", io.BytesIO(json.dumps(data).encode()))
    assert c.title == "Lawrence Panes" and c.platform == "messenger"
    assert [(m.sender, m.text) for m in c.messages] == [("Lawrence Panes", "uy"), ("Diane", "hii")]
    assert abs(c.messages[0].ts - 1790000000) < 1


def test_discord_chat_exporter_json():
    data = {"guild": {"name": "Direct Messages"}, "channel": {"name": "mia"}, "messages": [
        {"timestamp": "2026-01-05T22:15:00+08:00", "content": "gg", "author": {"name": "mia_", "nickname": "Mia"}},
        {"timestamp": "2026-01-05T22:16:00+08:00", "content": "rematch?", "author": {"name": "ana"}},
    ]}
    [c] = importers.read_export("dm.json", io.BytesIO(json.dumps(data).encode()))
    assert c.platform == "discord" and c.title == "mia"
    assert [(m.sender, m.text) for m in c.messages] == [("Mia", "gg"), ("ana", "rematch?")]


LINE_TXT = "[LINE] Chat history with Mia\nSaved on: 2024/01/06 10:00\n\n2024/01/05(Fri)\n22:15\tMia\thello\n22:16\tAna\thi po\nkamusta\n22:17\tMia\t[Sticker]\n"


def test_line_txt():
    [c] = importers.read_export("chat.txt", io.BytesIO(LINE_TXT.encode()))
    assert c.platform == "line" and c.title == "Mia"
    assert [(m.sender, m.text) for m in c.messages][:2] == [("Mia", "hello"), ("Ana", "hi po\nkamusta")]


def test_file_name_doesnt_matter():
    [c] = importers.read_export("notes from phone.dat", io.BytesIO(WA_IPHONE.encode()))
    assert c.platform == "whatsapp" and len(c.messages) == 4
