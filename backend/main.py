"""main.py - the web API (FastAPI).

Run from the project root:   python -m uvicorn backend.main:app --reload
Interactive docs:            http://localhost:8000/docs

Built to keep running when something goes wrong: every request uses short database transactions
(slow work like reading screenshots or asking the AI happens outside them), one bad file never
fails a whole upload, a failing reply falls back to a simpler one, and unexpected errors come back
as a friendly message instead of taking the page down.
"""
import hashlib
import html
import json
import os
import random
import re
import shutil
import threading
import time
import traceback
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from PIL import Image
from pydantic import BaseModel

from . import auth, backup, claude, engine, importers, insights, llm, looks, mailer, media as media_mod, notes, ocr, parser, style as style_mod, usage
from . import feedback as feedback_mod
from .db import DATA_DIR, UPLOAD_DIR, bump_version, db, init_db, now

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = ROOT / "frontend" / "dist"
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
MAX_IMAGE_BYTES = int(os.environ.get("IMY_MAX_IMAGE_MB", "25")) * 1024 * 1024
MAX_EXPORT_BYTES = int(os.environ.get("IMY_MAX_EXPORT_MB", "500")) * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 60_000_000          # refuse absurd images (a "decompression bomb") instead of running out of memory


@asynccontextmanager
async def lifespan(_app):
    init_db()
    auth.sweep_sessions()                    # drop sign-ins that ran out while imy was off
    auth.start_sweeper()                     # and keep doing it while it runs
    insights.sweep()                         # yesterday's visit codes, old admin links (see insights.py)
    insights.start_sweeper()
    backup.start_daily()                     # only does anything with IMY_BACKUP_DAILY=1
    usage.prune()                            # daily AI counts older than ~a month aren't needed
    engine.VectorSpace.warm_up()             # load the matcher in the background: the first reply isn't slow
    if llm.enabled():
        print(f"[imy] AI brain on: {', '.join(llm.providers())} (mode: {llm.mode()})")
        if llm.status()["warning"]:
            print("[imy] WARNING: " + llm.status()["warning"])
    yield


app = FastAPI(title="imy", lifespan=lifespan)
ORIGINS = [o.strip() for o in os.environ.get("IMY_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Pages anyone can reach without signing in. Everything else under /api and every uploaded file
# needs an account - that's what keeps one person's chats out of another person's browser.
PUBLIC_PATHS = {"/api/health", "/api/status", "/api/auth/me",
                "/api/auth/signup", "/api/auth/signin", "/api/auth/signout",
                "/api/auth/forgot", "/api/auth/reset", "/api/auth/reset/check", "/api/visit"}
# The admin page has its own door (insights.py): an emailed link, its own cookie, and only these
# can be reached without it.
ADMIN_PUBLIC = {"/api/admin/link", "/api/admin/login", "/api/admin/me", "/api/admin/logout"}


async def identify(request: Request, call_next):
    """Work out who is asking (from the sign-in cookie) before the request reaches anything else."""
    path = request.url.path
    if path.startswith("/api/admin/"):
        return await _identify_admin(request, call_next, path)
    user = None
    try:
        token = request.cookies.get(auth.SESSION_COOKIE, "")
        if token:
            user = auth.load_session(token)
    except Exception:
        print(f"[imy] couldn't read the sign-in cookie:\n{traceback.format_exc()}")
    if user:
        insights.saw(user["id"])              # "active today" for the admin page; at most one write a minute
    reset = auth.CURRENT.set(user)
    try:
        needs_account = (path.startswith("/api/") and path not in PUBLIC_PATHS) or path.startswith("/uploads/")
        if needs_account and not user:
            return JSONResponse({"detail": "Please sign in."}, status_code=401)
        return await call_next(request)
    finally:
        auth.CURRENT.reset(reset)


async def _identify_admin(request: Request, call_next, path: str):
    """Admin requests: the client sign-in cookie means nothing here, only the admin one does."""
    admin = None
    try:
        admin = insights.load_admin(request.cookies.get(insights.ADMIN_COOKIE, ""))
    except Exception:
        print(f"[imy] couldn't read the admin cookie:\n{traceback.format_exc()}")
    if not admin and path not in ADMIN_PUBLIC:
        return JSONResponse({"detail": "Admin sign-in needed."}, status_code=401)
    reset = insights.ADMIN.set(admin)
    try:
        return await call_next(request)
    finally:
        insights.ADMIN.reset(reset)


# Added in this order so CORS ends up outermost (Starlette wraps each new one around the last).
app.add_middleware(BaseHTTPMiddleware, dispatch=identify)
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------- never crash
@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    """Anything we didn't plan for: log it for you, show the user a calm message."""
    print(f"[imy] error on {request.method} {request.url.path}:\n{traceback.format_exc()}")
    return JSONResponse({"detail": "Something went wrong on imy's side. Please try again."}, status_code=500)


# A small per-visitor speed limit, so one person (or a bot) can't use up the free AI limit or the CPU.
LIMITS = {"chat": (40, 60), "upload": (120, 60), "import": (20, 60),
          "auth": (12, 300),        # sign-in guesses: a dozen tries per 5 minutes, per visitor
          "forgot": (5, 900),       # "email me a reset link": 5 per 15 minutes, per visitor
          "admin": (5, 900),        # "email me an admin link"
          "feedback": (6, 3600),    # feedback notes: 6 an hour is plenty for a person, too few for spam
          "visit": (120, 60)}       # page-view counting
_hits: dict[tuple[str, str], deque] = defaultdict(deque)
_hits_lock = threading.Lock()
_swept_at = 0.0
SWEEP_EVERY = 300        # seconds between clear-outs


def _forget_quiet_visitors(t: float) -> None:
    """Drop visitors who haven't been back inside their time window.

    Without this, every address that ever visits is remembered for as long as imy runs - a few
    hundred bytes each, never given back. That's invisible on a laptop and a slow leak on a busy
    site. Called with the lock already held, and only every few minutes."""
    global _swept_at
    if t - _swept_at < SWEEP_EVERY:
        return
    _swept_at = t
    for key in list(_hits):
        q = _hits[key]
        per = LIMITS[key[1]][1]
        while q and q[0] < t - per:
            q.popleft()
        if not q:
            del _hits[key]


def visitor(request: Request) -> str:
    """The visitor's address, in a way they can't fake.

    Behind a host's proxy (Render, Railway, Fly...), the real address is in X-Forwarded-For. But
    anyone can send that header with whatever they like in it, and the proxy only ADDS the address
    it saw to the end. So the first entry is whatever the visitor typed, and the last one is the
    one the proxy vouches for. imy used to take the first, which let a script dodge the sign-in
    limit just by sending a different made-up address each time.

    IMY_PROXY_HOPS is how many proxies sit in front of imy (1 for most hosts; 2 if you add
    Cloudflare in front of one; 0 if nothing does, and then the header is ignored)."""
    try:
        hops = max(0, int(os.environ.get("IMY_PROXY_HOPS", "1")))
    except ValueError:
        hops = 1
    chain = [h.strip() for h in (request.headers.get("x-forwarded-for") or "").split(",") if h.strip()]
    if hops and chain:
        return chain[-min(hops, len(chain))]
    return request.client.host if request.client else "?"


def rate_limit(request: Request | None, kind: str) -> None:
    if request is None or os.environ.get("IMY_RATE_LIMIT", "1") == "0":
        return
    who = visitor(request)
    n, per = LIMITS[kind]
    t = time.time()
    with _hits_lock:
        _forget_quiet_visitors(t)
        q = _hits[(who, kind)]
        while q and q[0] < t - per:
            q.popleft()
        if len(q) >= n:
            raise HTTPException(429, "Slow down a little, then try again.")
        q.append(t)


@app.get("/api/health")
def health():
    """For hosting platforms: is the server up and can it use the database?"""
    with db() as conn:
        conn.execute("SELECT 1").fetchone()
    return {"ok": True}


@app.get("/api/status")
def status():
    """Which brain is on (no keys are ever shown)."""
    return {"ai": llm.status(), "claude": claude.enabled(), "matcher": engine.VectorSpace.kind_now()}


# ---------------------------------------------------------------- accounts
class SignUpIn(BaseModel):
    email: str = ""
    password: str = ""
    is_adult: bool = False      # confirmed they're 18 or over
    agrees: bool = False        # agreed to the Terms and read the privacy page (same tick box on the site)


class SignInIn(BaseModel):
    email: str = ""
    password: str = ""


class PasswordIn(BaseModel):
    old_password: str = ""
    new_password: str = ""


def _signed_in(user: dict, token: str, request: Request | None = None) -> JSONResponse:
    """Answer with the account, and put the session in a cookie the browser sends back every time.
    httponly keeps JavaScript (and anything injected into the page) from reading it; samesite=lax
    stops another website from using it behind your back."""
    resp = JSONResponse({"user": {"id": user["id"], "email": user["email"]}})
    resp.set_cookie(auth.SESSION_COOKIE, token, max_age=auth.SESSION_DAYS * 86400,
                    httponly=True, samesite="lax", secure=auth.secure_cookies(request), path="/")
    return resp


@app.post("/api/auth/signup")
def sign_up(body: SignUpIn, request: Request = None):
    rate_limit(request, "auth")
    try:
        if not body.agrees:
            raise auth.AuthError("Please agree to the Terms to make an account.")
        user = auth.create_user(body.email, body.password, body.is_adult, terms=True)
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc))
    token = auth.start_session(user["id"], request.headers.get("user-agent", "") if request else "")
    return _signed_in(user, token, request)


@app.post("/api/auth/signin")
def sign_in(body: SignInIn, request: Request = None):
    rate_limit(request, "auth")
    try:
        user = auth.verify_user(body.email, body.password)
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc))
    token = auth.start_session(user["id"], request.headers.get("user-agent", "") if request else "")
    return _signed_in(user, token, request)


