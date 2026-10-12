"""usage.py - how much of the AI each account uses, per day.

The free AI (Groq) has ONE daily allowance for the whole site. Without a limit, a few very chatty
people could use it up by lunchtime and everyone else would get imy's simpler replies for the rest
of the day. So each account gets a fair share:

    IMY_AI_DAILY_PER_ACCOUNT=50   AI replies + photo looks, per account, per day   (0 = no limit)
    IMY_AI_DAILY_SITE=0           every AI call on the whole site, per day          (0 = no limit)
                                  Set this once you pay for the AI and want a ceiling on the bill.

What counts: a reply the AI wrote, and a photo the AI looked at. Those are what people see. The
memory notes (notes.py) also use the AI, but they're housekeeping, so they only count towards the
site total - and they pause for an account that has used up its share.

Past the limit nothing breaks: replies come from imy's own reading of their messages, a photo still
gets a reaction (just without a description), and the memory notes wait. The count starts again at
midnight in imy's time zone (TZ, e.g. Asia/Manila).

Only successful calls are counted, so a failed or rate-limited request never uses up someone's share.
"""
import os
import threading
import time
from datetime import date, timedelta

from . import auth
from .db import db

SEEN = ("reply", "photo")      # counted against the account; everything else is "other"


def per_account() -> int:
    return _env_int("IMY_AI_DAILY_PER_ACCOUNT", 50)


def per_site() -> int:
    return _env_int("IMY_AI_DAILY_SITE", 0)


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default) or 0))
    except ValueError:
        return default


def today() -> str:
    return time.strftime("%Y-%m-%d")        # local time, so "midnight" means midnight where you are


# The site-wide total is read on every AI call, so it's kept in memory instead of summing the table
# each time. It's loaded from the database once a day (or after a restart) and then counted up here.
_site = {"day": "", "n": 0}
_site_lock = threading.Lock()


def site_used() -> int:
    d = today()
    with _site_lock:
        if _site["day"] == d:
            return _site["n"]
    with db() as conn:
        n = conn.execute("SELECT COALESCE(SUM(seen + other), 0) FROM ai_usage WHERE day = ?", (d,)).fetchone()[0]
    with _site_lock:
        if _site["day"] != d:
            _site.update(day=d, n=n)
        return _site["n"]


def used(user_id: int | None = None) -> int:
    """AI replies + photo looks this account has used today."""
    uid = auth.current_id() if user_id is None else user_id
    if uid is None:
        return 0
    with db() as conn:
        row = conn.execute("SELECT seen FROM ai_usage WHERE day = ? AND user_id = ?", (today(), uid)).fetchone()
    return row[0] if row else 0


def blocked(kind: str = "reply") -> str:
    """"" if the AI may be used right now, otherwise a short reason a person can read."""
    if not auth.cloud_ai_allowed():
        return "you've turned off the outside AI for your account"
    site = per_site()
    if site and site_used() >= site:
        return "imy's AI allowance for today is used up; it comes back at midnight"
    limit = per_account()
    uid = auth.current_id()
    if limit and uid is not None and used(uid) >= limit:
        return f"you've used today's {limit} AI {'reply' if limit == 1 else 'replies'}; they come back at midnight"
    return ""


def spend(kind: str = "reply") -> None:
    """Count one successful AI call for whoever is asking (0 when nobody is signed in, e.g. tests)."""
    uid = auth.current_id() or 0
    col = "seen" if kind in SEEN else "other"
    d = today()
    try:
        with db() as conn:
            conn.execute(f"INSERT INTO ai_usage (day, user_id, {col}) VALUES (?, ?, 1) "
                         f"ON CONFLICT(day, user_id) DO UPDATE SET {col} = {col} + 1", (d, uid))
    except Exception as exc:        # counting must never be the reason a reply fails
        print(f"[imy] couldn't count AI use: {exc}")
        return
    with _site_lock:
        if _site["day"] == d:
            _site["n"] += 1


def summary(user_id: int | None = None) -> dict:
    """For the account menu: "AI replies today: 12 of 50"."""
    return {"used": used(user_id), "limit": per_account()}


def prune(keep_days: int = 40) -> None:
    """Old days aren't needed; keep a little over a month for looking back."""
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    try:
        with db() as conn:
            conn.execute("DELETE FROM ai_usage WHERE day < ?", (cutoff,))
    except Exception as exc:
        print(f"[imy] couldn't tidy old usage counts: {exc}")
