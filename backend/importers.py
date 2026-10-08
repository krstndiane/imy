"""importers.py - read chat EXPORT files (the legal, official way to get your own chats).

Every big app lets you download your own messages. This file reads those downloads:

  WhatsApp          Chat > (name) > Export chat > Without media   -> a .txt, or a .zip with _chat.txt
  Messenger         facebook.com/dyi > Messages > Format: JSON    -> a .zip with .../inbox/<name>/message_1.json
  Instagram         Accounts Center > Download your information > Messages > JSON (same format as Messenger)
  Telegram Desktop  Chat > ⋮ > Export chat history > Format: JSON -> result.json

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
from pathlib import PurePosixPath

SESSION_GAP_HOURS = 6       # silence longer than this = a new talk
MAX_SESSION_MESSAGES = 120  # very long talks are split so each item stays reviewable
MAX_TEXT_CHARS = 2000


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
    r"^you (sent|shared) (a|an) .{1,20}\.?$", r"^.{0,60} reacted .{1,8} to (your|a) message",
    r"^reacted .{1,8} to (your|a) message", r"^.{0,60} unsent a message\.?$", r"^this message was unsent\.?$",
    r"^.{0,60} (called you|missed a call from you|missed your call)\.?$", r"^you (called|missed a call from) .{0,60}$",
    r"^the (video|audio) (chat|call) ended\.?$", r"^.{0,60} (set|changed|cleared) (the|your|their|his|her) (nickname|theme|emoji|chat|group)",
    r"^.{0,60} (named|changed the name of) the (group|chat)", r"^.{0,60} (joined|left) the (group|call)",
    r"^.{0,60} pinned a message", r"^.{0,60} added .{0,60} to the group", r"^waved at you!?$", r"^liked a message$",
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


# ------------------------------------------------------------------ dispatch
def _from_json(data, name: str, idx: int, platform_hint: str) -> list[Conversation]:
    if _looks_facebook(data):
        return [read_facebook_json(data, f"fb-{idx}", platform_hint)]
    if _looks_telegram_chat(data):
        return [read_telegram_chat(data, f"tg-{idx}")]
    if isinstance(data, dict) and isinstance(data.get("chats"), dict):   # Telegram "export all data"
        chats = [c for c in data["chats"].get("list", []) if _looks_telegram_chat(c)]
        return [read_telegram_chat(c, f"tg-{idx}-{i}") for i, c in enumerate(chats)]
    return []


def _platform_from_path(path: str) -> str:
    p = path.lower()
    return "instagram" if "instagram" in p or "your_instagram_activity" in p else "messenger"


def _merge_parts(convs: list[Conversation]) -> list[Conversation]:
    """Long Messenger chats are split into message_1.json, message_2.json... in one folder."""
    merged: dict[str, Conversation] = {}
    for c in convs:
        key = c.id.rsplit("#", 1)[0]
        if key in merged:
            merged[key].messages.extend(c.messages)
        else:
            c.id = key
            merged[key] = c
    return list(merged.values())


def read_export(filename: str, fileobj) -> list[Conversation]:
    """Read any supported export. `fileobj` is a binary file (seekable)."""
    name = (filename or "").lower()
    head = fileobj.read(4)
    fileobj.seek(0)
    convs: list[Conversation] = []

    if head.startswith(b"PK"):   # a .zip file
        try:
            zf = zipfile.ZipFile(fileobj)
        except zipfile.BadZipFile:
            raise ExportError("That .zip file looks damaged. Try downloading the export again.")
        names = [n for n in zf.namelist() if not n.startswith("__MACOSX/") and not n.endswith("/")]
        for i, entry in enumerate(names):
            low = entry.lower()
            if low.endswith(".json") and ("message" in low or "result" in low or "inbox" in low):
                try:
                    data = json.loads(zf.read(entry))
                except (ValueError, UnicodeDecodeError):
                    continue
                folder = str(PurePosixPath(entry).parent)
                for c in _from_json(data, entry, i, _platform_from_path(entry)):
                    if c.id.startswith("fb-"):
                        c.id = f"fb:{folder}#{i}"   # same folder = same chat (message_1, message_2...)
                    convs.append(c)
            elif low.endswith(".txt") and ("chat" in low or len(names) <= 3):
                text = zf.read(entry).decode("utf-8", errors="replace")
                title = PurePosixPath(filename).stem.replace("WhatsApp Chat - ", "").replace("WhatsApp Chat with ", "")
                for c in read_whatsapp_txt(text, title or "WhatsApp chat"):
                    c.id = f"wa:{entry}"
                    convs.append(c)
        if not names or not convs:
            if any(n.lower().endswith(".html") for n in names):
                raise ExportError("This export is in HTML. Download it again and choose JSON as the format.")
        convs = _merge_parts(convs)
    elif name.endswith(".json") or head.lstrip()[:1] in (b"{", b"["):
        try:
            data = json.load(io.TextIOWrapper(fileobj, encoding="utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            raise ExportError("That .json file couldn't be read.")
        convs = _from_json(data, filename, 0, _platform_from_path(name))
    elif name.endswith(".txt"):
        text = fileobj.read().decode("utf-8-sig", errors="replace")
        convs = read_whatsapp_txt(text, PurePosixPath(filename).stem.replace("WhatsApp Chat with ", ""))
    elif name.endswith(".html"):
        raise ExportError("This export is in HTML. Download it again and choose JSON as the format.")

    convs = [c for c in convs if c.messages]
    if not convs:
        raise ExportError(
            "Couldn't find any chats in that file. Supported: a WhatsApp export (.txt or .zip), "
            "a Messenger or Instagram download in JSON (.zip or message_1.json), or Telegram's result.json.")
    for c in convs:
        if all(m.ts is not None for m in c.messages):
            c.messages.sort(key=lambda m: m.ts)   # Messenger lists newest first
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
