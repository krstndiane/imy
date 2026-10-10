"""feedback.py - what people tell you from inside the app, and the admin page's inbox for it.

A signed-in client writes a note (an idea, a problem, something they love), optionally rates imy
1-5, and says whether it's fine to email them back. The admin page lists it; the admin marks it
read or done, replies by email, or deletes it.

Their email is shown to the admin only when they ticked "you can email me about this". Deleting
an account deletes its feedback too, like everything else.
"""
from .db import db, now

KINDS = {"idea": "Idea", "problem": "Problem", "love": "Something I like", "other": "Other"}
STATUSES = ("new", "read", "done")
MAX_CHARS = 2000


class FeedbackError(Exception):
    pass


def add(user_id: int, kind: str, message: str, rating: int | None, can_reply: bool, page: str = "") -> dict:
    message = (message or "").strip()
    if not message:
        raise FeedbackError("Write a few words first.")
    if len(message) > MAX_CHARS:
        raise FeedbackError(f"That's a bit long - please keep it under {MAX_CHARS} characters.")
    kind = kind if kind in KINDS else "other"
    if rating is not None and not (1 <= int(rating) <= 5):
        rating = None
    with db() as conn:
        fid = conn.execute(
            "INSERT INTO feedback (user_id, kind, rating, message, page, can_reply, created_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, kind, rating, message, (page or "")[:40], 1 if can_reply else 0, now())).lastrowid
    return {"id": fid, "kind": kind}


def listing(status: str = "") -> dict:
    where, args = ("WHERE f.status = ?", (status,)) if status in STATUSES else ("", ())
    with db() as conn:
        rows = conn.execute(
            "SELECT f.id, f.kind, f.rating, f.message, f.page, f.can_reply, f.status, f.created_at, "
            "CASE WHEN f.can_reply = 1 THEN u.email ELSE NULL END AS email "
            f"FROM feedback f LEFT JOIN users u ON u.id = f.user_id {where} ORDER BY f.id DESC LIMIT 500", args).fetchall()
        counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM feedback GROUP BY status")}
        avg = conn.execute("SELECT AVG(rating), COUNT(rating) FROM feedback WHERE rating IS NOT NULL").fetchone()
    return {"items": [{**dict(r), "can_reply": bool(r["can_reply"]), "kind_label": KINDS.get(r["kind"], "Other")} for r in rows],
            "counts": {s: counts.get(s, 0) for s in STATUSES},
            "rating": {"average": round(avg[0], 1) if avg[0] else None, "count": avg[1]}}


def set_status(fid: int, status: str) -> bool:
    if status not in STATUSES:
        raise FeedbackError("Unknown status.")
    with db() as conn:
        return conn.execute("UPDATE feedback SET status = ? WHERE id = ?", (status, fid)).rowcount == 1


def delete(fid: int) -> bool:
    with db() as conn:
        return conn.execute("DELETE FROM feedback WHERE id = ?", (fid,)).rowcount == 1


def forget_user(conn, user_id: int) -> None:
    """Part of deleting an account (main.wipe_account)."""
    conn.execute("DELETE FROM feedback WHERE user_id = ?", (user_id,))
