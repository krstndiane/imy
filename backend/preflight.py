"""preflight.py - check everything on the launch checklist that a computer can check.

Run it on the server (or wherever imy will run) with the same settings imy uses:

    python -m backend.preflight                          settings, database, backups, disk, build
    python -m backend.preflight --url https://your.site  also checks the live site from outside

It prints one line per check - OK, WARN or FAIL - and what to do about anything that isn't OK.
It exits with 1 if anything FAILs, so a deploy script can stop on it. It never changes anything:
no accounts are made, nothing is deleted, no backup is written.
"""
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import claude  # noqa: F401  (loads .env, the same way imy does)
from . import backup, usage
from .db import DATA_DIR, DB_PATH

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "frontend" / "dist"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")

results: list[tuple[str, str, str]] = []      # (level, what, advice)


def ok(what: str, advice: str = "") -> None:
    results.append(("OK", what, advice))


def warn(what: str, advice: str) -> None:
    results.append(("WARN", what, advice))


def fail(what: str, advice: str) -> None:
    results.append(("FAIL", what, advice))


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# ---------------------------------------------------------------- settings
def check_settings() -> None:
    if env("GROQ_API_KEY"):
        ok("Groq key is set")
    else:
        warn("No GROQ_API_KEY", "Replies will all be imy's own (no AI). Add a key from console.groq.com/keys.")

    if env("GEMINI_API_KEY") or env("GOOGLE_API_KEY"):
        if env("IMY_GEMINI_PAID") == "1":
            ok("Gemini is on, with billing")
        else:
            fail("Gemini key is set on the FREE tier",
                 "Google's free tier may let people read what's sent. Turn on billing and set IMY_GEMINI_PAID=1, "
                 "or remove GEMINI_API_KEY. The privacy page depends on this.")

    if env("ANTHROPIC_API_KEY"):
        warn("ANTHROPIC_API_KEY is set",
             "Claude can read screenshots, but the privacy page says screenshot images are never sent anywhere. "
             "Remove the key, or update privacy.html to say screenshots may go to Anthropic.")

    email = env("IMY_CONTACT_EMAIL")
    if not email:
        fail("No IMY_CONTACT_EMAIL", "The privacy and terms pages need a real address for deletion requests. "
             "Set IMY_CONTACT_EMAIL in your host's settings.")
    elif not EMAIL_RE.match(email):
        fail(f"IMY_CONTACT_EMAIL doesn't look like an email ({email!r})", "Fix the address.")
    else:
        ok(f"Contact email is set ({email})")

    origins = [o.strip() for o in env("IMY_ORIGINS").split(",") if o.strip()]
    plain = [o for o in origins if o.startswith("http://") and "localhost" not in o and "127.0.0.1" not in o]
    if plain:
        fail(f"IMY_ORIGINS has a plain http:// address ({', '.join(plain)})", "Use the https:// address.")
    elif origins and all(o.startswith("http://localhost") or o.startswith("http://127.0.0.1") for o in origins):
        warn("IMY_ORIGINS only lists localhost", "Fine on your computer. On the server, leave it unset when the "
             "site and API share one address, or set it to your https:// address.")
    else:
        ok("IMY_ORIGINS is " + (", ".join(origins) if origins else "unset (site and API on one address)"))

    flag = env("IMY_SECURE_COOKIES")
    if flag == "0":
        fail("IMY_SECURE_COOKIES=0", "The sign-in cookie would be sent over plain http too. Remove the setting.")
    else:
        ok("Sign-in cookie: Secure on HTTPS" + (" (forced on)" if flag == "1" else " (decided per visit)"))

    if env("IMY_RATE_LIMIT") == "0":
        fail("IMY_RATE_LIMIT=0", "Rate limiting is off, so anyone can hammer sign-in or use up the AI. Remove it.")
    else:
        ok("Rate limits are on")

    per = usage.per_account()
    if per == 0:
        warn("No daily AI limit per account (IMY_AI_DAILY_PER_ACCOUNT=0)",
             "One busy account can use up the whole site's free AI allowance. 30-60 is a fair share.")
    else:
        ok(f"Daily AI limit: {per} per account" + (f", {usage.per_site()} for the whole site" if usage.per_site() else ""))

    if not env("TZ"):
        warn("TZ isn't set", "Set TZ=Asia/Manila so the daily AI limit resets at your midnight (the Dockerfile does).")
    else:
        ok(f"Time zone {env('TZ')}")


