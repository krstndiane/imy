"""importers.py - read chat EXPORT files (the legal, official way to get your own chats).

Every big app lets you download your own messages. This file reads those downloads:

  WhatsApp          Chat > (name) > Export chat > Without media   -> a .txt, or a .zip with _chat.txt
  Messenger         facebook.com/dyi > Messages > Format: JSON    -> a .zip with .../inbox/<name>/message_1.json
  Instagram         Accounts Center > Download your information > Messages > JSON (same format as Messenger)
  Telegram Desktop  Chat > ⋮ > Export chat history                  -> result.json, or messages.html (the default)
  Messenger E2EE    Privacy & safety > End-to-end encrypted chats > Message storage > Download secure storage data
  LINE              Chat > ≡ > Settings > Export chat history       -> a .txt
  Discord           DiscordChatExporter, JSON format

The FILE NAME doesn't matter: each file is recognised by what's inside it (a WhatsApp chat renamed
"notes.txt" still works), and Facebook/Instagram downloads made in HTML are read too.

Whatever goes in, the same shape comes out:

  Conversation(id, title, platform, messages=[Msg(sender, text, ts), ...])

The API (main.py) then shows you the conversations, you say which sender is them and
which is you, and `split_sessions` cuts the chat into separate "talks" (a new talk starts
after a long silence). Each talk is stored like one screenshot, so the reply engine pairs
"what you said" with "what they said next" only inside the same talk.

Nothing here needs the internet. To support another app, write a function that returns a
list of Conversation and add it to `read_export`.
"""
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import PurePosixPath

SESSION_GAP_HOURS = 6       # silence longer than this = a new talk
MAX_SESSION_MESSAGES = 120  # very long talks are split so each item stays reviewable
MAX_TEXT_CHARS = 2000
MAX_FILE_BYTES = 300 * 1024 * 1024   # one file inside the export


@dataclass
class Msg:
    sender: str
    text: str
    ts: float | None = None   # seconds since 1970, if the export has times


@dataclass
class Conversation:
    id: str
    title: str
    platform: str             # whatsapp | messenger | instagram | telegram | unknown
    messages: list[Msg] = field(default_factory=list)

    def participants(self) -> list[dict]:
        """Senders, most active first: [{"name": "Mia", "count": 812}, ...]"""
        counts: dict[str, int] = {}
        for m in self.messages:
            counts[m.sender] = counts.get(m.sender, 0) + 1
        return [{"name": n, "count": c} for n, c in sorted(counts.items(), key=lambda kv: -kv[1])]


class ExportError(ValueError):
    """The file isn't a chat export we understand. The message is shown to the user."""


# ------------------------------------------------------------------ cleaning
# Lines that are the app talking, not the person: media placeholders, calls, reactions...
SKIP_RES = [re.compile(p, re.I) for p in [
    r"^<media omitted>$", r"^(image|video|audio|sticker|gif|document|contact card) omitted$",
    r"^<attached: .*>$", r"^null$", r"^this message was deleted\.?$", r"^you deleted this message\.?$",
    r"^messages and calls are end-to-end encrypted", r"^missed (voice|video) call", r"^(voice|video) call(, .*)?$",
    r"^.{0,60} (sent|shared) (a|an) (photo|video|attachment|gif|sticker|voice message|link)\.?$",
    r"^you (sent|shared) (a|an) (photo|video|attachment|gif|sticker|voice message|link|location|file|reel|post)\.?$",
    r"^.{0,60} reacted .{1,8} to (your|a) message", r"^reacted .{1,8} to (your|a) message",
    r"^.{0,60} unsent a message\.?$", r"^this message was unsent\.?$",
    r"^.{0,60} (called you|missed a call from you|missed your call)\.?$", r"^you (called|missed a call from) .{0,60}$",
    r"^the (video|audio) (chat|call) ended\.?$",
    # system lines are short and end the sentence: "Mia set the nickname to Mimi."
    r"^.{0,40} (set|changed|cleared) (the|your|their|his|her) (nickname|theme|emoji|chat|group)\b.{0,40}\.?$",
    r"^.{0,60} (named|changed the name of) the (group|chat)\b.{0,40}\.?$",
    r"^.{0,40} (joined|left) the (group|call)\.?$",
    r"^.{0,40} pinned a message\.?$", r"^.{0,40} added .{0,40} to the group\.?$",
    r"^waved at you!?$", r"^liked a message$",
]]
URL_ONLY_RE = re.compile(r"^\s*https?://\S+\s*$")
INVISIBLE_RE = re.compile(r"[‎‏‪-‮⁦-⁩﻿]")
EDITED_RE = re.compile(r"\s*<this message was edited>\s*$", re.I)


