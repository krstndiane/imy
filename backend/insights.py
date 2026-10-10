"""insights.py - the admin page's side of imy: who runs the site, and how many people use it.

Two kinds of people use imy, and they never share a door:

  Clients   sign in at /app with an email and password (auth.py) and see only their own chats.
  Admins    the people running the site. They sign in at /admin with a one-time link emailed to
            them, and see how many people use imy and what they sent as feedback. They never see
            anyone's chats, people, photos or screenshots - none of that is reachable from /admin.

Who counts as an admin
    IMY_ADMIN_EMAILS=you@example.com,partner@example.com
    If that's not set, the contact address (IMY_CONTACT_EMAIL) is the admin. There's no admin
    password to leak or guess: signing in means opening a link sent to that inbox, so only
    whoever can read the inbox gets in. Each link works once, for 15 minutes; an admin sign-in
    lasts 12 hours. On your own computer with email off, the link is printed in the Terminal.

What's counted, and how (the privacy page says the same in plain words)
    Accounts      sign-ups and the last time each account did anything (at most once a minute is
                  written), plus "this account used imy today" - one row per account per day, so
                  the admin page can say how many people were active each day. Kept 400 days.
    Visitors      how many people opened the homepage and the app each day. No cookies, nothing
                  stored in the browser. Each visit becomes a code made from a secret that changes
                  every day + the visitor's address + their browser name, so the same person counts
                  once a day. The codes and the day's secret are deleted the next day, so visits
                  can't be linked across days or traced back to anyone. Browsers that send
                  "Do Not Track" or "Global Privacy Control" aren't counted at all.
"""
import hashlib
import os
import re
import secrets
import threading
import time
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone

from .db import db, now

ADMIN_COOKIE = "imy_admin"
ADMIN_COOKIE_PATH = "/api/admin"     # the cookie never travels with ordinary requests
LINK_MINUTES = 15
SESSION_HOURS = 12
ONLINE_MINUTES = 5                   # "online now" = did something in the last 5 minutes
ACTIVE_KEEP_DAYS = 400
PAGES = ("landing", "app")
BOT_RE = re.compile(r"bot|crawl|spider|slurp|preview|monitor|curl|wget|python-requests|headless", re.I)

# The admin making this request (set by the middleware in main.py for /api/admin/... only).
ADMIN: ContextVar[dict | None] = ContextVar("imy_current_admin", default=None)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _utc(delta: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat(timespec="seconds")


def today() -> str:
    return time.strftime("%Y-%m-%d")        # local date (TZ), like the AI limits in usage.py


# ---------------------------------------------------------------- who is an admin
def admin_emails() -> list[str]:
    raw = os.environ.get("IMY_ADMIN_EMAILS", "").strip() or os.environ.get("IMY_CONTACT_EMAIL", "").strip()
    return [e.strip().lower() for e in raw.split(",") if "@" in e]


def is_admin_email(email: str) -> bool:
    return (email or "").strip().lower() in admin_emails()


def start_admin_link(email: str) -> str | None:
    """A one-time sign-in link token for an admin email, or None if this isn't an admin (the page
    says the same either way, so it can't be used to find out who the admins are)."""
    email = (email or "").strip().lower()
    if not is_admin_email(email):
        return None
    token = secrets.token_urlsafe(32)
    with db() as conn:
        recent = conn.execute("SELECT COUNT(*) FROM admin_links WHERE email = ? AND created_at > ?",
                              (email, _utc(-timedelta(hours=1)))).fetchone()[0]
        if recent >= 5:                     # nobody can flood the admin's inbox
            return None
        conn.execute("INSERT INTO admin_links (token_hash, email, created_at, expires_at) VALUES (?,?,?,?)",
                     (_hash(token), email, now(), _utc(timedelta(minutes=LINK_MINUTES))))
    return token


def use_admin_link(token: str) -> tuple[str, str] | None:
    """Trade a link for an admin session. Returns (email, session token), or None if the link is
    used, expired, or its email stopped being an admin. Claiming the link is one step, so opening
    it twice at once still only works once."""
    if not token:
        return None
    th = _hash(token)
    with db() as conn:
        claimed = conn.execute("UPDATE admin_links SET used_at = ? WHERE token_hash = ? AND used_at IS NULL "
                               "AND expires_at > ?", (now(), th, now()))
        if claimed.rowcount != 1:
            return None
        email = conn.execute("SELECT email FROM admin_links WHERE token_hash = ?", (th,)).fetchone()["email"]
        if not is_admin_email(email):
            return None
        session = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO admin_sessions (token_hash, email, created_at, expires_at) VALUES (?,?,?,?)",
                     (_hash(session), email, now(), _utc(timedelta(hours=SESSION_HOURS))))
    return email, session