@app.post("/api/auth/signout")
def sign_out(request: Request = None):
    if request:
        auth.end_session(request.cookies.get(auth.SESSION_COOKIE, ""))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


@app.get("/api/auth/me")
def who_am_i():
    """Who's signed in, or nobody. The only protected-looking route that's fine to call signed out,
    because the website asks it first to decide whether to show the sign-in screen."""
    me = auth.current()
    if not me:
        return {"user": None}
    with db() as conn:
        people = conn.execute("SELECT COUNT(*) FROM persons WHERE user_id = ?", (me["id"],)).fetchone()[0]
    return {"user": {"id": me["id"], "email": me["email"], "created_at": me["created_at"], "people": people,
                     "ai_today": usage.summary(me["id"])}}


def wipe_account(user_id: int) -> list[int]:
    """Remove an account and everything under it: people, messages, chats, photos, screenshots,
    sign-ins, reset links, usage counts, feedback, activity days, and every uploaded file. Used by "Delete everything" and by
    `python -m backend.admin remove`. Returns the ids of the people removed."""
    with db() as conn:
        pids = [r["id"] for r in conn.execute("SELECT id FROM persons WHERE user_id = ?", (user_id,))]
        paths = [r["stored_path"] for r in conn.execute(
            "SELECT s.stored_path FROM screenshots s JOIN persons p ON p.id = s.person_id "
            "WHERE p.user_id = ? AND s.stored_path IS NOT NULL", (user_id,))]
        # Deleted by hand rather than left to the database's cascade: "delete everything" has to
        # mean everything, so nothing depends on a foreign key behaving as expected.
        for pid in pids:
            conn.execute("DELETE FROM messages WHERE person_id = ?", (pid,))
            conn.execute("DELETE FROM chat_messages WHERE person_id = ?", (pid,))
            conn.execute("DELETE FROM media WHERE person_id = ?", (pid,))
            conn.execute("DELETE FROM screenshots WHERE person_id = ?", (pid,))
        conn.execute("DELETE FROM persons WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM password_resets WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM ai_usage WHERE user_id = ?", (user_id,))
        feedback_mod.forget_user(conn, user_id)
        insights.forget_user(conn, user_id)
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    for p in paths:
        (UPLOAD_DIR / p).unlink(missing_ok=True)
    for pid in pids:
        shutil.rmtree(UPLOAD_DIR / str(pid), ignore_errors=True)
        engine.forget(pid)
    return pids


class DeleteAccountIn(BaseModel):
    password: str = ""
    confirm: str = ""      # the word DELETE, typed out, so this can't happen by accident


@app.delete("/api/auth/account")
def delete_account(body: DeleteAccountIn, request: Request = None):
    """Delete everything: the account, every person set up under it, their messages, and every
    uploaded file. There is no undo, which is why it needs the password and the typed word."""
    rate_limit(request, "auth")
    me = auth.current()
    if body.confirm.strip().upper() != "DELETE":
        raise HTTPException(400, 'Type DELETE to confirm.')
    try:
        auth.verify_user(me["email"], body.password)
    except auth.AuthError:
        raise HTTPException(401, "That isn't your password.")
    pids = wipe_account(me["id"])
    resp = JSONResponse({"ok": True, "deleted_people": len(pids)})
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


@app.put("/api/auth/password")
def set_password(body: PasswordIn, request: Request = None):
    rate_limit(request, "auth")
    me = auth.current()
    try:
        auth.change_password(me["id"], body.old_password, body.new_password)
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc))
    token = auth.start_session(me["id"], request.headers.get("user-agent", "") if request else "")
    _tell_password_changed(me["email"], request)
    return _signed_in(me, token, request)      # changing it signs out every other browser; keep this one


# ---------------------------------------------------------------- forgot password
# 1. The sign-in page sends an email address to /forgot. If there's an account, imy emails it a link:
#       https://your-site/app#reset=<token>
#    The token comes after "#", so browsers never send it to the server: it stays out of server
#    and host logs, and out of the "Referer" other sites would see.
# 2. Opening the link shows "Choose a new password" (the page asks /reset/check first, so an old
#    link says so straight away instead of after typing a new password).
# 3. /reset sets the password, signs out every device, signs in this one, and emails a notice.
class ForgotIn(BaseModel):
    email: str = ""


class ResetCheckIn(BaseModel):
    token: str = ""


class ResetIn(BaseModel):
    token: str = ""
    password: str = ""


def _tell_password_changed(email: str, request: Request | None) -> None:
    site = mailer.site_url(request)
    mailer.send_later(email, *mailer.changed_email(site, email), print_if_off=mailer.is_local(site))


@app.post("/api/auth/forgot")
def forgot_password(body: ForgotIn, request: Request = None):
    """Email a reset link. Always the same answer whether or not the account exists - otherwise
    this would tell anyone who has an imy account."""
    rate_limit(request, "forgot")
    email = (body.email or "").strip().lower()
    if not auth.EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(400, "That doesn't look like an email address.")
    site = mailer.site_url(request)
    local = mailer.is_local(site)
    if not site or not (mailer.configured() or local):
        # Said to everyone alike (it depends on the site, not the account), so it gives nothing away.
        who = contact_email()
        print("[imy] someone asked for a password reset, but email isn't set up "
              "(IMY_SMTP_HOST and IMY_PUBLIC_URL; see backend/mailer.py)", flush=True)
        raise HTTPException(503, "imy can't send email yet, so passwords can't be reset here. "
                                 + (f"Write to {who} for help." if who else "Please try again later."))
    found = auth.start_reset(email)
    if found:
        user, token = found
        link = f"{site}/app#reset={token}"
        mailer.send_later(user["email"], *mailer.reset_email(link, auth.RESET_MINUTES, user["email"]), print_if_off=local)
    # "printed": email is off and this is your own computer, so the link went to the Terminal.
    return {"ok": True, "minutes": auth.RESET_MINUTES, "printed": not mailer.configured()}


@app.post("/api/auth/reset/check")
def check_reset_link(body: ResetCheckIn, request: Request = None):
    rate_limit(request, "auth")
    user = auth.check_reset(body.token)
    if not user:
        raise HTTPException(400, auth.RESET_EXPIRED)
    return {"email": user["email"], "minutes": auth.RESET_MINUTES}


@app.post("/api/auth/reset")
def reset_password(body: ResetIn, request: Request = None):
    rate_limit(request, "auth")
    try:
        user = auth.finish_reset(body.token, body.password)
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc))
    token = auth.start_session(user["id"], request.headers.get("user-agent", "") if request else "")
    _tell_password_changed(user["email"], request)
    return _signed_in(user, token, request)


# ---------------------------------------------------------------- who owns what
def _uid() -> int:
    """The signed-in account's id. The middleware already turned away anyone without one, so this
    failing means a route was added outside /api without thinking about accounts."""
    me = auth.current_id()
    if me is None:
        raise HTTPException(401, "Please sign in.")
    return me


def _person_row(conn, pid: int):
    """A person, but only if they're yours. Every route goes through here, which is what stops one
    account reaching another's data - "not yours" and "doesn't exist" give the same answer, so
    nobody can go fishing for which ids exist."""
    row = conn.execute("SELECT * FROM persons WHERE id = ? AND user_id = ?", (pid, _uid())).fetchone()
    if not row:
        raise HTTPException(404, "Person not found")
    return row


def _owned(conn, table: str, row_id: int, what: str):
    """One row from a table that hangs off a person (a screenshot, a message, a photo), but only
    if that person belongs to you."""
    row = conn.execute(
        f"SELECT t.* FROM {table} t JOIN persons p ON p.id = t.person_id WHERE t.id = ? AND p.user_id = ?",
        (row_id, _uid())).fetchone()
    if not row:
        raise HTTPException(404, f"{what} not found")
    return row


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def _person_dict(row) -> dict:
    return {**dict(row), "answers": _loads(row["answers"], {}), "use_llm": bool(row["use_llm"])}


def _look(conn, pid: int, chosen: str, prefs: dict) -> dict:
    """How the chat screen should look: the chosen app (or the one detected in the screenshots), in light
    or dark like the screenshots, with the bubble colours measured from them."""
    metas = [_loads(r["meta"], {}) for r in conn.execute(
        "SELECT meta FROM screenshots WHERE person_id = ? AND stored_path IS NOT NULL ORDER BY id DESC LIMIT 40", (pid,))]
    look = looks.summarize(metas, chosen)
    if not look["detected"]:   # chat exports know their app too
        row = conn.execute("SELECT platform FROM screenshots WHERE person_id = ? AND stored_path IS NULL AND platform IN "
                           "('messenger','instagram','whatsapp','telegram','imessage','viber') GROUP BY platform "
                           "ORDER BY COUNT(*) DESC LIMIT 1", (pid,)).fetchone()
        if row:
            look["detected"] = row["platform"]
            look["platform"] = chosen or row["platform"]
    look["pref_mode"] = prefs.get("mode", "auto")
    look["use_colors"] = prefs.get("colors", True)
    return look


def _person_summary(conn, row) -> dict:
    p = _person_dict(row)
    pid = p["id"]
    one = lambda sql: conn.execute(sql, (pid,)).fetchone()[0]
    return {
        "id": pid, "name": p["name"],
        # The nickname you set in the chat wins; otherwise it's what you called them in the interview.
        "nickname": (p.get("nick_them") or "").strip() or p["answers"].get("q2", ""),
        "nick_them": p.get("nick_them") or "", "nick_me": p.get("nick_me") or "",
        "avatar": f"/uploads/{p['avatar']}" if p.get("avatar") else None,
        "my_avatar": f"/uploads/{p['my_avatar']}" if p.get("my_avatar") else None,
        "theme": _loads(p.get("theme"), {}),
        "answers": p["answers"], "use_llm": p["use_llm"], "platform": p.get("platform") or "",
        "created_at": p["created_at"], "updated_at": p["updated_at"],
        "screenshot_count": one("SELECT COUNT(*) FROM screenshots WHERE person_id = ? AND stored_path IS NOT NULL"),
        "message_count": one("SELECT COUNT(*) FROM messages WHERE person_id = ?"),
        "person_message_count": one("SELECT COUNT(*) FROM messages WHERE person_id = ? AND speaker = 'person'"),
        "chat_count": one("SELECT COUNT(*) FROM chat_messages WHERE person_id = ? AND kind != 'event'"),
        "media_count": one("SELECT COUNT(*) FROM media WHERE person_id = ?"),
        "remembers": len(notes.load(p.get("memory") or "")),
        "look": _look(conn, pid, p.get("platform") or "", _loads(p.get("look"), {})),
    }


