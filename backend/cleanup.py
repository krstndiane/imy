"""cleanup.py - re-apply the parser's text cleaning to messages that are already stored.

Run after improving parser.clean_text / CHROME_PATTERNS, so old data benefits too:

    python -m backend.cleanup

A copy of the database is saved next to it first (imy.backup-<timestamp>.db).
"""
import json
import shutil
from datetime import datetime

from . import parser
from .db import DB_PATH, bump_version, db, init_db


def run() -> dict:
    init_db()
    backup = DB_PATH.with_name(f"imy.backup-{datetime.now():%Y%m%d-%H%M%S}.db")
    shutil.copy2(DB_PATH, backup)
    stats = {"fixed": 0, "removed": 0, "backup": str(backup)}
    with db() as conn:
        for person in conn.execute("SELECT id, answers FROM persons").fetchall():
            answers = json.loads(person["answers"])
            names = [answers.get("q1", ""), answers.get("q2", "")]
            rows = conn.execute(
                "SELECT m.id, m.text, m.position, s.stored_path FROM messages m JOIN screenshots s ON s.id = m.screenshot_id "
                "WHERE m.person_id = ? ORDER BY m.screenshot_id, m.position", (person["id"],)).fetchall()
            for m in rows:
                text = parser.clean_text(m["text"])
                from_image = m["stored_path"] is not None   # pasted text has no app chrome in it
                header = from_image and m["position"] < 2 and parser.is_contact_header(text, names)
                if not text or header or (from_image and parser.is_chrome(text)):
                    conn.execute("DELETE FROM messages WHERE id = ?", (m["id"],))
                    stats["removed"] += 1
                elif text != m["text"]:
                    conn.execute("UPDATE messages SET text = ? WHERE id = ?", (text, m["id"]))
                    stats["fixed"] += 1
            for c in conn.execute("SELECT id, text FROM chat_messages WHERE person_id = ? AND role = 'person'", (person["id"],)).fetchall():
                text = parser.clean_text(c["text"])
                if text and text != c["text"]:
                    conn.execute("UPDATE chat_messages SET text = ? WHERE id = ?", (text, c["id"]))
            bump_version(conn, person["id"])
    return stats


if __name__ == "__main__":
    print(run())