def load_admin(token: str) -> dict | None:
    """The admin for this cookie, if the session is live and the email is still on the admin list
    (taking someone off IMY_ADMIN_EMAILS locks them out at once, without waiting 12 hours)."""
    if not token:
        return None
    with db() as conn:
        row = conn.execute("SELECT email, expires_at FROM admin_sessions WHERE token_hash = ?", (_hash(token),)).fetchone()
    if not row or row["expires_at"] <= now() or not is_admin_email(row["email"]):
        return None
    return {"email": row["email"]}


def end_admin(token: str) -> None:
    if token:
        with db() as conn:
            conn.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (_hash(token),))


# ---------------------------------------------------------------- "this account is using imy"
_seen: dict[int, float] = {}
_seen_lock = threading.Lock()


def saw(user_id: int) -> None:
    """Called on every signed-in request. Writes at most once a minute per account: the time they
    were last active, and that they used imy today."""
    t = time.time()
    with _seen_lock:
        if t - _seen.get(user_id, 0) < 60:
            return
        _seen[user_id] = t
        if len(_seen) > 50_000:            # never grows without end on a busy server
            for uid, at in list(_seen.items()):
                if t - at > 120:
                    del _seen[uid]
    try:
        with db() as conn:
            conn.execute("UPDATE users SET last_active = ? WHERE id = ?", (now(), user_id))
            conn.execute("INSERT OR IGNORE INTO active_days (day, user_id) VALUES (?, ?)", (today(), user_id))
    except Exception as exc:               # counting must never get in the way of using the app
        with _seen_lock:
            _seen.pop(user_id, None)       # try again on their next request
        print(f"[imy] couldn't note activity: {exc}")


def forget_user(conn, user_id: int) -> None:
    """Part of deleting an account (main.wipe_account)."""
    conn.execute("DELETE FROM active_days WHERE user_id = ?", (user_id,))
    with _seen_lock:
        _seen.pop(user_id, None)


# ---------------------------------------------------------------- visitors, without cookies
def _salt(conn, day: str) -> str:
    row = conn.execute("SELECT salt FROM visit_salt WHERE day = ?", (day,)).fetchone()
    if row:
        return row["salt"]
    salt = secrets.token_hex(16)
    conn.execute("INSERT OR IGNORE INTO visit_salt (day, salt) VALUES (?, ?)", (day, salt))
    return conn.execute("SELECT salt FROM visit_salt WHERE day = ?", (day,)).fetchone()["salt"]


def count_visit(page: str, address: str, user_agent: str, headers) -> bool:
    """Count one page view. Returns False when it wasn't counted (bot, Do Not Track, unknown page)."""
    if page not in PAGES:
        return False
    if headers.get("dnt") == "1" or headers.get("sec-gpc") == "1":
        return False
    ua = (user_agent or "")[:300]
    if not ua or BOT_RE.search(ua):
        return False
    day = today()
    with db() as conn:
        code = hashlib.sha256(f"{_salt(conn, day)}|{address}|{ua}".encode()).hexdigest()[:32]
        new = conn.execute("INSERT OR IGNORE INTO visit_seen (day, page, vhash) VALUES (?,?,?)", (day, page, code)).rowcount
        conn.execute("INSERT OR IGNORE INTO visit_days (day, page) VALUES (?, ?)", (day, page))
        conn.execute("UPDATE visit_days SET views = views + 1, visitors = visitors + ? WHERE day = ? AND page = ?",
                     (1 if new else 0, day, page))
    return True


# ---------------------------------------------------------------- tidying up
def sweep() -> None:
    """Forget yesterday's visit codes and secret, old activity, and used or expired admin links."""
    try:
        day = today()
        old = (date.today() - timedelta(days=ACTIVE_KEEP_DAYS)).isoformat()
        with db() as conn:
            conn.execute("DELETE FROM visit_seen WHERE day < ?", (day,))
            conn.execute("DELETE FROM visit_salt WHERE day < ?", (day,))
            conn.execute("DELETE FROM active_days WHERE day < ?", (old,))
            conn.execute("DELETE FROM admin_links WHERE expires_at <= ?", (_utc(-timedelta(days=1)),))
            conn.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", (now(),))
    except Exception as exc:
        print(f"[imy] couldn't tidy the admin counts: {exc}")