def _name_from_answers(answers: dict) -> str:
    return (answers.get("q1") or answers.get("q2") or "Unnamed").strip() or "Unnamed"


def _known_names(answers: dict) -> list[str]:
    names = [answers.get("q1", ""), answers.get("q2", "")]
    names += [n.split()[0] for n in names if n.strip()]
    return [n for n in names if n.strip()]


def _messages_for_engine(conn, pid: int) -> list[dict]:
    rows = conn.execute(
        "SELECT m.*, s.platform AS shot_platform FROM messages m JOIN screenshots s ON s.id = m.screenshot_id "
        "WHERE m.person_id = ? ORDER BY s.id, m.position", (pid,)).fetchall()
    return [dict(r) for r in rows]


def _load_training(pid: int) -> list[dict]:
    """Their training messages, on its own short connection - used by the lazy loader in send_chat."""
    with db() as conn:
        return _messages_for_engine(conn, pid)


def _person_for_engine(conn, pid: int) -> dict:
    """The person plus the platform most of their screenshots came from."""
    person = _person_dict(_person_row(conn, pid))
    if person.get("platform"):  # chosen by the user in setup
        return person
    row = conn.execute(
        "SELECT platform FROM screenshots WHERE person_id = ? AND platform NOT IN ('unknown', 'text-import', 'practice', 'correction') "
        "GROUP BY platform ORDER BY COUNT(*) DESC LIMIT 1", (pid,)).fetchone()
    person["platform"] = row["platform"] if row else "unknown"
    return person


def _store_messages(conn, pid: int, shot_id: int, msgs: list[dict]) -> None:
    conn.executemany(
        "INSERT INTO messages (person_id, screenshot_id, position, speaker, text) VALUES (?,?,?,?,?)",
        [(pid, shot_id, i, m["speaker"], m["text"]) for i, m in enumerate(msgs)])


def _shot_out(conn, shot) -> dict:
    msgs = conn.execute("SELECT id, position, speaker, text FROM messages WHERE screenshot_id = ? ORDER BY position",
                        (shot["id"],)).fetchall()
    meta = _loads(shot["meta"], {}) if "meta" in shot.keys() else {}
    return {
        "id": shot["id"], "filename": shot["filename"], "platform": shot["platform"],
        "flipped": bool(shot["flipped"]),
        "url": f"/uploads/{shot['stored_path']}" if shot["stored_path"] else None,
        "messages": [dict(m) for m in msgs],
        "meta": {k: meta[k] for k in ("platform", "confidence", "mode", "title", "group", "senders") if k in meta},
    }


# ---------------------------------------------------------------- questions
@app.get("/api/questions")
def get_questions():
    return json.loads((Path(__file__).parent / "questions.json").read_text())


# ---------------------------------------------------------------- persons
PLATFORMS = {"", "messenger", "imessage", "instagram", "whatsapp", "telegram", "viber"}


class AnswersIn(BaseModel):
    answers: dict[str, str] = {}
    platform: str | None = None   # None = leave unchanged


def _check_platform(platform: str | None) -> None:
    if platform is not None and platform not in PLATFORMS:
        raise HTTPException(400, "Unknown platform.")


def _reply_prompts() -> dict:
    """{question id: the message shown as yours} for every 'reply' question in questions.json."""
    data = json.loads((Path(__file__).parent / "questions.json").read_text())
    return {q["id"]: q["prompt"] for s in data["sections"] for q in s["questions"] if q.get("type") == "reply"}


def _sync_practice(conn, pid: int, answers: dict) -> None:
    """Turn 'If you texted X, they'd reply ___' answers into exchanges the engine learns from."""
    conn.execute("DELETE FROM screenshots WHERE person_id = ? AND platform = 'practice'", (pid,))
    pairs = [(prompt, answers[qid].strip()) for qid, prompt in _reply_prompts().items() if (answers.get(qid) or "").strip()]
    if not pairs:
        return
    cur = conn.execute(
        "INSERT INTO screenshots (person_id, filename, stored_path, platform, raw_text, created_at) VALUES (?,?,?,?,?,?)",
        (pid, "How they'd reply (interview)", None, "practice", "", now()))
    msgs = []
    for prompt, reply in pairs:
        msgs += [{"speaker": "user", "text": prompt}] + [{"speaker": "person", "text": line.strip()}
                                                         for line in reply.splitlines() if line.strip()]
    _store_messages(conn, pid, cur.lastrowid, msgs)


@app.get("/api/persons")
def list_persons():
    with db() as conn:
        rows = conn.execute("SELECT * FROM persons WHERE user_id = ? ORDER BY updated_at DESC", (_uid(),)).fetchall()
        return [_person_summary(conn, r) for r in rows]


class NewPersonIn(AnswersIn):
    # Ticked "these are conversations I was part of, and I won't use imy to pretend to be this person
    # to anyone else". Asked once, when the person is added, and the time is kept with them.
    consent: bool = False


@app.post("/api/persons")
def create_person(body: NewPersonIn):
    _check_platform(body.platform)
    if not body.consent:
        raise HTTPException(400, "Please confirm these are your own conversations before adding someone.")
    with db() as conn:
        ts = now()
        cur = conn.execute(
            "INSERT INTO persons (name, answers, platform, user_id, consent_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (_name_from_answers(body.answers), json.dumps(body.answers), body.platform or "", _uid(), ts, ts, ts))
        _sync_practice(conn, cur.lastrowid, body.answers)
        return _person_summary(conn, _person_row(conn, cur.lastrowid))


@app.get("/api/persons/{pid}")
def get_person(pid: int):
    with db() as conn:
        return _person_summary(conn, _person_row(conn, pid))


@app.put("/api/persons/{pid}")
def update_person(pid: int, body: AnswersIn):
    _check_platform(body.platform)
    with db() as conn:
        _person_row(conn, pid)
        conn.execute("UPDATE persons SET name = ?, answers = ?, platform = COALESCE(?, platform) WHERE id = ?",
                     (_name_from_answers(body.answers), json.dumps(body.answers), body.platform, pid))
        _sync_practice(conn, pid, body.answers)
        bump_version(conn, pid)
        return _person_summary(conn, _person_row(conn, pid))


class SettingsIn(BaseModel):
    use_llm: bool


@app.put("/api/persons/{pid}/settings")
def update_settings(pid: int, body: SettingsIn):
    with db() as conn:
        _person_row(conn, pid)
        conn.execute("UPDATE persons SET use_llm = ? WHERE id = ?", (int(body.use_llm), pid))
        return _person_summary(conn, _person_row(conn, pid))


class LookIn(BaseModel):
    platform: str | None = None     # the app the chat looks like ("" = imy's own look)
    mode: str | None = None         # "auto" (like the screenshots), "light" or "dark"
    colors: bool | None = None      # copy the bubble colours from the screenshots


@app.put("/api/persons/{pid}/look")
def update_look(pid: int, body: LookIn):
    _check_platform(body.platform)
    if body.mode is not None and body.mode not in ("auto", "light", "dark"):
        raise HTTPException(400, "mode must be auto, light or dark")
    with db() as conn:
        row = _person_row(conn, pid)
        prefs = _loads(row["look"], {})
        if body.mode is not None:
            prefs["mode"] = body.mode
        if body.colors is not None:
            prefs["colors"] = body.colors
        conn.execute("UPDATE persons SET look = ?, platform = COALESCE(?, platform) WHERE id = ?",
                     (json.dumps(prefs), body.platform, pid))
        return _person_summary(conn, _person_row(conn, pid))


# ---------------------------------------------------------------- profile picture, nicknames, theme
def _event(conn, pid: int, text: str) -> None:
    """A little grey note in the middle of the chat, the way the apps do it
    ("You set the nickname for Lawrence to Law")."""
    conn.execute("INSERT INTO chat_messages (person_id, role, text, kind, created_at) VALUES (?,?,?,'event',?)",
                 (pid, "user", text, now()))


@app.post("/api/persons/{pid}/avatar")
def set_avatar(pid: int, file: UploadFile = File(...), who: str = "person", request: Request = None):
    """Upload a profile picture - theirs, or yours for this chat. Saved as a small square."""
    rate_limit(request, "upload")
    if who not in ("person", "me"):
        raise HTTPException(400, "who must be 'person' or 'me'")
    with db() as conn:
        row = _person_row(conn, pid)
        old = row["my_avatar" if who == "me" else "avatar"]
    data = file.file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, f"That photo is too big (max {MAX_IMAGE_BYTES // (1024 * 1024)} MB).")
    try:
        saved = media_mod.save(UPLOAD_DIR / str(pid) / "avatar", file.filename or "photo.jpg", data, avatar=True)
    except media_mod.MediaError as exc:
        raise HTTPException(400, str(exc))
    column = "my_avatar" if who == "me" else "avatar"
    with db() as conn:
        name = _person_dict(_person_row(conn, pid))["name"]
        conn.execute(f"UPDATE persons SET {column} = ?, updated_at = ? WHERE id = ?",
                     (f"{pid}/avatar/{saved['name']}", now(), pid))
        _event(conn, pid, "You changed your photo." if who == "me" else f"You changed {name}'s photo.")
        out = _person_summary(conn, _person_row(conn, pid))
    if old:
        (UPLOAD_DIR / old).unlink(missing_ok=True)
    return out