def clean(text: str) -> str:
    text = INVISIBLE_RE.sub("", text or "").replace("\r", "")
    text = EDITED_RE.sub("", text).strip()
    if not text or URL_ONLY_RE.match(text) or any(r.search(text) for r in SKIP_RES):
        return ""
    return text[:MAX_TEXT_CHARS]


def fix_facebook_text(s: str) -> str:
    """Facebook/Instagram JSON stores emojis and ñ as broken latin-1 ("Ã±" instead of "ñ").
    Re-reading the bytes as UTF-8 repairs it. If it wasn't broken, keep it as is."""
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


# ------------------------------------------------------------------ WhatsApp (.txt)
# iPhone:   [10/8/26, 10:32:15 PM] Mia: hello
# Android:  10/8/26, 10:32 PM - Mia: hello      (also 08.10.26, 22:32 - Mia: hello)
_WA_LINE = re.compile(
    r"^\[?(?P<a>\d{1,4})[/.\-](?P<b>\d{1,2})[/.\-](?P<c>\d{2,4}),?\s+"
    r"(?P<h>\d{1,2})[:.](?P<mi>\d{2})(?::\d{2})?\s*(?P<ap>[aApP]\.?\s?[mM]\.?)?\]?\s*[-–~]?\s*"
    r"(?P<rest>.*)$")
_WA_SENDER = re.compile(r"^(?P<name>[^:]{1,60}):\s?(?P<msg>.*)$")


def _wa_day_first(lines: list[re.Match]) -> bool:
    """Is the date day/month or month/day? Look for a part bigger than 12."""
    for m in lines:
        a, b = int(m["a"]), int(m["b"])
        if len(m["a"]) == 4:
            return False  # 2026-10-08 style: year first, month second
        if a > 12:
            return True
        if b > 12:
            return False
    return False  # can't tell: assume month/day (what PH/US phones use)


def _wa_timestamp(m: re.Match, day_first: bool) -> float | None:
    try:
        a, b, c = int(m["a"]), int(m["b"]), int(m["c"])
        if len(m["a"]) == 4:
            year, month, day = a, b, c
        else:
            day, month = (a, b) if day_first else (b, a)
            year = c + 2000 if c < 100 else c
        hour = int(m["h"]) % 24
        ap = (m["ap"] or "").lower().replace(".", "").replace(" ", "")
        if ap == "pm" and hour < 12:
            hour += 12
        if ap == "am" and hour == 12:
            hour = 0
        return datetime(year, month, day, hour, int(m["mi"])).timestamp()
    except (ValueError, TypeError):
        return None


def read_whatsapp_txt(text: str, title: str = "WhatsApp chat") -> list[Conversation]:
    text = INVISIBLE_RE.sub("", text)
    entries: list[list] = []   # [match, sender, text]
    for raw in text.splitlines():
        m = _WA_LINE.match(raw.strip())
        sm = _WA_SENDER.match(m["rest"]) if m else None
        if m and sm:
            entries.append([m, sm["name"].strip(), sm["msg"]])
        elif m:
            entries.append([m, None, ""])            # system line ("X joined", security notice...)
        elif entries and raw.strip():
            entries[-1][2] += "\n" + raw.strip()     # a message that spans several lines
    if not any(e[1] for e in entries):
        return []
    day_first = _wa_day_first([e[0] for e in entries])
    msgs = []
    for m, sender, body in entries:
        body = clean(body)
        if sender and body:
            msgs.append(Msg(sender, body, _wa_timestamp(m, day_first)))
    return [Conversation("whatsapp-0", title, "whatsapp", msgs)] if msgs else []


# ------------------------------------------------------------------ Messenger / Instagram (.json)
def _looks_facebook(data) -> bool:
    return isinstance(data, dict) and isinstance(data.get("messages"), list) and \
        any(isinstance(m, dict) and "sender_name" in m for m in data["messages"][:20])


