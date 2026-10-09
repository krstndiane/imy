"""admin.py - looking after accounts from the server's command line.

    python -m backend.admin accounts             every account: email, when made, people, last seen
    python -m backend.admin usage                today's AI use: the site total and the busiest accounts
    python -m backend.admin remove EMAIL         show what removing this account would delete
    python -m backend.admin remove EMAIL --yes   ...and actually delete it (no undo)

`remove` does exactly what "Delete everything" does in the app: the account, every person under
it, their messages, chats, photos and screenshots, and every uploaded file. Use it for test
accounts before launch, and for deletion requests that come in by email.

Make a backup first (python -m backend.backup) - there's no undo.
"""
import sys

from . import usage
from .db import db, init_db


def accounts() -> None:
    with db() as conn:
        rows = conn.execute(
            "SELECT u.id, u.email, u.created_at, u.last_seen, u.terms_version, "
            "(SELECT COUNT(*) FROM persons p WHERE p.user_id = u.id) AS people "
            "FROM users u ORDER BY u.id").fetchall()
    print(f"{len(rows)} account(s)")
    for r in rows:
        terms = r["terms_version"] or "before Terms"
        print(f"  #{r['id']:<5} {r['email']:<36} made {r['created_at'][:10]}  last seen {r['last_seen'][:10]}  "
              f"{r['people']} people  terms: {terms}")


def show_usage() -> None:
    day = usage.today()
    with db() as conn:
        rows = conn.execute(
            "SELECT a.user_id, COALESCE(u.email, '(not signed in)') AS email, a.seen, a.other FROM ai_usage a "
            "LEFT JOIN users u ON u.id = a.user_id WHERE a.day = ? ORDER BY a.seen + a.other DESC LIMIT 15",
            (day,)).fetchall()
        total = conn.execute("SELECT COALESCE(SUM(seen + other), 0) FROM ai_usage WHERE day = ?", (day,)).fetchone()[0]
    limit = usage.per_account()
    site = usage.per_site()
    print(f"AI calls today ({day}): {total}" + (f" of {site} for the site" if site else ""))
    print(f"Per-account limit: {limit or 'none'}")
    for r in rows:
        print(f"  {r['email']:<36} {r['seen']:>4} replies/photos   {r['other']:>4} background")


def remove(email: str, really: bool) -> int:
    with db() as conn:
        user = conn.execute("SELECT id, email FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        if not user:
            print(f"No account with the email {email}.")
            return 1
        people = conn.execute("SELECT id, name FROM persons WHERE user_id = ?", (user["id"],)).fetchall()
        counts = {}
        for table in ("messages", "chat_messages", "media", "screenshots"):
            counts[table] = sum(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE person_id = ?", (p["id"],)).fetchone()[0]
                                for p in people)
    print(f"Account #{user['id']} {user['email']}")
    print(f"  {len(people)} people: {', '.join(p['name'] for p in people) or '-'}")
    print(f"  {counts['messages']} training messages, {counts['chat_messages']} chat messages, "
          f"{counts['screenshots']} screenshots, {counts['media']} shared files")
    if not really:
        print("Nothing deleted. Add --yes to delete all of this (there's no undo; make a backup first).")
        return 0
    from .main import wipe_account          # imported here: the web app is only needed to delete
    wipe_account(user["id"])
    print("Deleted.")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    init_db()                                # adds any new columns, so this works on an older database
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        sys.exit(0)
    if args[0] == "accounts":
        accounts()
    elif args[0] == "usage":
        show_usage()
    elif args[0] == "remove" and len(args) >= 2:
        sys.exit(remove(args[1], "--yes" in args))
    else:
        print(__doc__)
        sys.exit(2)