@app.delete("/api/persons/{pid}/avatar")
def clear_avatar(pid: int, who: str = "person"):
    if who not in ("person", "me"):
        raise HTTPException(400, "who must be 'person' or 'me'")
    column = "my_avatar" if who == "me" else "avatar"
    with db() as conn:
        row = _person_row(conn, pid)
        old = row[column]
        conn.execute(f"UPDATE persons SET {column} = NULL WHERE id = ?", (pid,))
        out = _person_summary(conn, _person_row(conn, pid))
    if old:
        (UPLOAD_DIR / old).unlink(missing_ok=True)
    return out


class ProfileIn(BaseModel):
    nick_them: str | None = None    # what you call them in this chat
    nick_me: str | None = None      # what they call you
    color: str | None = None        # chat theme colour ("" = back to the app's own colour)
    emoji: str | None = None        # the quick-reaction emoji (Messenger's 👍 button)


HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@app.put("/api/persons/{pid}/profile")
def update_profile(pid: int, body: ProfileIn):
    """Nicknames and the chat theme, like tapping the name at the top of a Messenger chat."""
    with db() as conn:
        row = _person_row(conn, pid)
        name = _person_dict(row)["name"]
        theme = _loads(row["theme"], {})
        if body.nick_them is not None:
            nick = body.nick_them.strip()[:40]
            if nick != (row["nick_them"] or ""):
                conn.execute("UPDATE persons SET nick_them = ? WHERE id = ?", (nick, pid))
                _event(conn, pid, f"You set the nickname for {name} to {nick}." if nick
                       else f"You removed the nickname for {name}.")
        if body.nick_me is not None:
            nick = body.nick_me.strip()[:40]
            if nick != (row["nick_me"] or ""):
                conn.execute("UPDATE persons SET nick_me = ? WHERE id = ?", (nick, pid))
                _event(conn, pid, f"You set your own nickname to {nick}." if nick else "You removed your nickname.")
        if body.color is not None:
            if body.color and not HEX_RE.match(body.color):
                raise HTTPException(400, "Colour must look like #1877f2.")
            theme["color"] = body.color
            _event(conn, pid, "You changed the chat colour." if body.color else "You went back to the normal colour.")
        if body.emoji is not None:
            theme["emoji"] = body.emoji.strip()[:8]
            _event(conn, pid, f"You changed the quick reaction to {theme['emoji']}." if theme["emoji"]
                   else "You removed the quick reaction.")
        conn.execute("UPDATE persons SET theme = ?, updated_at = ? WHERE id = ?", (json.dumps(theme), now(), pid))
        return _person_summary(conn, _person_row(conn, pid))


@app.delete("/api/persons/{pid}")
def delete_person(pid: int):
    with db() as conn:
        _person_row(conn, pid)
        paths = [r["stored_path"] for r in conn.execute(
            "SELECT stored_path FROM screenshots WHERE person_id = ? AND stored_path IS NOT NULL", (pid,))]
        conn.execute("DELETE FROM persons WHERE id = ?", (pid,))
    for p in paths:   # includes images that came from a merged profile's folder
        (UPLOAD_DIR / p).unlink(missing_ok=True)
    shutil.rmtree(UPLOAD_DIR / str(pid), ignore_errors=True)
    engine.forget(pid)
    return {"ok": True}


# ---------------------------------------------------------------- screenshots
def _read_image(img, names: list[str]) -> dict:
    """Who-said-what for one screenshot: Claude if switched on, else the local screen-aware reader."""
    if claude.enabled():
        try:
            return claude.parse_screenshot(img, names)
        except Exception as exc:  # fall back to local OCR
            print(f"[imy] Claude couldn't read a screenshot: {claude.explain(exc)}")
    try:
        screen = ocr.read_screen(img, names)
    except ocr.TesseractNotFoundError:
        raise HTTPException(503, "Tesseract OCR isn't installed or isn't on your PATH. See the README (step 1).")
    return parser.parse_screen(screen, names)


def _other_shot_messages(conn, pid: int, except_id: int | None = None) -> list[list[dict]]:
    rows = conn.execute("SELECT id FROM screenshots WHERE person_id = ? AND stored_path IS NOT NULL AND id != ?",
                        (pid, except_id or -1)).fetchall()
    return [[dict(m) for m in conn.execute("SELECT speaker, text FROM messages WHERE screenshot_id = ? ORDER BY position",
                                           (r["id"],))] for r in rows]


def _file_hash(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _save_upload(f: UploadFile, dest: Path, limit: int) -> None:
    """Copy an upload to disk, refusing files over `limit` bytes without reading them all into memory."""
    size = 0
    with dest.open("wb") as out:
        while chunk := f.file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"{f.filename}: the file is too big (max {limit // (1024 * 1024)} MB).")
            out.write(chunk)


def _find_twin(conn, pid: int, digest: str):
    """The same image already uploaded for this person? Old rows without a fingerprint get one now."""
    row = conn.execute("SELECT * FROM screenshots WHERE person_id = ? AND sha1 = ?", (pid, digest)).fetchone()
    if row:
        return row
    for r in conn.execute("SELECT id, stored_path FROM screenshots WHERE person_id = ? AND stored_path IS NOT NULL "
                          "AND sha1 IS NULL", (pid,)).fetchall():
        path = UPLOAD_DIR / r["stored_path"]
        if path.exists():
            conn.execute("UPDATE screenshots SET sha1 = ? WHERE id = ?", (_file_hash(path), r["id"]))
    return conn.execute("SELECT * FROM screenshots WHERE person_id = ? AND sha1 = ?", (pid, digest)).fetchone()


def _warning(meta: dict, names: list[str]) -> str:
    title = meta.get("title") or ""
    if title and looks.same_person(title, names) is False and not meta.get("group"):
        return f"This looks like a chat with “{title}”, not {names[0] if names else 'this person'}. Remove it if it's the wrong chat."
    if meta.get("group") and not any(looks.same_person(s, names) for s in meta.get("senders", [])):
        return "This is a group chat and none of the names matched. Check who's who."
    return ""


