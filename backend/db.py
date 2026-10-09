"""db.py - SQLite storage.

Four tables:
  persons       one row per person you've set up (+ their interview answers as JSON)
  screenshots   one row per uploaded screenshot (or pasted text import)
  messages      every extracted message, labelled speaker = 'user' or 'person'
  chat_messages the live conversation history in Chat Mode

`persons.data_version` goes up every time the person's training data changes.
The engine uses it to know when its cached index is stale.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("IMY_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "imy.db"
UPLOAD_DIR = DATA_DIR / "uploads"

SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  answers TEXT NOT NULL DEFAULT '{}',
  use_llm INTEGER NOT NULL DEFAULT 0,
  data_version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS screenshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  filename TEXT NOT NULL,
  stored_path TEXT,
  platform TEXT NOT NULL DEFAULT 'unknown',
  flipped INTEGER NOT NULL DEFAULT 0,
  raw_text TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  screenshot_id INTEGER NOT NULL REFERENCES screenshots(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  speaker TEXT NOT NULL CHECK (speaker IN ('user', 'person')),
  text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user', 'person')),
  text TEXT NOT NULL,
  debug TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_person ON messages(person_id, screenshot_id, position);
CREATE INDEX IF NOT EXISTS idx_chat_person ON chat_messages(person_id, id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Columns added after the first version. Old databases get them on start-up.
MIGRATIONS = [
    ("persons", "platform", "TEXT NOT NULL DEFAULT ''"),   # the app this chat should look like
    ("persons", "look", "TEXT NOT NULL DEFAULT '{}'"),     # look settings: {"mode": "auto|light|dark", "colors": true}
    ("screenshots", "meta", "TEXT NOT NULL DEFAULT '{}'"), # what the reader noticed: app, dark/light, colours, chat name
    ("screenshots", "sha1", "TEXT"),                       # fingerprint of the image, to skip exact repeats
]


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute("PRAGMA journal_mode = WAL")   # readers never wait for a writer (and vice versa)
        conn.executescript(SCHEMA)
        for table, column, decl in MIGRATIONS:
            if column not in [c["name"] for c in conn.execute(f"PRAGMA table_info({table})")]:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shots_person ON screenshots(person_id, id)")


@contextmanager
def db():
    """Open a connection, commit on success, roll back on error."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # timeout: when another request is writing, wait for it (up to 30 s) instead of failing with
    # "database is locked", which used to show up as a crash when uploads and chat overlapped.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def bump_version(conn, person_id: int) -> None:
    """Call whenever training data changes so cached indexes get rebuilt."""
    conn.execute(
        "UPDATE persons SET data_version = data_version + 1, updated_at = ? WHERE id = ?",
        (now(), person_id),
    )