# ---------------------------------------------------------------- data, backups, disk
def check_data() -> None:
    if not DB_PATH.exists():
        warn(f"No database yet at {DB_PATH}", "Normal before the first start. imy creates it.")
        return
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        accounts = conn.execute("SELECT email FROM users").fetchall() if "users" in tables else []
        conn.close()
    except sqlite3.Error as exc:
        fail("Couldn't open the database", str(exc))
        return
    (ok if quick == "ok" else fail)(f"Database check: {quick}", "" if quick == "ok" else "Restore the last good backup.")
    (ok if mode == "wal" else warn)(f"Journal mode: {mode}", "" if mode == "wal" else "Start imy once; it switches to WAL.")

    test = [e for (e,) in accounts if re.search(r"@(example\.(com|org|net)|test\.|localhost)|^test", e, re.I)]
    if test:
        warn(f"{len(test)} test-looking account(s): {', '.join(test[:5])}{' ...' if len(test) > 5 else ''}",
             "Remove them before launch: python -m backend.admin remove EMAIL --yes")
    else:
        ok(f"{len(accounts)} account(s), none look like test accounts")

    free = shutil.disk_usage(DATA_DIR).free / 1073741824
    if free < 2:
        fail(f"Only {free:.1f} GB free where imy keeps its data", "Grow the disk. Plan 15-20 GB per 1,000 active people.")
    elif free < 10:
        warn(f"{free:.1f} GB free where imy keeps its data", "Fine to start. Plan 15-20 GB per 1,000 active people.")
    else:
        ok(f"{free:.0f} GB free for data")


def check_backups() -> None:
    if env("IMY_BACKUP_DAILY") != "1":
        fail("Daily backups are off", "Set IMY_BACKUP_DAILY=1.")
    else:
        ok(f"Daily backups on, keeping {backup.KEEP}")
    bdir = backup.BACKUP_DIR.resolve()
    if DATA_DIR.resolve() in [bdir, *bdir.parents]:
        warn(f"Backups are on the same disk as the data ({bdir})",
             "That protects against mistakes, not a lost disk. Also turn on your host's disk snapshots, or copy "
             "a backup somewhere else every week.")
    newest = backup._newest_first()[0] if backup.BACKUP_DIR.exists() and backup._newest_first() else None
    if not newest:
        warn("No backup made yet", "Make one now and prove it: python -m backend.backup, then --check on the file.")
        return
    hours = (time.time() - newest.stat().st_mtime) / 3600
    (ok if hours < 26 else warn)(f"Newest backup is {hours:.0f} h old ({newest.name})",
                                 "" if hours < 26 else "Daily backups should make one every 24 h. Is imy running?")
    info = backup.check(newest)
    if info.get("ok"):
        ok(f"Newest backup opens and checks out ({sum(info['rows'].values())} rows, {info['uploads']} files)")
    else:
        fail("Newest backup is damaged", info.get("error") or "Make a new one and check it.")


# ---------------------------------------------------------------- the built website
def check_build() -> None:
    index = DIST / "index.html"
    if not index.exists():
        fail("The website isn't built (frontend/dist/index.html missing)", "Build the frontend before deploying.")
        return
    html = index.read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="/?(assets/[^"]+)"', html)
    missing = [r for r in refs if not (DIST / r).exists()]
    (fail if missing else ok)("Website build" + (f" is missing {missing}" if missing else " is complete"),
                              "Rebuild the frontend." if missing else "")
    for page in ("privacy.html", "terms.html"):
        f = DIST / page
        if not f.exists():
            fail(f"{page} isn't in the build", "Rebuild the frontend (it copies frontend/public).")
        elif "{{CONTACT_EMAIL}}" not in f.read_text(encoding="utf-8"):
            warn(f"{page} has no {{{{CONTACT_EMAIL}}}} spot", "The contact address won't be filled in automatically.")
    old = sorted(p.name for p in (DIST / "assets").glob("index-*") if f"assets/{p.name}" not in refs)
    if old:
        warn(f"{len(old)} old build file(s) in frontend/dist/assets", "Harmless, but they ship with the site. "
             "Delete the ones index.html doesn't use.")