def read_facebook_json(data: dict, cid: str, platform: str) -> Conversation:
    title = fix_facebook_text(data.get("title") or "Chat")
    msgs = []
    for m in data["messages"]:
        if not isinstance(m, dict) or m.get("is_unsent"):
            continue
        body = clean(fix_facebook_text(m.get("content") or ""))
        if body:
            ts = m.get("timestamp_ms")
            msgs.append(Msg(fix_facebook_text(m.get("sender_name") or "?"), body, ts / 1000 if ts else None))
    return Conversation(cid, title, platform, msgs)


# ------------------------------------------------------------------ Telegram (result.json)
def _telegram_text(t) -> str:
    if isinstance(t, str):
        return t
    if isinstance(t, list):  # mixed plain strings and {"type": "bold", "text": "..."} pieces
        return "".join(p if isinstance(p, str) else str(p.get("text", "")) for p in t)
    return ""


def _looks_telegram_chat(data) -> bool:
    return isinstance(data, dict) and isinstance(data.get("messages"), list) and \
        any(isinstance(m, dict) and m.get("type") == "message" and "from" in m for m in data["messages"][:50])


def read_telegram_chat(data: dict, cid: str) -> Conversation:
    msgs = []
    for m in data["messages"]:
        if not isinstance(m, dict) or m.get("type") != "message" or m.get("media_type") or m.get("photo"):
            continue
        body = clean(_telegram_text(m.get("text")))
        if body and m.get("from"):
            ts = m.get("date_unixtime")
            msgs.append(Msg(str(m["from"]), body, float(ts) if ts else None))
    return Conversation(cid, data.get("name") or "Telegram chat", "telegram", msgs)


# ------------------------------------------------------------------ any other JSON chat
# Newer Messenger "secure storage" downloads, Discord exports and similar use other key names for the
# same things. Rather than one reader per app, look for a list of messages that each have a sender and
# a text, whatever the keys are called.
SENDER_KEYS = ("sender_name", "senderName", "sender", "from", "author", "user", "name", "displayName")
TEXT_KEYS = ("content", "text", "body", "message", "msg")
TIME_KEYS = ("timestamp_ms", "timestampMs", "timestamp", "date_unixtime", "date", "time", "created_at", "createdAt", "sent_at")
TITLE_KEYS = ("title", "threadName", "thread_name", "name", "chat_name", "conversation")


