"""account_export.py - "Download my data": everything imy holds about one account, as a zip.

People have the right to a copy of what a site keeps about them (the Philippines' Data Privacy Act,
the GDPR, and similar laws), so the account menu has a "Download my data" button that calls this.

What goes in (all of it belongs to the signed-in account and nobody else's):
  account.json      the email, when it was made, which Terms it agreed to, the AI choice
  activity.json     the days the account was used, and its daily AI counts
  feedback.json     notes sent from the app
  people/<id>-<name>/
      person.json            the name, nicknames, chat look, interview answers and memory notes
      conversation.txt/.json the chat in imy, readable and as data
      imported_messages.json what was read from the screenshots, pasted text and chat exports
      screenshots.json, shared_files.json   what each file is
      files/                 the screenshots, shared photos and profile pictures themselves

What stays out: the password (only a scrambled copy exists, so there is nothing readable to give),
sign-in tokens, and imy's own internal scoring notes on its replies.

Only the standard library is used on purpose, so this is easy to read and to test. The zip is
written to a temporary file in the data folder (a long chat history with many photos would be
too big to hold in memory); main.py sends it and then deletes it.
"""
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path

from .db import DATA_DIR, UPLOAD_DIR, db, now

ROOT = "imy-my-data"
# Photos, video and zips are already compressed, so squeezing them again only wastes time.
ALREADY_COMPRESSED = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".mp4", ".webm", ".mov",
                      ".m4a", ".mp3", ".ogg", ".wav", ".zip", ".pdf"}


def build(user_id: int) -> Path:
    """Write the zip for this account and return where it is. The caller deletes it after sending."""
    folder = DATA_DIR / "tmp"
    folder.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="export-", suffix=".zip", dir=folder)
    os.close(fd)
    path = Path(name)
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            _fill(z, user_id)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


# ---------------------------------------------------------------- small helpers
def _safe(name: str, fallback: str = "file") -> str:
    """A name that is safe inside a zip and on any computer: letters, digits, dot, dash, space."""
    cleaned = re.sub(r"[^\w.\- ]+", "_", name or "", flags=re.UNICODE).strip(" .")[:80]
    return cleaned or fallback


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def _put(z: zipfile.ZipFile, arcname: str, data) -> None:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2)
    z.writestr(arcname, text)


def _stamp(iso: str | None) -> str:
    """2026-10-12T21:03:44+00:00 -> 2026-10-12 21:03 (UTC)"""
    return (iso or "")[:16].replace("T", " ")


class _Files:
    """Adds an uploaded file to the zip, but only if it really lives in this person's own folder."""

    def __init__(self, z: zipfile.ZipFile, person_id: int, folder: str):
        self.z, self.folder, self.missing = z, folder, []
        self.base = (UPLOAD_DIR / str(person_id)).resolve()

    def add(self, stored_path: str | None, where: str, row_id, original_name: str) -> str | None:
        if not stored_path:
            return None
        target = (UPLOAD_DIR / stored_path).resolve()
        if self.base not in target.parents or not target.is_file():
            self.missing.append(stored_path)
            return None
        name = _safe(original_name, "file")
        if not Path(name).suffix:
            name += target.suffix
        arc = f"{self.folder}/files/{where}/{row_id}-{name}"
        kind = zipfile.ZIP_STORED if target.suffix.lower() in ALREADY_COMPRESSED else zipfile.ZIP_DEFLATED
        self.z.write(target, arc, compress_type=kind)
        return arc


# ---------------------------------------------------------------- the zip itself
def _fill(z: zipfile.ZipFile, uid: int) -> None:
    exported = now()
    missing_total = 0
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not row:
            raise ValueError("No such account.")
        u = dict(row)
        _put(z, f"{ROOT}/account.json", {
            "email": u["email"],
            "account_created": u["created_at"],
            "last_seen": u.get("last_seen"),
            "confirmed_18_or_over": bool(u.get("is_adult")),
            "agreed_to_terms_version": u.get("terms_version") or None,
            "agreed_to_terms_at": u.get("terms_at"),
            "time_zone": u.get("tz"),
            "outside_ai_helps_write_replies": bool(u.get("cloud_ai", 1)),
            "exported_at": exported,
        })
        _put(z, f"{ROOT}/activity.json", {
            "days_used": [r["day"] for r in conn.execute(
                "SELECT day FROM active_days WHERE user_id = ? ORDER BY day", (uid,))],
            "ai_use_per_day": [{"day": r["day"], "ai_replies_and_photo_looks": r["seen"], "background_notes": r["other"]}
                               for r in conn.execute("SELECT day, seen, other FROM ai_usage WHERE user_id = ? ORDER BY day", (uid,))],
        })
        _put(z, f"{ROOT}/feedback.json", [
            {"sent": r["created_at"], "about": r["kind"], "rating": r["rating"], "message": r["message"],
             "screen": r["page"], "ok_to_email_me": bool(r["can_reply"])}
            for r in conn.execute("SELECT * FROM feedback WHERE user_id = ? ORDER BY id", (uid,))])

        people = [dict(r) for r in conn.execute("SELECT * FROM persons WHERE user_id = ? ORDER BY id", (uid,))]
        for p in people:
            missing_total += _one_person(z, conn, p)

    _put(z, f"{ROOT}/README.txt", _readme(u["email"], exported, len(people), missing_total))