def _process_image(pid: int, f: UploadFile) -> dict:
    """Save, recognise and store ONE screenshot. Raises HTTPException with a friendly message on failure."""
    ext = Path(f.filename or "").suffix.lower()
    if ext in (".heic", ".heif"):
        raise HTTPException(400, f"{f.filename}: HEIC photos can't be read yet. Take a screenshot instead (it saves as PNG).")
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"{f.filename}: unsupported file type (use PNG, JPG or WEBP).")
    with db() as conn:
        person = _person_dict(_person_row(conn, pid))
    names = _known_names(person["answers"])
    folder = UPLOAD_DIR / str(pid)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{uuid.uuid4().hex}{ext}"
    _save_upload(f, dest, MAX_IMAGE_BYTES)
    digest = _file_hash(dest)
    with db() as conn:
        twin = _find_twin(conn, pid, digest)
        if twin:
            dest.unlink(missing_ok=True)
            shot = _shot_out(conn, twin)
            shot.update(duplicate=True, needs_review=False)
            return shot
    try:
        img = Image.open(dest)
        img.load()
    except Exception:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"{f.filename}: couldn't read this image.")
    try:
        parsed = _read_image(img, names)        # slow (OCR): done outside any database transaction
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        print(f"[imy] couldn't read {f.filename}:\n{traceback.format_exc()}")
        raise HTTPException(422, f"{f.filename}: imy couldn't read this screenshot. Try a clearer, uncropped one.")
    meta = parsed.get("meta") or {"platform": parsed["platform"]}
    with db() as conn:
        # Screenshots taken while scrolling overlap: skip messages another screenshot already has.
        msgs, skipped = parser.trim_overlap(parsed["messages"], _other_shot_messages(conn, pid))
        cur = conn.execute(
            "INSERT INTO screenshots (person_id, filename, stored_path, platform, raw_text, created_at, meta, sha1) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (pid, f.filename, f"{pid}/{dest.name}", parsed["platform"], parsed["raw_text"], now(), json.dumps(meta), digest))
        _store_messages(conn, pid, cur.lastrowid, msgs)
        bump_version(conn, pid)
        shot = _shot_out(conn, conn.execute("SELECT * FROM screenshots WHERE id = ?", (cur.lastrowid,)).fetchone())
    warning = _warning(meta, names)
    shot.update(needs_review=parsed["needs_review"] or bool(warning), overlap_skipped=skipped, warning=warning)
    return shot


@app.post("/api/persons/{pid}/screenshots")
def upload_screenshots(pid: int, files: list[UploadFile] = File(...), request: Request = None):
    """Read each image (chat area only, one message per bubble), drop what's already been added, store the rest.
    One bad file doesn't stop the others: it comes back with an "error" instead."""
    rate_limit(request, "upload")
    with db() as conn:
        _person_row(conn, pid)
    results = []
    for f in files:
        try:
            results.append(_process_image(pid, f))
        except HTTPException as exc:
            if len(files) == 1:
                raise
            results.append({"filename": f.filename, "error": exc.detail, "messages": []})
    return results


@app.post("/api/screenshots/{sid}/reread")
def reread_screenshot(sid: int):
    """Read a stored screenshot again with the current reader (e.g. after imy got smarter). Replaces its messages."""
    with db() as conn:
        shot = _owned(conn, "screenshots", sid, "Screenshot")
        if not shot["stored_path"]:
            raise HTTPException(404, "Screenshot not found")
        person = _person_dict(_person_row(conn, shot["person_id"]))
    path = UPLOAD_DIR / shot["stored_path"]
    if not path.exists():
        raise HTTPException(410, "The image file is missing. Remove this item and upload it again.")
    names = _known_names(person["answers"])
    parsed = _read_image(Image.open(path), names)
    meta = parsed.get("meta") or {"platform": parsed["platform"]}
    with db() as conn:
        msgs, skipped = parser.trim_overlap(parsed["messages"], _other_shot_messages(conn, shot["person_id"], sid))
        conn.execute("DELETE FROM messages WHERE screenshot_id = ?", (sid,))
        conn.execute("UPDATE screenshots SET platform = ?, raw_text = ?, flipped = 0, meta = ? WHERE id = ?",
                     (parsed["platform"], parsed["raw_text"], json.dumps(meta), sid))
        _store_messages(conn, shot["person_id"], sid, msgs)
        bump_version(conn, shot["person_id"])
        out = _shot_out(conn, conn.execute("SELECT * FROM screenshots WHERE id = ?", (sid,)).fetchone())
    warning = _warning(meta, names)
    out.update(needs_review=parsed["needs_review"] or bool(warning), overlap_skipped=skipped, warning=warning)
    return out


class TextImportIn(BaseModel):
    text: str
    person_name: str = ""


@app.post("/api/persons/{pid}/import-text")
def import_text(pid: int, body: TextImportIn):
    """Alternative to screenshots: paste a WhatsApp export or 'Name: message' lines.
    Also the best way to capture emojis, which OCR can't read."""
    if len(body.text) > 2_000_000:
        raise HTTPException(413, "That's a lot of text. Upload the chat export file instead.")
    with db() as conn:
        person = _person_dict(_person_row(conn, pid))
        names = ([body.person_name] if body.person_name.strip() else []) + _known_names(person["answers"])
        msgs, needs_review = parser.parse_text_export(body.text, names)
        if not msgs:
            raise HTTPException(400, "Couldn't find any messages. Use lines like  Name: message  or a WhatsApp export.")
        cur = conn.execute(
            "INSERT INTO screenshots (person_id, filename, stored_path, platform, raw_text, created_at) VALUES (?,?,?,?,?,?)",
            (pid, "pasted-text", None, "text-import", body.text[:20000], now()))
        _store_messages(conn, pid, cur.lastrowid, msgs)
        bump_version(conn, pid)
        shot = _shot_out(conn, conn.execute("SELECT * FROM screenshots WHERE id = ?", (cur.lastrowid,)).fetchone())
        shot["needs_review"] = needs_review
        return shot


# ---------------------------------------------------------------- chat export files
# Two steps, because one export can hold many chats and many senders:
#   1. POST import-file   read the file, save the result for a moment, show the chats in it
#   2. POST import-commit you pick the chat + which sender is them / you -> messages are stored
IMPORT_DIR = DATA_DIR / "imports"


def _cleanup_old_imports(max_age_s: int = 24 * 3600) -> None:
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    for f in IMPORT_DIR.glob("*.json"):
        if time.time() - f.stat().st_mtime > max_age_s:
            f.unlink(missing_ok=True)


@app.post("/api/persons/{pid}/import-file")
def import_file(pid: int, file: UploadFile = File(...), request: Request = None):
    """Read a chat export (any supported app; recognised by its content) and list the chats inside.
    The chat that matches this person's name is marked and listed first."""
    rate_limit(request, "import")
    with db() as conn:
        person = _person_dict(_person_row(conn, pid))
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size > MAX_EXPORT_BYTES:
        raise HTTPException(413, f"That file is too big (max {MAX_EXPORT_BYTES // (1024 * 1024)} MB). Export just this one chat.")
    try:
        convs = importers.read_export(file.filename or "", file.file)
    except importers.ExportError as exc:
        raise HTTPException(400, str(exc))
    except Exception:
        print(f"[imy] import failed:\n{traceback.format_exc()}")
        raise HTTPException(400, "imy couldn't read that file. Make sure it's the chat download from the app, not edited.")
    owner = importers.owner_name(convs)
    _cleanup_old_imports()
    token = uuid.uuid4().hex
    (IMPORT_DIR / f"{token}.json").write_text(json.dumps([
        {"id": c.id, "title": c.title, "platform": c.platform,
         "messages": [[m.sender, m.text, m.ts] for m in c.messages]} for c in convs]))
    names = _known_names(person["answers"])
    out = []
    for c in convs:
        their, mine = importers.guess_roles(c, names, owner)
        stamps = [m.ts for m in c.messages if m.ts]
        match = bool(their) and (looks.same_person(c.title, names) is not False or len(c.participants()) <= 2)
        out.append({"id": c.id, "title": c.title, "platform": c.platform, "message_count": len(c.messages),
                    "participants": c.participants(), "guess_person": their, "guess_me": mine, "match": match,
                    "first": min(stamps) if stamps else None, "last": max(stamps) if stamps else None})
    out.sort(key=lambda c: (not (c["match"] and looks.same_person(c["title"], names)), not c["match"], -c["message_count"]))
    return {"token": token, "filename": file.filename, "conversations": out}


class ImportCommitIn(BaseModel):
    token: str
    conversation_id: str
    person_name: str        # the sender who is the person you're teaching imy
    me_name: str            # the sender who is you (everyone else in a group chat is skipped)


@app.post("/api/persons/{pid}/import-commit")
def import_commit(pid: int, body: ImportCommitIn):
    if not re.fullmatch(r"[0-9a-f]{32}", body.token):
        raise HTTPException(400, "Bad import token.")
    path = IMPORT_DIR / f"{body.token}.json"
    if not path.exists():
        raise HTTPException(410, "That upload expired. Please choose the file again.")
    conv = next((c for c in json.loads(path.read_text()) if c["id"] == body.conversation_id), None)
    if not conv:
        raise HTTPException(404, "Chat not found in that file.")
    if body.person_name == body.me_name:
        raise HTTPException(400, "Pick different senders for them and for you.")
    roles = {body.person_name: "person", body.me_name: "user"}
    msgs = [{"speaker": roles[s], "text": t, "ts": ts} for s, t, ts in conv["messages"] if s in roles]
    if not any(m["speaker"] == "person" for m in msgs):
        raise HTTPException(400, f"{body.person_name} has no messages in this chat.")
    sessions = importers.split_sessions(msgs)
    title = conv["title"] or "Chat export"
    with db() as conn:
        row = _person_row(conn, pid)
        for n, chunk in enumerate(sessions, 1):
            cur = conn.execute(
                "INSERT INTO screenshots (person_id, filename, stored_path, platform, raw_text, created_at, meta) VALUES (?,?,?,?,?,?,?)",
                (pid, f"{title} · talk {n} of {len(sessions)}", None, conv["platform"], "", now(),
                 json.dumps({"platform": conv["platform"], "title": title})))
            _store_messages(conn, pid, cur.lastrowid, chunk)
        if not row["platform"] and conv["platform"] in PLATFORMS:   # nothing chosen yet: look like the app it came from
            conn.execute("UPDATE persons SET platform = ? WHERE id = ?", (conv["platform"], pid))
        bump_version(conn, pid)
    path.unlink(missing_ok=True)
    return {"sessions": len(sessions), "messages": len(msgs),
            "person_messages": sum(m["speaker"] == "person" for m in msgs),
            "user_messages": sum(m["speaker"] == "user" for m in msgs)}


@app.get("/api/persons/{pid}/screenshots")
def list_screenshots(pid: int):
    with db() as conn:
        _person_row(conn, pid)
        # "practice" = the interview's "how would they reply?" answers; they're edited in the interview, not here
        shots = conn.execute("SELECT * FROM screenshots WHERE person_id = ? AND platform != 'practice' ORDER BY id",
                             (pid,)).fetchall()
        return [_shot_out(conn, s) for s in shots]


@app.post("/api/screenshots/{sid}/flip")
def flip_screenshot(sid: int):
    """Swap user/person on every message in a screenshot (when the sides were guessed backwards)."""
    with db() as conn:
        shot = _owned(conn, "screenshots", sid, "Screenshot")
        conn.execute("UPDATE messages SET speaker = CASE speaker WHEN 'user' THEN 'person' ELSE 'user' END WHERE screenshot_id = ?", (sid,))
        conn.execute("UPDATE screenshots SET flipped = 1 - flipped WHERE id = ?", (sid,))
        bump_version(conn, shot["person_id"])
        return _shot_out(conn, conn.execute("SELECT * FROM screenshots WHERE id = ?", (sid,)).fetchone())


@app.delete("/api/screenshots/{sid}")
def delete_screenshot(sid: int):
    with db() as conn:
        shot = _owned(conn, "screenshots", sid, "Screenshot")
        conn.execute("DELETE FROM screenshots WHERE id = ?", (sid,))
        bump_version(conn, shot["person_id"])
    if shot["stored_path"]:
        (UPLOAD_DIR / shot["stored_path"]).unlink(missing_ok=True)
    return {"ok": True}


# ---------------------------------------------------------------- message corrections
class MessageEdit(BaseModel):
    text: str | None = None
    speaker: str | None = None


@app.put("/api/messages/{mid}")
def edit_message(mid: int, body: MessageEdit):
    with db() as conn:
        m = _owned(conn, "messages", mid, "Message")
        if body.speaker is not None and body.speaker not in ("user", "person"):
            raise HTTPException(400, "speaker must be 'user' or 'person'")
        conn.execute("UPDATE messages SET text = ?, speaker = ? WHERE id = ?",
                     (body.text if body.text is not None else m["text"],
                      body.speaker or m["speaker"], mid))
        bump_version(conn, m["person_id"])
        return dict(conn.execute("SELECT id, position, speaker, text FROM messages WHERE id = ?", (mid,)).fetchone())


@app.delete("/api/messages/{mid}")
def delete_message(mid: int):
    with db() as conn:
        m = _owned(conn, "messages", mid, "Message")
        conn.execute("DELETE FROM messages WHERE id = ?", (mid,))
        bump_version(conn, m["person_id"])
    return {"ok": True}


@app.post("/api/messages/{mid}/merge-up")
def merge_message_up(mid: int):
    """Glue this message onto the one before it (when one bubble was split in two)."""
    with db() as conn:
        m = _owned(conn, "messages", mid, "Message")
        prev = conn.execute("SELECT * FROM messages WHERE screenshot_id = ? AND position < ? ORDER BY position DESC LIMIT 1",
                            (m["screenshot_id"], m["position"])).fetchone()
        if not prev:
            raise HTTPException(400, "There's no message above this one.")
        conn.execute("UPDATE messages SET text = ? WHERE id = ?", (prev["text"] + " " + m["text"], prev["id"]))
        conn.execute("DELETE FROM messages WHERE id = ?", (mid,))
        bump_version(conn, m["person_id"])
    return {"ok": True}


# ---------------------------------------------------------------- sharing photos and files in the chat
def _media_out(row) -> dict:
    return {"id": row["id"], "url": f"/uploads/{row['stored_path']}", "kind": row["kind"], "mime": row["mime"],
            "filename": row["filename"], "width": row["width"], "height": row["height"],
            "bytes": row["bytes"], "description": row["description"], "created_at": row["created_at"]}


@app.post("/api/persons/{pid}/media")
def upload_media(pid: int, files: list[UploadFile] = File(...), request: Request = None):
    """Share a photo or file in the chat. It's saved with this person, and - if a free AI that can see
    images is set up - imy writes down what's in it, so their reply can be about the photo itself."""
    rate_limit(request, "upload")
    with db() as conn:
        _person_row(conn, pid)
    out = []
    for f in files[:10]:
        data = f.file.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            out.append({"filename": f.filename, "error": f"too big (max {MAX_IMAGE_BYTES // (1024 * 1024)} MB)"})
            continue
        try:
            saved = media_mod.save(UPLOAD_DIR / str(pid) / "media", f.filename or "photo.jpg", data)
        except media_mod.MediaError as exc:
            out.append({"filename": f.filename, "error": str(exc)})
            continue
        path = UPLOAD_DIR / str(pid) / "media" / saved["name"]
        desc = media_mod.describe(path, saved["kind"], saved["filename"])   # slow: done before any database lock
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO media (person_id, stored_path, filename, kind, mime, bytes, width, height, description, sender, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,'user',?)",
                (pid, f"{pid}/media/{saved['name']}", saved["filename"], saved["kind"], saved["mime"],
                 saved["bytes"], saved["width"], saved["height"], desc, now()))
            out.append(_media_out(conn.execute("SELECT * FROM media WHERE id = ?", (cur.lastrowid,)).fetchone()))
    if len(files) == 1 and out and out[0].get("error"):
        raise HTTPException(400, f"{out[0]['filename']}: {out[0]['error']}")
    return out