def _first(d: dict, keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _sender_of(m: dict) -> str | None:
    v = _first(m, SENDER_KEYS)
    if isinstance(v, dict):   # Discord: {"author": {"name": "...", "nickname": "..."}}
        v = v.get("nickname") or v.get("name") or v.get("username") or v.get("displayName")
    return str(v).strip() if v not in (None, "") else None


def _text_of(m: dict) -> str:
    v = _first(m, TEXT_KEYS)
    if isinstance(v, list):   # Telegram-style rich text pieces
        return _telegram_text(v)
    return v if isinstance(v, str) else ""


def _time_of(m: dict) -> float | None:
    v = _first(m, TIME_KEYS)
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        v = float(v)
        return v / 1000 if v > 1e11 else v          # milliseconds or seconds
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _message_list(data) -> list | None:
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data
    if isinstance(data, dict):
        for k in ("messages", "message", "chat", "items", "data"):
            v = data.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return None


def _looks_generic(data) -> bool:
    msgs = _message_list(data)
    if not msgs:
        return False
    sample = [m for m in msgs[:50] if isinstance(m, dict)]
    good = sum(1 for m in sample if _sender_of(m) and _text_of(m))
    return good >= max(1, len(sample) // 3)


def read_generic_json(data, cid: str, platform: str) -> Conversation:
    msgs = []
    for m in _message_list(data) or []:
        if not isinstance(m, dict) or m.get("is_unsent") or m.get("isUnsent"):
            continue
        sender, body = _sender_of(m), clean(fix_facebook_text(_text_of(m)))
        if sender and body:
            msgs.append(Msg(fix_facebook_text(sender), body, _time_of(m)))
    title = ""
    if isinstance(data, dict):
        t = _first(data, TITLE_KEYS)
        if isinstance(t, dict):
            t = t.get("name")
        if not t and isinstance(data.get("channel"), dict):
            t = data["channel"].get("name")
        if not t and isinstance(data.get("participants"), list):
            t = ", ".join(str(p.get("name") if isinstance(p, dict) else p) for p in data["participants"][:3])
        title = fix_facebook_text(str(t or ""))
    if isinstance(data, dict) and isinstance(data.get("guild"), dict):
        platform = "discord"
    return Conversation(cid, title or "Chat", platform, msgs)


# ------------------------------------------------------------------ HTML downloads
class _HTMLChat(HTMLParser):
    """Reads the HTML exports of Telegram Desktop (its default format) and of Facebook/Instagram
    "Download your information" (when HTML was picked instead of JSON).

    Telegram:   <div class="message default"> <div class="date details" title="05.01.2024 22:15:03">
                <div class="from_name">Name</div> <div class="text">hello</div>   ("joined" = same sender)
    Facebook:   <div class="... _a6-g ..."> <div class="_a6-h">Name</div> <div class="_a6-p">hello</div>
                <div class="_a6-o">Jan 05, 2024 10:15:03 pm</div>"""
    FIELDS = {"from_name": "sender", "text": "text", "_a6-h": "sender", "_a6-p": "text", "_a6-o": "date"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.items, self.cur = "", [], None
        self.depth = 0            # div nesting
        self.msg_depth = None     # depth of the current message container
        self.field = None         # (name, depth) being collected
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag == "title":
            self.in_title = True
        if tag == "br" and self.field and self.cur is not None:
            self.cur[self.field[0]] = self.cur.get(self.field[0], "") + "\n"
        if tag != "div":
            return
        self.depth += 1
        if ("message" in cls and "default" in cls) or "_a6-g" in cls:
            self.cur = {"joined": "joined" in cls}
            self.items.append(self.cur)
            self.msg_depth = self.depth
            return
        if self.cur is None:
            return
        if "date" in cls and "details" in cls and a.get("title"):
            self.cur["date"] = a["title"]
        for c in cls:
            if c in self.FIELDS and self.field is None and not self.cur.get(self.FIELDS[c]):
                self.field = (self.FIELDS[c], self.depth)
                self.cur.setdefault(self.FIELDS[c], "")
                if self.FIELDS[c] == "text" and "media_wrap" in cls:
                    self.field = None

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag != "div":
            return
        if self.field and self.field[1] == self.depth:
            self.field = None
        if self.msg_depth == self.depth:
            self.cur, self.msg_depth = None, None
        self.depth -= 1

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif self.field and self.cur is not None:
            self.cur[self.field[0]] += data


_HTML_DATES = ("%d.%m.%Y %H:%M:%S", "%b %d, %Y %I:%M:%S %p", "%b %d, %Y %I:%M %p", "%b %d, %Y, %I:%M %p", "%d %b %Y, %H:%M")


def _html_time(s: str | None) -> float | None:
    if not s:
        return None
    s = re.sub(r"\s*UTC[+-]\d{2}:\d{2}$", "", s.strip())
    s = re.sub(r"(?i)(\d)\s*([ap]m)$", lambda m: f"{m[1]} {m[2].upper()}", s)
    for fmt in _HTML_DATES:
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return None


def read_html_chat(html: str, cid: str, path: str = "") -> Conversation | None:
    if 'class="message default' not in html and "_a6-g" not in html:
        return None
    p = _HTMLChat()
    p.feed(html)
    telegram = 'class="message default' in html
    platform = "telegram" if telegram else _platform_from_path(path)
    msgs, last_sender = [], None
    for it in p.items:
        sender = (it.get("sender") or "").strip() or (last_sender if (telegram and it.get("joined")) else None)
        body = clean(it.get("text", "").strip())
        if sender:
            last_sender = sender
        if sender and body:
            msgs.append(Msg(sender, body, _html_time(it.get("date"))))
    title = re.sub(r"\s+", " ", p.title).strip() or "Chat"
    return Conversation(cid, title, platform, msgs)


# ------------------------------------------------------------------ LINE (.txt)
# 2024/01/05(Fri)          or  Fri, 01/05/2024
# 22:15<TAB>Lawrence<TAB>hello  or  10:15 PM<TAB>Lawrence<TAB>hello
_LINE_DATE = re.compile(r"^(?:\w{3},\s*)?(?:(?P<y>\d{4})[/.-](?P<m>\d{1,2})[/.-](?P<d>\d{1,2})|(?P<m2>\d{1,2})/(?P<d2>\d{1,2})/(?P<y2>\d{4}))")
_LINE_MSG = re.compile(r"^(?P<h>\d{1,2}):(?P<mi>\d{2})\s?(?P<ap>[AaPp][Mm])?\t(?P<name>[^\t]+)\t(?P<msg>.*)$")


def read_line_txt(text: str, title: str = "LINE chat") -> list[Conversation]:
    if "\t" not in text:
        return []
    day = None
    msgs: list[Msg] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        m = _LINE_MSG.match(line)
        if m:
            ts = None
            if day:
                hour = int(m["h"]) % 12 + (12 if (m["ap"] or "").lower() == "pm" else 0) if m["ap"] else int(m["h"])
                try:
                    ts = datetime(*day, hour, int(m["mi"])).timestamp()
                except ValueError:
                    ts = None
            body = clean(m["msg"].strip('"'))
            if body:
                msgs.append(Msg(m["name"].strip(), body, ts))
            continue
        d = _LINE_DATE.match(line.strip())
        if d and len(line.strip()) <= 24:
            y, mo, dd = (d["y"], d["m"], d["d"]) if d["y"] else (d["y2"], d["m2"], d["d2"])
            day = (int(y), int(mo), int(dd))
        elif msgs and line.strip() and not line.startswith("[LINE]") and not line.lower().startswith("saved on"):
            msgs[-1].text = (msgs[-1].text + "\n" + line.strip())[:MAX_TEXT_CHARS]
    if len(msgs) < 2:
        return []
    m = re.search(r"\[LINE\]\s*(?:Chat history (?:with|in)\s*)?(.+)", text[:200])
    return [Conversation("line-0", (m.group(1).strip() if m else title), "line", msgs)]


# ------------------------------------------------------------------ dispatch
def _from_json(data, name: str, idx: int, platform_hint: str) -> list[Conversation]:
    if _looks_facebook(data):
        return [read_facebook_json(data, f"fb-{idx}", platform_hint)]
    if _looks_telegram_chat(data):
        return [read_telegram_chat(data, f"tg-{idx}")]
    if isinstance(data, dict) and isinstance(data.get("chats"), dict):   # Telegram "export all data"
        chats = [c for c in data["chats"].get("list", []) if _looks_telegram_chat(c)]
        return [read_telegram_chat(c, f"tg-{idx}-{i}") for i, c in enumerate(chats)]
    if _looks_generic(data):                                             # Messenger secure storage, Discord, others
        return [read_generic_json(data, f"fb-{idx}", platform_hint)]
    return []


def _platform_from_path(path: str) -> str:
    p = path.lower()
    return "instagram" if "instagram" in p or "your_instagram_activity" in p else "messenger"


def _merge_parts(convs: list[Conversation]) -> list[Conversation]:
    """Long chats are split into message_1.json, message_2.json (or messages.html, messages2.html...) in one folder."""
    merged: dict[str, Conversation] = {}
    for c in convs:
        key = c.id.rsplit("#", 1)[0]
        if key in merged:
            merged[key].messages.extend(c.messages)
        else:
            c.id = key
            merged[key] = c
    return list(merged.values())


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_one(name: str, raw: bytes, idx: int, title_hint: str, few_files: bool) -> list[Conversation]:
    """Recognise one file by its CONTENT (the extension is only a hint)."""
    low = name.lower()
    head = raw[:4096].lstrip()
    folder = str(PurePosixPath(name).parent)
    if head[:1] in (b"{", b"[") or low.endswith(".json"):
        try:
            data = json.loads(_decode(raw))
        except ValueError:
            data = None                       # e.g. a LINE export starts with "[LINE]": read it as text below
        if data is not None:
            out = _from_json(data, name, idx, _platform_from_path(name))
            for c in out:
                if c.id.startswith("fb-"):
                    c.id = f"fb:{folder}#{idx}"   # same folder = same chat (message_1, message_2...)
            return out
    text = _decode(raw)
    if "<html" in text[:2000].lower() or low.endswith((".html", ".htm")):
        conv = read_html_chat(text, f"html:{folder}#{idx}", name)
        return [conv] if conv and conv.messages else []
    if low.endswith(".txt") or few_files or "chat" in low:
        title = title_hint.replace("WhatsApp Chat - ", "").replace("WhatsApp Chat with ", "")
        convs = read_whatsapp_txt(text, title or "WhatsApp chat")
        for c in convs:
            c.id = f"wa:{name}"
        if convs:
            return convs
        return read_line_txt(text, title or "LINE chat")
    return []


def read_export(filename: str, fileobj) -> list[Conversation]:
    """Read any supported export. `fileobj` is a binary file (seekable)."""
    name = (filename or "").lower()
    head = fileobj.read(4)
    fileobj.seek(0)
    convs: list[Conversation] = []
    html_seen = False

    if head.startswith(b"PK"):   # a .zip file
        try:
            zf = zipfile.ZipFile(fileobj)
        except zipfile.BadZipFile:
            raise ExportError("That .zip file looks damaged. Try downloading the export again.")
        names = [n for n in zf.namelist() if not n.startswith("__MACOSX/") and not n.endswith("/")
                 and not PurePosixPath(n).name.startswith("._")]
        chatty = [n for n in names if n.lower().endswith((".json", ".txt", ".html", ".htm"))]
        title_hint = PurePosixPath(filename or "").stem
        for i, entry in enumerate(chatty):
            low = entry.lower()
            # In a full Facebook/Instagram download only the message folders matter; skip the rest quickly.
            if len(chatty) > 3 and not any(k in low for k in ("message", "inbox", "result", "chat", "conversation")):
                continue
            info = zf.getinfo(entry)
            if info.file_size > MAX_FILE_BYTES:
                continue
            raw = zf.read(entry)
            html_seen = html_seen or low.endswith((".html", ".htm"))
            convs.extend(_read_one(entry, raw, i, title_hint, len(names) <= 3))
        convs = _merge_parts(convs)
    else:
        raw = fileobj.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ExportError("That file is too big. Export just the one chat instead of everything.")
        html_seen = name.endswith((".html", ".htm"))
        convs = _merge_parts(_read_one(filename or "chat", raw, 0, PurePosixPath(filename or "").stem, True))

    convs = [c for c in convs if c.messages]
    if not convs:
        if html_seen:
            raise ExportError("Couldn't read the chats in this HTML download. Download it again and choose JSON as the format.")
        raise ExportError(
            "Couldn't find any chats in that file. Supported: WhatsApp (.txt or .zip), Messenger or Instagram "
            "(the .zip, JSON or HTML), Telegram (result.json or messages.html), LINE (.txt) and Discord (JSON).")
    for c in convs:
        if all(m.ts is not None for m in c.messages):
            c.messages.sort(key=lambda m: m.ts)   # Messenger lists newest first
        elif c.platform in ("messenger", "instagram") and c.id.startswith("html:"):
            c.messages.reverse()                  # Facebook HTML is newest first too
    convs.sort(key=lambda c: -len(c.messages))
    return convs


# ------------------------------------------------------------------ after the user picks who's who
def guess_roles(conv: Conversation, known_names: list[str], owner_hint: str | None = None) -> tuple[str | None, str | None]:
    """Best guess at (their_name, your_name) among the senders. The user can change both."""
    senders = [p["name"] for p in conv.participants()]
    known = [k.casefold().strip() for k in known_names if k and k.strip()]

    def matches(sender: str) -> bool:
        s = sender.casefold()
        return any(k == s or k in s.split() or s.startswith(k) for k in known)

    person = next((s for s in senders if matches(s)), None)
    me = owner_hint if owner_hint in senders and owner_hint != person else None
    if person is None and me:
        person = next((s for s in senders if s != me), None)
    if me is None and person:
        me = next((s for s in senders if s != person), None)
    return person, me


def owner_name(convs: list[Conversation]) -> str | None:
    """In a full Messenger/Instagram download you are in every chat, so the sender who
    appears in the most chats is probably you."""
    if len(convs) < 2:
        return None
    seen: dict[str, int] = {}
    for c in convs:
        for p in {m.sender for m in c.messages}:
            seen[p] = seen.get(p, 0) + 1
    best = max(seen.items(), key=lambda kv: kv[1])
    return best[0] if best[1] >= 2 else None


def split_sessions(msgs: list[dict], gap_hours: float = SESSION_GAP_HOURS,
                   max_len: int = MAX_SESSION_MESSAGES) -> list[list[dict]]:
    """Cut a long chat into separate talks: after `gap_hours` of silence, or every `max_len` messages."""
    sessions: list[list[dict]] = []
    last_ts = None
    for m in msgs:
        ts = m.get("ts")
        new_talk = not sessions or len(sessions[-1]) >= max_len or \
            (ts is not None and last_ts is not None and ts - last_ts > gap_hours * 3600)
        if new_talk:
            sessions.append([])
        sessions[-1].append(m)
        last_ts = ts if ts is not None else last_ts
    return sessions
