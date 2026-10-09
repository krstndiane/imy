"""db.py - SQLite storage.

Seven tables:
  users         one row per account; `sessions` is how a browser stays signed in (see auth.py)
  sessions      live sign-ins, as hashes of the cookie token
  persons       one row per person you've set up (+ their interview answers as JSON), owned by a user
  screenshots   one row per uploaded screenshot (or pasted text import)
  messages      every extracted message, labelled speaker = 'user' or 'person'
  chat_messages the live conversation history in Chat Mode
  media         photos and files shared inside a chat (what they are is saved too, for the AI)

Everything lives in this one file on disk (data/imy.db), so closing the browser or restarting the
server loses nothing: profile pictures, nicknames, every message and every shared photo are still
there when you come back.

`persons.data_version` goes up every time the person's training data changes.
The engine uses it to know when its cached index is stale.
"""
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("IMY_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "imy.db"
UPLOAD_DIR = DATA_DIR / "uploads"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,          -- scrypt; the password itself is never stored (see auth.py)
  is_adult INTEGER NOT NULL DEFAULT 0,  -- confirmed 18+ at sign-up
  created_at TEXT NOT NULL,
  last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,          -- only the hash: a stolen database can't sign anyone in
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  user_agent TEXT NOT NULL DEFAULT ''
);
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
CREATE TABLE IF NOT EXISTS media (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  stored_path TEXT NOT NULL,
  filename TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'file',      -- image | video | audio | file
  mime TEXT NOT NULL DEFAULT '',
  bytes INTEGER NOT NULL DEFAULT 0,
  width INTEGER, height INTEGER,
  description TEXT NOT NULL DEFAULT '',   -- what's in it, in words, so the reply can be about it
  sender TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_person ON messages(person_id, screenshot_id, position);
CREATE INDEX IF NOT EXISTS idx_chat_person ON chat_messages(person_id, id);
CREATE INDEX IF NOT EXISTS idx_media_person ON media(person_id, id);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Columns added after the first version. Old databases get them on start-up.
MIGRATIONS = [
    ("persons", "platform", "TEXT NOT NULL DEFAULT ''"),   # the app this chat should look like
    ("persons", "look", "TEXT NOT NULL DEFAULT '{}'"),     # look settings: {"mode": "auto|light|dark", "colors": true}
    ("persons", "avatar", "TEXT"),                         # their profile picture (a file in data/uploads)
    ("persons", "my_avatar", "TEXT"),                      # your profile picture in this chat
    ("persons", "nick_them", "TEXT NOT NULL DEFAULT ''"),  # nickname you gave them, like Messenger
    ("persons", "nick_me", "TEXT NOT NULL DEFAULT ''"),    # nickname they call you
    ("persons", "theme", "TEXT NOT NULL DEFAULT '{}'"),    # chat theme: {"color": "#...", "emoji": "👍"}
    ("persons", "memory", "TEXT NOT NULL DEFAULT ''"),     # notes from your chats, so they remember what you told them
    ("screenshots", "meta", "TEXT NOT NULL DEFAULT '{}'"), # what the reader noticed: app, dark/light, colours, chat name
    ("screenshots", "sha1", "TEXT"),                       # fingerprint of the image, to skip exact repeats
    ("chat_messages", "kind", "TEXT NOT NULL DEFAULT 'text'"),  # text | media | event (a note like "You set the nickname…")
    ("chat_messages", "media_id", "INTEGER"),              # the photo or file this message carries
    ("chat_messages", "reply_to", "INTEGER"),              # the message this one is replying to
    ("chat_messages", "reactions", "TEXT NOT NULL DEFAULT '{}'"),  # {"user": "❤️", "person": "😂"}
    ("chat_messages", "deleted", "INTEGER NOT NULL DEFAULT 0"),    # unsent: kept as "You unsent a message"
    # Who this person belongs to. Empty in copies of imy from before accounts existed: the first
    # account created adopts them (see auth.create_user), so nothing is lost when you upgrade.
    ("persons", "user_id", "INTEGER REFERENCES users(id) ON DELETE CASCADE"),
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_persons_user ON persons(user_id)")


# ---------------------------------------------------------------- one writer at a time, fairly
# SQLite lets any number of people read at once, but only one write at a time. Left to itself it
# settles that with a scramble: whoever loses sleeps a moment and tries again. With a lot of people
# writing at once, some keep losing - under load a one-line nickname change was taking four seconds
# while the typical one took twenty milliseconds.
#
# So imy forms a queue instead of a scramble. The first write inside a `with db()` block takes this
# lock and keeps it until that block finishes. Reads never touch it, so they stay as parallel as
# ever. Writes take well under a millisecond each, so the queue stays short.
#
# This only orders the writes inside one running copy of imy. Run a second copy against the same
# file (several web workers, or the backup script) and they fall back to SQLite's own waiting, which
# is what `busy_timeout` below is for.
_write_lock = threading.RLock()
_WRITES = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP")


class _Connection(sqlite3.Connection):
    """A connection that joins the write queue the moment it actually writes something."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._queued = False

    def _queue_if_writing(self, sql: str) -> None:
        if self._queued:
            return
        first = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        if first in _WRITES:
            _write_lock.acquire()
            self._queued = True        # released by db() when the block ends

    def _done_writing(self) -> None:
        if self._queued:
            self._queued = False
            _write_lock.release()

    def execute(self, sql, parameters=()):
        self._queue_if_writing(sql)
        return super().execute(sql, parameters)

    def executemany(self, sql, parameters):
        self._queue_if_writing(sql)
        return super().executemany(sql, parameters)

    def executescript(self, sql):
        self._queue_if_writing(sql)
        return super().executescript(sql)


@contextmanager
def db():
    """Open a connection, commit on success, roll back on error."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # timeout: when another program is writing, wait for it (up to 30 s) instead of failing with
    # "database is locked", which used to show up as a crash when uploads and chat overlapped.
    conn = sqlite3.connect(DB_PATH, timeout=30, factory=_Connection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    # synchronous: with WAL, NORMAL still can't corrupt the database - the worst a sudden power cut
    # can cost is the last moment of writes. FULL waits for the disk on every single commit, which
    # measured 30x slower, and that wait is what the queue above would otherwise be waiting on.
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            conn.close()
        finally:
            conn._done_writing()


def bump_version(conn, person_id: int) -> None:
    """Call whenever training data changes so cached indexes get rebuilt."""
    conn.execute(
        "UPDATE persons SET data_version = data_version + 1, updated_at = ? WHERE id = ?",
        (now(), person_id),
    )