@app.get("/uploads/{path:path}")
def serve_upload(path: str):
    """Screenshots, profile pictures and shared photos.

    These used to be handed to anyone who knew the address. Every uploaded file is stored under the
    id of the person it belongs to, so the first part of the path says who owns it - and you only
    get the file if that person is yours. `resolve()` is what stops a path like `../../` from
    reaching out of the uploads folder."""
    base = UPLOAD_DIR.resolve()
    target = (base / path).resolve()
    owner = path.split("/")[0] if path else ""
    if not owner.isdigit() or base not in target.parents or not target.is_file():
        raise HTTPException(404, "Not found")
    with db() as conn:
        _person_row(conn, int(owner))      # 404s unless this person belongs to you
    # "private" so only this browser caches it, never a shared cache along the way.
    return FileResponse(target, headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/persons/{pid}/media")
def list_media(pid: int):
    """Everything shared in this chat, newest first - the "Media & files" screen."""
    with db() as conn:
        _person_row(conn, pid)
        rows = conn.execute(
            "SELECT m.* FROM media m WHERE m.person_id = ? AND NOT EXISTS "
            "(SELECT 1 FROM chat_messages c WHERE c.media_id = m.id AND c.deleted = 1) ORDER BY m.id DESC LIMIT 300",
            (pid,)).fetchall()
        return [_media_out(r) for r in rows]


# ---------------------------------------------------------------- chat
class ChatIn(BaseModel):
    message: str = ""
    messages: list[str] = []      # several messages sent in a row ("burst"): answered together, like a person would
    media_ids: list[int] = []     # photos or files sent with this message
    reply_to: int | None = None   # the message you're replying to


CHAT_COLS = ("SELECT c.id, c.role, c.text, c.kind, c.debug, c.created_at, c.reactions, c.reply_to, c.deleted, "
             "c.media_id FROM chat_messages c WHERE c.person_id = ?")
PAGE = 60


def _in(ids) -> str:
    return ",".join("?" * len(ids))


def _chat_out(conn, rows) -> list[dict]:
    """Add each message's photo and the message it replies to."""
    media_ids = sorted({r["media_id"] for r in rows if r["media_id"]})
    media = {r["id"]: _media_out(r) for r in conn.execute(
        f"SELECT * FROM media WHERE id IN ({_in(media_ids)})", media_ids)} if media_ids else {}
    quoted_ids = sorted({r["reply_to"] for r in rows if r["reply_to"]})
    quotes = {}
    if quoted_ids:
        q = conn.execute(f"SELECT id, role, text, kind, media_id FROM chat_messages WHERE id IN ({_in(quoted_ids)})",
                         quoted_ids).fetchall()
        quotes = {r["id"]: {"id": r["id"], "role": r["role"], "text": r["text"], "kind": r["kind"],
                            "photo": bool(r["media_id"])} for r in q}
    out = []
    for r in rows:
        m = {"id": r["id"], "role": r["role"], "kind": r["kind"] or "text", "created_at": r["created_at"],
             "text": "" if r["deleted"] else r["text"], "deleted": bool(r["deleted"]),
             "debug": _loads(r["debug"], None), "reactions": _loads(r["reactions"], {}),
             "media": media.get(r["media_id"]) if not r["deleted"] else None,
             "quote": quotes.get(r["reply_to"]), "reply_to": r["reply_to"]}
        out.append(m)
    return out


@app.get("/api/persons/{pid}/chat")
def get_chat(pid: int, before: int | None = None, limit: int = PAGE):
    """The conversation, newest page first. Everything is kept on the server, so closing the browser
    (or restarting imy) loses nothing: scrolling up loads the older messages."""
    limit = max(10, min(200, limit))
    with db() as conn:
        _person_row(conn, pid)
        sql = CHAT_COLS + (" AND c.id < ?" if before else "") + " ORDER BY c.id DESC LIMIT ?"
        args = [pid] + ([before] if before else []) + [limit + 1]
        rows = conn.execute(sql, args).fetchall()
        more = len(rows) > limit
        rows = rows[:limit][::-1]
        return {"messages": _chat_out(conn, rows), "has_more": more}


SAFE_REPLIES = ["wait lang", "teka", "hmm", "one sec"]


def _history_line(row) -> dict:
    """One past message as the reply engine sees it: a photo becomes the words describing it."""
    text = row["text"] or ""
    if row["kind"] == "media" and row["description"]:
        text = f"[sent a photo: {row['description'][:200]}]" + (f" {text}" if text else "")
    return {"role": row["role"], "text": text}


def _maybe_react(index, texts: list[str], rng_seed: int) -> str:
    """Sometimes a person reacts to your message as well as replying. Uses their own favourite emojis."""
    rng = random.Random(rng_seed)
    if rng.random() > 0.14 or not texts or len(texts[-1]) < 4:
        return ""
    favs = (index.style.get("top_emojis") or [])[:4] or ["❤️", "😂", "🥹"]
    return rng.choice(favs)


@app.post("/api/persons/{pid}/chat")
def send_chat(pid: int, body: ChatIn, request: Request = None):
    rate_limit(request, "chat")
    texts = [t.strip()[:4000] for t in (body.messages or [body.message]) if t and t.strip()][:12]
    if not texts and not body.media_ids:
        raise HTTPException(400, "Message is empty.")
    sent, shared = [], []
    with db() as conn:            # 1. save what you said (a short write)
        person = _person_for_engine(conn, pid)
        history = [_history_line(r) for r in conn.execute(
            "SELECT c.role, c.text, c.kind, m.description FROM chat_messages c LEFT JOIN media m ON m.id = c.media_id "
            "WHERE c.person_id = ? AND c.kind != 'event' AND c.deleted = 0 ORDER BY c.id DESC LIMIT 16", (pid,)).fetchall()][::-1]
        for mid in body.media_ids[:10]:       # photos first, then your words, like in a real chat
            row = conn.execute("SELECT * FROM media WHERE id = ? AND person_id = ?", (mid, pid)).fetchone()
            if not row:
                continue
            shared.append(row["description"] or "a photo")
            cur = conn.execute("INSERT INTO chat_messages (person_id, role, text, kind, media_id, reply_to, created_at) "
                               "VALUES (?,?,'','media',?,?,?)", (pid, "user", mid, body.reply_to, now()))
            sent.append(cur.lastrowid)
        for n, t in enumerate(texts):
            cur = conn.execute("INSERT INTO chat_messages (person_id, role, text, reply_to, created_at) VALUES (?,?,?,?,?)",
                               (pid, "user", t, body.reply_to if n == 0 and not sent else None, now()))
            sent.append(cur.lastrowid)
        quote = conn.execute("SELECT text FROM chat_messages WHERE id = ? AND person_id = ?",
                             (body.reply_to, pid)).fetchone() if body.reply_to else None
    # Their training messages are fetched only if the engine actually needs to rebuild its index -
    # usually it doesn't, because the index is cached. Reading thousands of rows on every message
    # just to discard them is the kind of waste that only shows up once lots of people are chatting.
    training = lambda: _load_training(pid)
    history += [{"role": "user", "text": t} for t in texts[:-1]]
    said = "\n".join(texts) if texts else ""
    if quote and quote["text"]:
        said = f'(replying to your message "{quote["text"][:120]}") {said}'.strip()
    try:                          # 2. think (no database lock held: other requests keep working)
        reply = engine.generate_reply(person, training, history, said, shared or None)
        bubbles = [b for b in reply.bubbles if b and b.strip()] or [SAFE_REPLIES[0]]
        debug = reply.debug()
    except Exception:
        print(f"[imy] reply failed:\n{traceback.format_exc()}")
        bubbles, debug = [SAFE_REPLIES[len(history) % len(SAFE_REPLIES)]], {"source": "safe", "note": "Something went wrong; sent a neutral reply."}
    out, react = [], ""
    with db() as conn:            # 3. save the reply (another short write)
        for i, bubble in enumerate(bubbles):
            last = i == len(bubbles) - 1
            cur = conn.execute("INSERT INTO chat_messages (person_id, role, text, debug, created_at) VALUES (?,?,?,?,?)",
                               (pid, "person", bubble, json.dumps(debug) if last else None, now()))
            out.append({"id": cur.lastrowid, "role": "person", "text": bubble, "kind": "text",
                        "created_at": now(), "debug": debug if last else None, "reactions": {}})
        try:
            react = _maybe_react(engine.get_index(person, training), texts, sent[-1] if sent else 0)
            if react and sent:
                conn.execute("UPDATE chat_messages SET reactions = ? WHERE id = ?",
                             (json.dumps({"person": react}), sent[-1]))
        except Exception:
            react = ""
        rows = conn.execute(CHAT_COLS + f" AND c.id IN ({_in(sent)}) ORDER BY c.id", [pid] + sent).fetchall() if sent else []
        mine = _chat_out(conn, rows)
    notes.update_later(pid, person["name"])      # keep their memory of this chat up to date, in the background
    return {"bubbles": out, "sent": mine, "reaction": {"id": sent[-1], "emoji": react} if react and sent else None}


class ReactIn(BaseModel):
    emoji: str = ""      # "" removes your reaction
    who: str = "user"    # "user" = you, "person" = them


@app.post("/api/chat-messages/{mid}/react")
def react_to_message(mid: int, body: ReactIn):
    """Tap a message to react to it, like every chat app. Tapping the same emoji again removes it."""
    if body.who not in ("user", "person"):
        raise HTTPException(400, "who must be 'user' or 'person'")
    with db() as conn:
        row = _owned(conn, "chat_messages", mid, "Message")
        reactions = _loads(row["reactions"], {})
        emoji = body.emoji.strip()[:8]
        if not emoji or reactions.get(body.who) == emoji:
            reactions.pop(body.who, None)
        else:
            reactions[body.who] = emoji
        conn.execute("UPDATE chat_messages SET reactions = ? WHERE id = ?", (json.dumps(reactions), mid))
        return {"id": mid, "reactions": reactions}


@app.delete("/api/chat-messages/{mid}")
def unsend_message(mid: int):
    """Unsend: the bubble stays as "You unsent a message", exactly like Messenger."""
    with db() as conn:
        _owned(conn, "chat_messages", mid, "Message")
        conn.execute("UPDATE chat_messages SET deleted = 1, text = '' WHERE id = ?", (mid,))
        return {"id": mid, "deleted": True}


@app.delete("/api/persons/{pid}/chat")
def clear_chat(pid: int, media: bool = False):
    """Clear the conversation. The photos you shared stay unless you ask for those too."""
    with db() as conn:
        _person_row(conn, pid)
        paths = [r["stored_path"] for r in conn.execute("SELECT stored_path FROM media WHERE person_id = ?", (pid,))] if media else []
        conn.execute("DELETE FROM chat_messages WHERE person_id = ?", (pid,))
        if media:
            conn.execute("DELETE FROM media WHERE person_id = ?", (pid,))
        conn.execute("UPDATE persons SET memory = '' WHERE id = ?", (pid,))
    for p in paths:
        (UPLOAD_DIR / p).unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/persons/{pid}/memory")
def get_memory(pid: int):
    """What they remember from your conversations (you can see it and clear it)."""
    with db() as conn:
        row = _person_row(conn, pid)
        return {"notes": notes.load(row["memory"] or "")}


@app.delete("/api/persons/{pid}/memory")
def clear_memory(pid: int):
    with db() as conn:
        _person_row(conn, pid)
        conn.execute("UPDATE persons SET memory = '' WHERE id = ?", (pid,))
    return {"ok": True}


ENDEARMENTS = ["love", "babe", "baby", "bby", "bebe", "beb", "mahal", "mhie", "hon", "honey", "beh", "bes", "besh",
               "sis", "mars", "bro", "pre", "dude", "bub", "bubba", "lab", "langga", "palangga"]


@app.get("/api/persons/{pid}/guesses")
def get_guesses(pid: int):
    """'imy noticed…' cards for the interview: things measured from the real messages.
    Each says which answer to fill in if you confirm it (mode 'add' appends to a comma list)."""
    with db() as conn:
        person = _person_for_engine(conn, pid)
        msgs = [m for m in _messages_for_engine(conn, pid) if m.get("shot_platform") not in ("practice", "correction")]
    texts = [m["text"] for m in msgs if m["speaker"] == "person"]
    if len(texts) < 10:
        return []
    st = style_mod.build_style(texts, {})
    answers = person["answers"]
    has = lambda qid, v: v.lower() in (answers.get(qid) or "").lower()
    out = []
    low = [t.lower() for t in texts]
    counts = {e: sum(bool(re.search(rf"\b{e}\b", t)) for t in low) for e in ENDEARMENTS}
    for e, c in sorted(counts.items(), key=lambda kv: -kv[1])[:2]:
        if c >= 3 and not has("q3", e):
            out.append({"id": f"end_{e}", "text": f"They call you “{e}” a lot", "field": "q3", "value": e, "mode": "add"})
    if st["laugh"] and not answers.get("x_laugh"):
        out.append({"id": "laugh", "text": f"They laugh with “{st['laugh']}”", "field": "x_laugh", "value": st["laugh"], "mode": "set"})
    if st["lowercase_start"] is not None and st["lowercase_start"] >= 0.75 and not answers.get("x_case"):
        out.append({"id": "case", "text": "They type in all lowercase", "field": "x_case", "value": "all lowercase, no periods", "mode": "set"})
    if not answers.get("q11"):
        if st["median_words"] <= 5:
            out.append({"id": "len", "text": "They text in short, quick messages", "field": "q11",
                        "value": "Short, quick messages, never much", "mode": "set"})
        elif st["median_words"] > 16:
            out.append({"id": "len", "text": "They write long paragraphs", "field": "q11", "value": "One long paragraph", "mode": "set"})
    if st["top_emojis"] and st["emoji_ratio"] >= 0.1 and not answers.get("x_emoji_fav"):
        fav = " ".join(st["top_emojis"][:3])
        out.append({"id": "emoji", "text": f"Their favorite emojis: {fav}", "field": "x_emoji_fav", "value": fav, "mode": "set"})
    elif st["emoji_ratio"] < 0.03 and not answers.get("q12"):
        out.append({"id": "noemoji", "text": "They barely use emojis", "field": "q12", "value": "No emojis, ever", "mode": "set"})
    tl = sum(bool(engine.TAGALOG_RE.search(t)) for t in texts) / len(texts)
    if tl >= 0.25 and not answers.get("x_lang"):
        out.append({"id": "lang", "text": "They text in Taglish", "field": "x_lang", "value": "Taglish", "mode": "set"})
    for phrase, _ in st["top_phrases"][:2]:
        if not has("q13", phrase):
            out.append({"id": f"phrase_{phrase}", "text": f"They often say “{phrase}”", "field": "q13", "value": phrase, "mode": "add"})
    return out


# ---------------------------------------------------------------- teaching from the chat
class TeachIn(BaseModel):
    prompt: str             # what you said
    reply: str              # what they would really say (a fix), or the reply imy gave that sounded right (a 👍)


@app.post("/api/persons/{pid}/teach")
def teach(pid: int, body: TeachIn):
    """Save "when I say X, they'd say Y" from the chat. Stored with the person's other chats (one item
    called "Taught in chat", editable in Review) and used before anything else when you say something similar."""
    prompt, reply = body.prompt.strip(), body.reply.strip()
    if not prompt or not reply:
        raise HTTPException(400, "Both your message and their reply are needed.")
    with db() as conn:
        _person_row(conn, pid)
        row = conn.execute("SELECT id FROM screenshots WHERE person_id = ? AND platform = 'correction'", (pid,)).fetchone()
        sid = row["id"] if row else conn.execute(
            "INSERT INTO screenshots (person_id, filename, stored_path, platform, raw_text, created_at) VALUES (?,?,?,?,?,?)",
            (pid, "Taught in chat", None, "correction", "", now())).lastrowid
        start = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM messages WHERE screenshot_id = ?", (sid,)).fetchone()[0]
        lines = [{"speaker": "user", "text": prompt}] + [{"speaker": "person", "text": l.strip()} for l in reply.splitlines() if l.strip()]
        conn.executemany("INSERT INTO messages (person_id, screenshot_id, position, speaker, text) VALUES (?,?,?,?,?)",
                         [(pid, sid, start + i, m["speaker"], m["text"]) for i, m in enumerate(lines)])
        bump_version(conn, pid)
        taught = conn.execute("SELECT COUNT(*) FROM messages WHERE screenshot_id = ? AND speaker = 'user'", (sid,)).fetchone()[0]
    return {"ok": True, "taught": taught}


# ---------------------------------------------------------------- merging duplicates
class MergeIn(BaseModel):
    from_id: int


@app.post("/api/persons/{pid}/merge")
def merge_person(pid: int, body: MergeIn):
    """Move everything learned about another person (screenshots, chats, answers they're missing) into this one,
    then remove the other one. For when the same person was set up twice."""
    if body.from_id == pid:
        raise HTTPException(400, "Pick a different person to merge in.")
    with db() as conn:
        target = _person_dict(_person_row(conn, pid))
        other = _person_dict(_person_row(conn, body.from_id))
        answers = {**{k: v for k, v in other["answers"].items() if (v or "").strip()},
                   **{k: v for k, v in target["answers"].items() if (v or "").strip()}}   # this person's answers win
        conn.execute("UPDATE screenshots SET person_id = ? WHERE person_id = ? AND platform NOT IN ('practice', 'correction')",
                     (pid, body.from_id))
        conn.execute("UPDATE messages SET person_id = ? WHERE screenshot_id IN (SELECT id FROM screenshots WHERE person_id = ?)",
                     (pid, pid))
        for kind in ("correction",):   # their chat corrections join this person's
            rows = conn.execute("SELECT id FROM screenshots WHERE person_id = ? AND platform = ?", (body.from_id, kind)).fetchall()
            for r in rows:
                conn.execute("UPDATE screenshots SET person_id = ?, filename = 'Taught in chat (merged)' WHERE id = ?", (pid, r["id"]))
                conn.execute("UPDATE messages SET person_id = ? WHERE screenshot_id = ?", (pid, r["id"]))
        conn.execute("UPDATE persons SET answers = ? WHERE id = ?", (json.dumps(answers), pid))
        _sync_practice(conn, pid, answers)
        conn.execute("DELETE FROM persons WHERE id = ?", (body.from_id,))   # its uploaded images stay on disk; they're still used
        bump_version(conn, pid)
        engine.forget(body.from_id)
        return _person_summary(conn, _person_row(conn, pid))


@app.get("/api/persons/{pid}/style")
def get_style(pid: int):
    with db() as conn:
        person = _person_for_engine(conn, pid)
        msgs = _messages_for_engine(conn, pid)
    index = engine.get_index(person, msgs)
    return {"lines": style_mod.describe_style(index.style), "embedder": index.space.kind,
            "exchanges": len(index.prompts), "messages": index.style["n_messages"],
            "ai": llm.status()}


# ---------------------------------------------------------------- feedback from clients, and visit counts
class FeedbackIn(BaseModel):
    kind: str = "other"         # idea | problem | love | other
    message: str = ""
    rating: int | None = None   # 1-5, optional
    can_reply: bool = False     # "you can email me about this"
    page: str = ""              # which screen they sent it from


@app.post("/api/feedback")
def send_feedback(body: FeedbackIn, request: Request = None):
    rate_limit(request, "feedback")
    me = auth.current()
    try:
        out = feedback_mod.add(me["id"], body.kind, body.message, body.rating, body.can_reply, body.page)
    except feedback_mod.FeedbackError as exc:
        raise HTTPException(400, str(exc))
    # Let the admins know (IMY_FEEDBACK_EMAILS=0 turns this off). Only when email really works:
    # a notice that can't be sent shouldn't fill the server log.
    if mailer.configured() and os.environ.get("IMY_FEEDBACK_EMAILS", "1") != "0":
        site = mailer.site_url(request)
        mail = mailer.feedback_email(feedback_mod.KINDS[out["kind"]], body.message.strip(), body.rating,
                                     me["email"] if body.can_reply else None, site)
        for admin in insights.admin_emails():
            mailer.send_later(admin, *mail)
    return {"ok": True, **out}


class VisitIn(BaseModel):
    page: str = ""              # landing | app


@app.post("/api/visit")
def count_visit(body: VisitIn, request: Request = None):
    """One page view, counted without cookies (see insights.py). Always answers ok: whether it was
    counted (bots and Do Not Track aren't) is nobody's business but the admin page's."""
    rate_limit(request, "visit")
    if request is not None:
        insights.count_visit(body.page, visitor(request), request.headers.get("user-agent", ""), request.headers)
    return {"ok": True}


# ---------------------------------------------------------------- the admin page (/admin)
# Its own door, separate from clients: an emailed one-time link (no password), its own cookie that
# only ever goes to /api/admin/..., and nothing here can read anyone's chats. See insights.py.
class AdminLinkIn(BaseModel):
    email: str = ""


class AdminLoginIn(BaseModel):
    token: str = ""


class FeedbackStatusIn(BaseModel):
    status: str = ""


@app.get("/api/admin/me")
def admin_me():
    return {"admin": insights.ADMIN.get(), "email_works": mailer.configured()}


@app.post("/api/admin/link")
def admin_link(body: AdminLinkIn, request: Request = None):
    """Email a sign-in link if this is an admin address. Same answer either way."""
    rate_limit(request, "admin")
    email = (body.email or "").strip().lower()
    if not auth.EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(400, "That doesn't look like an email address.")
    site = mailer.site_url(request)
    local = mailer.is_local(site)
    if not site or not (mailer.configured() or local):
        raise HTTPException(503, "imy can't send email yet, so the admin link can't be sent. Set IMY_SMTP_HOST and "
                                 "IMY_PUBLIC_URL (see backend/mailer.py).")
    token = insights.start_admin_link(email)
    if not token and local:
        # Your own computer: no need to keep it a secret who the admins are - say why, here and in the Terminal.
        admins = insights.admin_emails()
        why = (f"{email} isn't an admin address on this computer. Admins: {', '.join(admins)}." if admins and email not in admins
               else "That address has asked for too many links this hour. Wait a little, or use the last one." if admins
               else "No admin address is set. Add IMY_ADMIN_EMAILS=you@example.com to the .env file, then restart imy.")
        print(f"[imy] no admin link sent: {why}", flush=True)
        return {"ok": False, "minutes": insights.LINK_MINUTES, "printed": local, "hint": why}
    if token:
        link = f"{site}/admin#login={token}"
        mailer.send_later(email, *mailer.admin_link_email(link, insights.LINK_MINUTES), print_if_off=local)
        if local and mailer.configured():
            # Your own computer: also print it, so a mistyped App Password doesn't lock you out of testing.
            print(f"[imy] admin sign-in link (shown because this is your own computer): {link}", flush=True)
    return {"ok": True, "minutes": insights.LINK_MINUTES, "printed": local}


@app.post("/api/admin/login")
def admin_login(body: AdminLoginIn, request: Request = None):
    rate_limit(request, "auth")
    got = insights.use_admin_link(body.token)
    if not got:
        raise HTTPException(400, "This link has expired or was already used. Ask for a new one.")
    email, session = got
    resp = JSONResponse({"admin": {"email": email}})
    # samesite=strict: the cookie is never sent when another website links or posts here.
    resp.set_cookie(insights.ADMIN_COOKIE, session, max_age=insights.SESSION_HOURS * 3600, httponly=True,
                    samesite="strict", secure=auth.secure_cookies(request), path=insights.ADMIN_COOKIE_PATH)
    return resp


@app.post("/api/admin/logout")
def admin_logout(request: Request = None):
    if request:
        insights.end_admin(request.cookies.get(insights.ADMIN_COOKIE, ""))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(insights.ADMIN_COOKIE, path=insights.ADMIN_COOKIE_PATH)
    return resp


@app.get("/api/admin/stats")
def admin_stats(days: int = 30):
    return insights.stats(max(7, min(days, 365)))


@app.get("/api/admin/accounts")
def admin_accounts(q: str = ""):
    return insights.accounts(q[:100])


@app.get("/api/admin/feedback")
def admin_feedback(status: str = ""):
    return feedback_mod.listing(status)


@app.put("/api/admin/feedback/{fid}")
def admin_feedback_status(fid: int, body: FeedbackStatusIn):
    try:
        if not feedback_mod.set_status(fid, body.status):
            raise HTTPException(404, "That feedback isn't there any more.")
    except feedback_mod.FeedbackError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.delete("/api/admin/feedback/{fid}")
def admin_feedback_delete(fid: int):
    if not feedback_mod.delete(fid):
        raise HTTPException(404, "That feedback isn't there any more.")
    return {"ok": True}


# ---------------------------------------------------------------- privacy + terms pages
# Both are plain HTML files in frontend/public (copied to dist when the site is built). They say
# {{CONTACT_EMAIL}} wherever your contact address goes, and it's filled in here from IMY_CONTACT_EMAIL,
# so the address lives in your settings instead of being typed into the pages and rebuilt.
LEGAL_PAGES = {"privacy.html", "terms.html"}


def contact_email() -> str:
    return os.environ.get("IMY_CONTACT_EMAIL", "").strip()


def _legal_page(name: str):
    for folder in (FRONTEND_DIST, ROOT / "frontend" / "public"):
        f = folder / name
        if f.is_file():
            email = contact_email()
            link = (f'<a href="mailto:{html.escape(email)}">{html.escape(email)}</a>' if email
                    else "<em>(the person running this site hasn't added a contact address yet)</em>")
            return HTMLResponse(f.read_text(encoding="utf-8").replace("{{CONTACT_EMAIL}}", link))
    raise HTTPException(404, "Not found")


@app.get("/privacy.html")
def privacy_page():
    return _legal_page("privacy.html")


@app.get("/terms.html")
def terms_page():
    return _legal_page("terms.html")


# ---------------------------------------------------------------- serve the built frontend (optional)
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")
    _DIST = FRONTEND_DIST.resolve()

    @app.get("/{path:path}")
    def spa(path: str):
        """The landing page at the main address; the app itself at /app (its screens all live in the one
        page, so /app/anything is the app too); and the files the pages use (/media/..., /privacy.html...)."""
        if path.startswith("api/"):
            raise HTTPException(404, "Not found")
        if path == "app" or path.startswith("app/"):
            return FileResponse(FRONTEND_DIST / "index.html")
        if path in ("admin", "admin/") and (FRONTEND_DIST / "admin.html").is_file():
            return FileResponse(FRONTEND_DIST / "admin.html")
        target = (FRONTEND_DIST / path).resolve()
        inside = target == _DIST or _DIST in target.parents     # never serve files outside frontend/dist
        if path and inside and target.is_file():
            return FileResponse(target)
        landing = FRONTEND_DIST / "landing.html"
        return FileResponse(landing if landing.is_file() else FRONTEND_DIST / "index.html")