def start_sweeper(minutes: int = 30) -> None:
    def loop():
        while True:
            time.sleep(minutes * 60)
            sweep()
    threading.Thread(target=loop, daemon=True).start()


# ---------------------------------------------------------------- what the admin page shows
def _days(n: int) -> list[str]:
    end = date.fromisoformat(today())
    return [(end - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def _local_day(iso_utc: str) -> str:
    """A stored UTC time as the local date, so "signed up today" means today where you are."""
    try:
        return datetime.fromisoformat(iso_utc).astimezone().date().isoformat()
    except (TypeError, ValueError):
        return ""


def stats(days: int = 30) -> dict:
    span = _days(days)
    first, day = span[0], span[-1]
    week = span[-7:]
    online_since = _utc(-timedelta(minutes=ONLINE_MINUTES))
    with db() as conn:
        accounts = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        people = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
        chats = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE role = 'user'").fetchone()[0]
        online = conn.execute("SELECT COUNT(*) FROM users WHERE last_active > ?", (online_since,)).fetchone()[0]
        active = {r["day"]: r["n"] for r in conn.execute(
            "SELECT day, COUNT(*) AS n FROM active_days WHERE day >= ? GROUP BY day", (first,))}
        active_week = conn.execute("SELECT COUNT(DISTINCT user_id) FROM active_days WHERE day >= ?", (week[0],)).fetchone()[0]
        active_month = conn.execute("SELECT COUNT(DISTINCT user_id) FROM active_days WHERE day >= ?", (first,)).fetchone()[0]
        visits = {(r["day"], r["page"]): r for r in conn.execute(
            "SELECT day, page, views, visitors FROM visit_days WHERE day >= ?", (first,))}
        joined = [_local_day(r["created_at"]) for r in conn.execute(
            "SELECT created_at FROM users WHERE created_at >= ?", (_utc(-timedelta(days=days + 1)),))]
        fb_new = conn.execute("SELECT COUNT(*) FROM feedback WHERE status = 'new'").fetchone()[0]
        fb_all = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        ai_today = conn.execute("SELECT COALESCE(SUM(seen + other), 0) FROM ai_usage WHERE day = ?", (day,)).fetchone()[0]
    signups = {d: joined.count(d) for d in span}
    series = [{"day": d,
               "visitors": visits[(d, "landing")]["visitors"] if (d, "landing") in visits else 0,
               "views": visits[(d, "landing")]["views"] if (d, "landing") in visits else 0,
               "app_visitors": visits[(d, "app")]["visitors"] if (d, "app") in visits else 0,
               "active": active.get(d, 0),
               "signups": signups.get(d, 0)} for d in span]
    t = series[-1]
    return {
        "today": day,
        "online_minutes": ONLINE_MINUTES,
        "online": online,
        "active_today": t["active"], "active_week": active_week, "active_month": active_month,
        "accounts": accounts, "signups_today": t["signups"], "signups_week": sum(signups[d] for d in week),
        "visitors_today": t["visitors"], "views_today": t["views"], "app_visitors_today": t["app_visitors"],
        "visitors_week": sum(s["visitors"] for s in series[-7:]),
        "people": people, "messages_sent": chats, "ai_today": ai_today,
        "feedback_new": fb_new, "feedback_total": fb_all,
        "series": series,
    }


def accounts(q: str = "", limit: int = 500) -> dict:
    """Every account, most recently active first: email, when they joined, when they were last
    active, how many people they set up and on how many of the last 30 days they used imy.
    Counts only - no names of the people they set up, no messages."""
    since = _days(30)[0]
    like = f"%{q.strip().lower()}%" if q.strip() else "%"
    with db() as conn:
        rows = conn.execute(
            "SELECT u.id, u.email, u.created_at, COALESCE(u.last_active, u.last_seen) AS last_active, "
            "(SELECT COUNT(*) FROM persons p WHERE p.user_id = u.id) AS people, "
            "(SELECT COUNT(*) FROM active_days a WHERE a.user_id = u.id AND a.day >= ?) AS days_active "
            "FROM users u WHERE u.email LIKE ? ORDER BY last_active DESC LIMIT ?", (since, like, limit)).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM users WHERE email LIKE ?", (like,)).fetchone()[0]
    online_since = _utc(-timedelta(minutes=ONLINE_MINUTES))
    return {"total": total, "accounts": [{**dict(r), "online": (r["last_active"] or "") > online_since} for r in rows]}
