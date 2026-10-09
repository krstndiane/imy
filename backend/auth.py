"""auth.py - accounts, passwords and sign-in sessions.

Until now imy had no idea who was using it: whoever opened the page saw every person in the database.
This file adds accounts, so each person you set up belongs to you and nobody else can see them.

How it works, in plain terms:

  Passwords      are never stored. What's stored is a scrambled version made with `scrypt`, a slow
                 algorithm built for exactly this: even with the database in hand, guessing the
                 password takes an unreasonable amount of time. Each password gets its own random
                 salt, so two people with the same password still look completely different.

  Staying signed in   When you sign in, imy makes a long random token, puts it in a cookie your
                 browser sends back on every request, and keeps only a *hash* of it. So a stolen
                 database still can't be used to sign in as you. Sessions expire after 30 days, and
                 signing out deletes that session on the server - not just in your browser.

  Who is asking  A request carries no name, only that cookie. `load_session` turns it into a user,
                 and the answer is put in `CURRENT` so the rest of the code can ask "whose request
                 is this?" without every single function having to pass it along.

Nothing here is imy-specific; it's the ordinary way a small site does sign-in.
"""
import hashlib
import hmac
import os
import re
import secrets
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

from .db import db, now

# "Who is making this request?" - set for each request by the middleware in main.py.
# A ContextVar keeps one value per request even when several arrive at once.
CURRENT: ContextVar[dict | None] = ContextVar("imy_current_user", default=None)

SESSION_COOKIE = "imy_session"
SESSION_DAYS = 30
MIN_PASSWORD = 8
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")

# scrypt settings. Higher n = slower to guess, but also slower to sign in; 2**14 takes a few
# hundredths of a second and needs ~16 MB, which is the usual balance for a small site.
_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32


class AuthError(Exception):
    """Something the person should be told about ("that email is taken")."""


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${key.hex()}"


def check_password(password: str, stored: str) -> bool:
    """True if the password matches. Always does the full work, so a wrong email and a wrong
    password take the same time (otherwise the timing itself tells an attacker which it was)."""
    try:
        scheme, n, r, p, salt_hex, key_hex = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                             n=int(n), r=int(r), p=int(p), dklen=len(key_hex) // 2)
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(key.hex(), key_hex)


_DUMMY = hash_password("imy-not-a-real-password")


def waste_time() -> None:
    """Called when the email doesn't exist, so signing in takes the same time either way."""
    check_password("x", _DUMMY)


# ---------------------------------------------------------------- accounts
def clean_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise AuthError("That doesn't look like an email address.")
    return email


def check_new_password(password: str) -> str:
    if len(password or "") < MIN_PASSWORD:
        raise AuthError(f"Password needs at least {MIN_PASSWORD} characters.")
    if len(password) > 200:
        raise AuthError("That password is too long.")
    return password


def create_user(email: str, password: str, is_adult: bool) -> dict:
    """Make an account. The first one ever created also adopts anyone already in the database,
    so upgrading an older copy of imy doesn't lose the people you already set up."""
    email = clean_email(email)
    check_new_password(password)
    if not is_adult:
        raise AuthError("imy is for adults only, so you have to confirm you're 18 or over.")
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise AuthError("There's already an account with that email. Sign in instead.")
        first = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, is_adult, created_at, last_seen) VALUES (?,?,1,?,?)",
            (email, hash_password(password), now(), now()))
        uid = cur.lastrowid
        if first:
            conn.execute("UPDATE persons SET user_id = ? WHERE user_id IS NULL", (uid,))
        return dict(conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())


def verify_user(email: str, password: str) -> dict:
    """Check an email and password. Deliberately vague on failure: saying which half was wrong
    tells someone guessing that an account exists."""
    email = (email or "").strip().lower()
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not row:
        waste_time()
        raise AuthError("Wrong email or password.")
    if not check_password(password, row["password_hash"]):
        raise AuthError("Wrong email or password.")
    return dict(row)


def change_password(user_id: int, old: str, new: str) -> None:
    with db() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row or not check_password(old, row["password_hash"]):
        raise AuthError("That isn't your current password.")
    check_new_password(new)
    with db() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new), user_id))
        # Signing in again everywhere else is the point of changing a password.
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


# ---------------------------------------------------------------- staying signed in
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(user_id: int, user_agent: str = "") -> str:
    """Return the token to put in the cookie. Only its hash is kept here."""
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    with db() as conn:
        conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at, user_agent) VALUES (?,?,?,?,?)",
                     (_hash_token(token), user_id, now(), expires, (user_agent or "")[:200]))
        conn.execute("UPDATE users SET last_seen = ? WHERE id = ?", (now(), user_id))
    return token


def load_session(token: str) -> dict | None:
    """The signed-in user for this cookie, or None. Expired sessions are cleaned up as they're found."""
    if not token:
        return None
    th = _hash_token(token)
    with db() as conn:
        row = conn.execute(
            "SELECT u.id, u.email, u.created_at, u.is_adult, s.expires_at FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (th,)).fetchone()
        if not row:
            return None
        if row["expires_at"] <= now():
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (th,))
            return None
        return {"id": row["id"], "email": row["email"], "created_at": row["created_at"],
                "is_adult": bool(row["is_adult"])}


def end_session(token: str) -> None:
    if token:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))


def end_all_sessions(user_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def sweep_sessions() -> None:
    """Drop sessions that ran out, so the table doesn't grow forever."""
    try:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now(),))
    except Exception as exc:
        print(f"[imy] couldn't tidy old sessions: {exc}")


def start_sweeper(hours: int = 6) -> None:
    """Keep tidying while imy runs. Doing it only at start-up is fine on a laptop you restart often,
    but a server that stays up for months would collect every expired sign-in it ever made."""
    import threading
    import time

    def loop():
        while True:
            time.sleep(hours * 3600)
            sweep_sessions()

    threading.Thread(target=loop, daemon=True).start()


# ---------------------------------------------------------------- asking who it is
def current() -> dict | None:
    return CURRENT.get()


def current_id() -> int | None:
    me = CURRENT.get()
    return me["id"] if me else None


def secure_cookies() -> bool:
    """Send the cookie only over HTTPS. On by default unless imy is running on a plain
    http:// address, which is how you run it on your own computer."""
    flag = os.environ.get("IMY_SECURE_COOKIES")
    if flag is not None:
        return flag == "1"
    return not any(h.startswith("http://") for h in
                   os.environ.get("IMY_ORIGINS", "http://localhost").split(","))