def _one_person(z: zipfile.ZipFile, conn, p: dict) -> int:
    pid, name = p["id"], p["name"]
    folder = f"{ROOT}/people/{pid}-{_safe(name, 'person')}"
    files = _Files(z, pid, folder)

    shots = [dict(r) for r in conn.execute("SELECT * FROM screenshots WHERE person_id = ? ORDER BY id", (pid,))]
    imported = [dict(r) for r in conn.execute(
        "SELECT * FROM messages WHERE person_id = ? ORDER BY screenshot_id, position", (pid,))]
    chat = [dict(r) for r in conn.execute("SELECT * FROM chat_messages WHERE person_id = ? ORDER BY id", (pid,))]
    media = [dict(r) for r in conn.execute("SELECT * FROM media WHERE person_id = ? ORDER BY id", (pid,))]

    # Profile pictures first (they aren't rows of their own).
    avatars = {"their_picture": files.add(p.get("avatar"), "profile-pictures", "them", "their-picture"),
               "your_picture": files.add(p.get("my_avatar"), "profile-pictures", "you", "your-picture")}

    _put(z, f"{folder}/person.json", {
        "name": name,
        "what_you_call_them": p.get("nick_them") or None,
        "what_they_call_you": p.get("nick_me") or None,
        "chat_app_it_looks_like": p.get("platform") or None,
        "chat_look": _loads(p.get("look"), {}),
        "chat_theme": _loads(p.get("theme"), {}),
        "interview_answers": _loads(p.get("answers"), {}),
        "memory_notes": p.get("memory") or "",
        "local_model_switch_on": bool(p.get("use_llm")),
        "added": p.get("created_at"),
        "updated": p.get("updated_at"),
        "confirmed_these_were_their_own_chats_at": p.get("consent_at"),
        "profile_pictures": {k: v for k, v in avatars.items() if v},
    })

    # What was read from screenshots, pasted text and chat export files.
    source_name = {s["id"]: s["filename"] for s in shots}
    _put(z, f"{folder}/imported_messages.json", [
        {"from": source_name.get(m["screenshot_id"]), "position": m["position"],
         "who": "you" if m["speaker"] == "user" else name, "text": m["text"]} for m in imported])
    shot_rows = []
    for s in shots:
        shot_rows.append({
            "id": s["id"], "original_name": s["filename"], "chat_app": s.get("platform"),
            "added": s.get("created_at"), "sides_swapped": bool(s.get("flipped")),
            "text_read_from_it": s.get("raw_text") or "",
            "file": files.add(s.get("stored_path"), "screenshots", s["id"], s["filename"]),
        })
    _put(z, f"{folder}/screenshots.json", shot_rows)

    # Photos and files shared inside the chat.
    media_name = {m["id"]: m["filename"] for m in media}
    _put(z, f"{folder}/shared_files.json", [{
        "id": m["id"], "name": m["filename"], "type": m.get("kind"), "mime": m.get("mime"), "bytes": m.get("bytes"),
        "what_it_shows": m.get("description") or "", "sent_by": "you" if m.get("sender") == "user" else name,
        "when": m.get("created_at"), "file": files.add(m["stored_path"], "shared", m["id"], m["filename"]),
    } for m in media])

    # The conversation, as data and as something you can simply read.
    rows, lines = [], []
    for c in chat:
        who = "You" if c["role"] == "user" else name
        attachment = media_name.get(c.get("media_id")) if c.get("media_id") else None
        rows.append({"id": c["id"], "when": c["created_at"], "who": who, "text": c["text"],
                     "kind": c.get("kind"), "attachment": attachment, "reply_to": c.get("reply_to"),
                     "reactions": _loads(c.get("reactions"), {}), "unsent": bool(c.get("deleted"))})
        extra = (f" [attachment: {attachment}]" if attachment else "") + (" [unsent]" if c.get("deleted") else "")
        lines.append(f"[{_stamp(c['created_at'])} UTC] {who}: {c['text']}{extra}")
    _put(z, f"{folder}/conversation.json", rows)
    _put(z, f"{folder}/conversation.txt", "\n".join(lines) + ("\n" if lines else ""))

    if files.missing:
        _put(z, f"{folder}/files/MISSING.txt",
             "These files are listed in your data but couldn't be found on the server:\n" + "\n".join(files.missing) + "\n")
    return len(files.missing)


def _readme(email: str, exported: str, people: int, missing: int) -> str:
    gaps = f"\nNote: {missing} uploaded file(s) couldn't be found and are listed in MISSING.txt inside the people folders.\n" if missing else ""
    return f"""Your imy data
=============
Account: {email}
Made: {_stamp(exported)} UTC
People: {people}

This is a copy of what imy keeps about your account.

  account.json     your account details and choices
  activity.json    the days you used imy and your daily AI counts
  feedback.json    notes you sent from the app
  people/          one folder per person you set up:
                     person.json, conversation.txt / .json, imported_messages.json,
                     screenshots.json, shared_files.json, and files/ with the pictures themselves

Not included: your password (imy only keeps a scrambled version, so there is nothing readable to
give you), sign-in tokens, and imy's internal notes on how it picked each reply.
{gaps}
To delete all of this from imy: open the account menu in the app and choose "Delete everything".
Questions about your data: see the Privacy page.
"""