def check_repo() -> None:
    if not (ROOT / ".git").exists():
        return
    import subprocess
    try:
        tracked = subprocess.run(["git", "ls-files", ".env"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout
        hist = subprocess.run(["git", "log", "--all", "--oneline", "--", ".env"], cwd=ROOT,
                              capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return
    if tracked.strip() or hist.strip():
        fail(".env is (or was) in git", "Rotate every key in it, then remove it from the repository history.")
    else:
        ok(".env has never been committed to git")


# ---------------------------------------------------------------- the live site, from outside
def _get(url: str, follow: bool = True):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "imy-preflight"})
    try:
        with opener.open(req, timeout=15) as r:
            return r.status, dict(r.headers), r.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read(20_000).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        return 0, {}, str(e)


def check_live(url: str, require_https: bool = True) -> None:
    url = url.rstrip("/")
    if require_https and not url.startswith("https://"):
        fail(f"{url} isn't https://", "Serve imy over HTTPS (most hosts do this for you) and check that address.")
        return
    st, _, body = _get(url + "/api/health")
    (ok if st == 200 and '"ok"' in body else fail)(f"Live health check: {st or body[:80]}",
                                                   "" if st == 200 else "Is imy running at that address?")
    if st != 200:
        return
    st, headers, _ = _get("http://" + url.split("://", 1)[1] + "/", follow=False)
    loc = headers.get("Location") or headers.get("location") or ""
    if st in (301, 302, 307, 308) and loc.startswith("https://"):
        ok("Plain http:// redirects to https://")
    elif st == 0:
        ok("Plain http:// isn't served at all")
    else:
        warn(f"http:// answered {st} without sending people to https://",
             "Turn on 'force HTTPS' (or an http-to-https redirect) at your host.")
    for page in ("privacy.html", "terms.html"):
        st, _, body = _get(f"{url}/{page}")
        if st != 200:
            fail(f"/{page} answered {st}", "Deploy the latest build.")
        elif "{{CONTACT_EMAIL}}" in body or "hasn't added a contact address" in body:
            fail(f"/{page} has no contact address", "Set IMY_CONTACT_EMAIL on the server and restart imy.")
        else:
            ok(f"/{page} is up, with a contact address")
    st, _, _ = _get(url + "/api/persons")
    (ok if st == 401 else fail)(f"Signed-out request for people answered {st}",
                                "" if st == 401 else "People's data must need an account. Don't launch until this is 401.")
    st, _, _ = _get(url + "/uploads/1/anything.png")
    (ok if st in (401, 404) else fail)(f"Signed-out request for an uploaded file answered {st}",
                                       "" if st in (401, 404) else "Uploaded files must need an account.")
    st, _, body = _get(url + "/api/status")
    try:
        ai = json.loads(body).get("ai", {})
        if ai.get("providers"):
            ok(f"Live site's AI: {', '.join(ai['providers'])} (mode {ai.get('mode')})")
        else:
            warn("Live site has no AI provider on", "Set GROQ_API_KEY on the server.")
    except (ValueError, AttributeError):
        warn("Couldn't read /api/status", "")


def main(argv: list[str]) -> int:
    check_settings()
    check_data()
    check_backups()
    check_build()
    check_repo()
    if "--url" in argv:
        check_live(argv[argv.index("--url") + 1])
    for level, what, advice in results:
        mark = {"OK": "  ok  ", "WARN": " WARN ", "FAIL": " FAIL "}[level]
        print(f"[{mark}] {what}")
        if advice and level != "OK":
            print(f"{' ' * 9}-> {advice}")
    fails = sum(1 for r in results if r[0] == "FAIL")
    warns = sum(1 for r in results if r[0] == "WARN")
    print(f"\n{len(results) - fails - warns} ok, {warns} to look at, {fails} to fix before launch.")
    if "--url" not in argv:
        print("Add --url https://your.address to also check the live site from outside.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
