"""The admin page: its separate sign-in, the usage counts, and client feedback.

Run with:  pytest backend/tests -q   (from the project root)
"""
import importlib
import os
import re
import tempfile

import pytest

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari/604.1"


def fresh(monkeypatch, admins="owner@imy.test", smtp=True):
    os.environ["IMY_DATA_DIR"] = tempfile.mkdtemp()
    from backend import auth, db as db_mod, feedback, insights, mailer, main
    importlib.reload(db_mod)
    importlib.reload(auth)
    importlib.reload(insights)
    importlib.reload(feedback)
    importlib.reload(main)
    main.init_db()
    monkeypatch.setenv("IMY_RATE_LIMIT", "0")
    monkeypatch.setenv("IMY_PUBLIC_URL", "https://imy.example.com")
    monkeypatch.setenv("IMY_ADMIN_EMAILS", admins)
    monkeypatch.delenv("IMY_ORIGINS", raising=False)
    if smtp:
        monkeypatch.setenv("IMY_SMTP_HOST", "smtp.example.com")
    else:
        monkeypatch.delenv("IMY_SMTP_HOST", raising=False)
    outbox = []
    monkeypatch.setattr(mailer, "send_later",
                        lambda to, subject, text, html=None, print_if_off=False: outbox.append(
                            {"to": to, "subject": subject, "text": text, "html": html}))
    return auth, insights, main, outbox


def token_from(mail) -> str:
    return re.search(r"#login=([A-Za-z0-9_-]+)", mail["text"]).group(1)


def client(auth, email="client@example.com"):
    user = auth.create_user(email, "testing12345", True, terms=True)
    auth.CURRENT.set(user)
    return user


# ---------------------------------------------------------------- signing in as admin
def test_only_admin_emails_get_a_link_and_the_answer_is_the_same(monkeypatch):
    _, insights, main, outbox = fresh(monkeypatch)
    yes = main.admin_link(main.AdminLinkIn(email=" Owner@IMY.test "))
    no = main.admin_link(main.AdminLinkIn(email="someone@example.com"))
    assert yes == no
    assert len(outbox) == 1 and outbox[0]["to"] == "owner@imy.test"
    assert "https://imy.example.com/admin#login=" in outbox[0]["text"]
    assert token_from(outbox[0]) in outbox[0]["html"]


def test_the_link_works_once_and_makes_an_admin_session(monkeypatch):
    _, insights, main, outbox = fresh(monkeypatch)
    main.admin_link(main.AdminLinkIn(email="owner@imy.test"))
    token = token_from(outbox[0])
    resp = main.admin_login(main.AdminLoginIn(token=token))
    cookie = resp.headers["set-cookie"]
    assert "imy_admin=" in cookie and "HttpOnly" in cookie and "Path=/api/admin" in cookie
    assert "samesite=strict" in cookie.lower()
    session = re.search(r"imy_admin=([^;]+)", cookie).group(1)
    assert insights.load_admin(session) == {"email": "owner@imy.test"}
    with pytest.raises(main.HTTPException):
        main.admin_login(main.AdminLoginIn(token=token))          # second use
    with pytest.raises(main.HTTPException):
        main.admin_login(main.AdminLoginIn(token="made-up"))


def test_removing_someone_from_the_admin_list_locks_them_out_at_once(monkeypatch):
    _, insights, main, outbox = fresh(monkeypatch)
    main.admin_link(main.AdminLinkIn(email="owner@imy.test"))
    email, session = insights.use_admin_link(token_from(outbox[0]))
    assert insights.load_admin(session)
    monkeypatch.setenv("IMY_ADMIN_EMAILS", "someone-else@imy.test")
    assert insights.load_admin(session) is None


def test_contact_email_is_the_admin_when_no_list_is_set(monkeypatch):
    _, insights, main, outbox = fresh(monkeypatch, admins="")
    monkeypatch.setenv("IMY_CONTACT_EMAIL", "pdyn@example.com")
    assert insights.admin_emails() == ["pdyn@example.com"]


def test_a_client_account_is_not_an_admin(monkeypatch):
    auth, insights, main, outbox = fresh(monkeypatch)
    client(auth, "owner@imy.test")               # same email as the admin, signed in as a client
    assert insights.ADMIN.get() is None           # the client cookie never opens the admin page
    assert insights.load_admin("") is None


def test_admin_links_are_limited_per_hour(monkeypatch):
    _, insights, main, outbox = fresh(monkeypatch)
    for _ in range(8):
        main.admin_link(main.AdminLinkIn(email="owner@imy.test"))
    assert len(outbox) == 5


def test_without_email_on_a_live_site_the_link_is_refused(monkeypatch):
    _, insights, main, outbox = fresh(monkeypatch, smtp=False)
    with pytest.raises(main.HTTPException) as got:
        main.admin_link(main.AdminLinkIn(email="owner@imy.test"))
    assert got.value.status_code == 503 and not outbox


# ---------------------------------------------------------------- counting people
def test_visitors_are_counted_once_a_day_without_bots_or_do_not_track(monkeypatch):
    _, insights, main, _ = fresh(monkeypatch)
    for _ in range(3):
        assert insights.count_visit("landing", "1.2.3.4", UA, {})
    insights.count_visit("landing", "5.6.7.8", UA, {})
    assert not insights.count_visit("landing", "9.9.9.9", "Googlebot/2.1", {})
    assert not insights.count_visit("landing", "9.9.9.8", UA, {"dnt": "1"})
    assert not insights.count_visit("landing", "9.9.9.7", UA, {"sec-gpc": "1"})
    assert not insights.count_visit("somewhere", "1.2.3.4", UA, {})
    s = insights.stats()
    assert s["visitors_today"] == 2 and s["views_today"] == 4


def test_visit_codes_and_the_daily_secret_are_forgotten_the_next_day(monkeypatch):
    _, insights, main, _ = fresh(monkeypatch)
    from backend.db import db
    insights.count_visit("landing", "1.2.3.4", UA, {})
    with db() as conn:
        conn.execute("UPDATE visit_seen SET day = '2000-01-01'")
        conn.execute("UPDATE visit_salt SET day = '2000-01-01'")
        conn.execute("UPDATE visit_days SET day = '2000-01-01'")
    insights.sweep()
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM visit_seen").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM visit_salt").fetchone()[0] == 0
        assert conn.execute("SELECT visitors FROM visit_days").fetchone()[0] == 1     # the count stays


def test_active_accounts_and_sign_ups(monkeypatch):
    auth, insights, main, _ = fresh(monkeypatch)
    a = client(auth, "a@example.com")
    b = client(auth, "b@example.com")
    client(auth, "c@example.com")
    insights.saw(a["id"])
    insights.saw(a["id"])                         # twice in a minute: one write
    insights.saw(b["id"])
    s = insights.stats()
    assert s["accounts"] == 3 and s["signups_today"] == 3
    assert s["active_today"] == 2 and s["online"] == 2 and s["active_week"] == 2
    assert len(s["series"]) == 30 and s["series"][-1]["active"] == 2


def test_the_accounts_list_has_counts_but_no_chats(monkeypatch):
    auth, insights, main, _ = fresh(monkeypatch)
    me = client(auth, "a@example.com")
    main.create_person(main.NewPersonIn(name="Secret Name", consent=True))
    insights.saw(me["id"])
    got = insights.accounts()
    assert got["total"] == 1
    row = got["accounts"][0]
    assert row["email"] == "a@example.com" and row["people"] == 1 and row["days_active"] == 1 and row["online"]
    assert "Secret Name" not in str(got)
    assert "password_hash" not in row


# ---------------------------------------------------------------- feedback
def test_feedback_reaches_the_inbox_and_the_admins(monkeypatch):
    auth, insights, main, outbox = fresh(monkeypatch)
    client(auth, "a@example.com")
    main.send_feedback(main.FeedbackIn(kind="idea", message="Add WhatsApp voice notes please", rating=5, can_reply=True))
    main.send_feedback(main.FeedbackIn(kind="problem", message="Upload was slow", rating=2, can_reply=False))
    got = main.admin_feedback()
    assert got["counts"] == {"new": 2, "read": 0, "done": 0}
    slow, idea = got["items"]
    assert idea["email"] == "a@example.com" and idea["kind_label"] == "Idea"
    assert slow["email"] is None                                  # didn't ask for a reply: no email shown
    assert got["rating"] == {"average": 3.5, "count": 2}
    assert [m["to"] for m in outbox] == ["owner@imy.test", "owner@imy.test"]
    assert "Add WhatsApp voice notes please" in outbox[0]["text"] and "a@example.com" in outbox[0]["text"]
    assert "a@example.com" not in outbox[1]["text"]


def test_feedback_status_and_delete(monkeypatch):
    auth, insights, main, _ = fresh(monkeypatch)
    client(auth)
    fid = main.send_feedback(main.FeedbackIn(message="hi"))["id"]
    main.admin_feedback_status(fid, main.FeedbackStatusIn(status="done"))
    assert main.admin_feedback(status="done")["items"][0]["id"] == fid
    assert main.admin_feedback(status="new")["items"] == []
    with pytest.raises(main.HTTPException):
        main.admin_feedback_status(fid, main.FeedbackStatusIn(status="weird"))
    main.admin_feedback_delete(fid)
    with pytest.raises(main.HTTPException):
        main.admin_feedback_delete(fid)


def test_empty_or_huge_feedback_is_refused(monkeypatch):
    auth, insights, main, _ = fresh(monkeypatch)
    client(auth)
    for text in ("   ", "x" * 2001):
        with pytest.raises(main.HTTPException) as got:
            main.send_feedback(main.FeedbackIn(message=text))
        assert got.value.status_code == 400


def test_deleting_an_account_deletes_its_feedback_and_activity(monkeypatch):
    auth, insights, main, _ = fresh(monkeypatch)
    me = client(auth)
    insights.saw(me["id"])
    main.send_feedback(main.FeedbackIn(message="bye"))
    main.wipe_account(me["id"])
    from backend.db import db
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM active_days").fetchone()[0] == 0


# ---------------------------------------------------------------- your own computer
def test_on_your_own_computer_the_page_says_why_no_link_came(monkeypatch):
    _, insights, main, outbox = fresh(monkeypatch)
    monkeypatch.setenv("IMY_PUBLIC_URL", "http://localhost:8000")
    got = main.admin_link(main.AdminLinkIn(email="me@example.com"))
    assert got["ok"] is False and "isn't an admin" in got["hint"] and "owner@imy.test" in got["hint"]
    assert not outbox
    monkeypatch.setenv("IMY_ADMIN_EMAILS", "")
    monkeypatch.delenv("IMY_CONTACT_EMAIL", raising=False)
    assert "No admin address is set" in main.admin_link(main.AdminLinkIn(email="me@example.com"))["hint"]


def test_the_live_site_never_says_who_the_admins_are(monkeypatch):
    _, insights, main, outbox = fresh(monkeypatch)
    got = main.admin_link(main.AdminLinkIn(email="me@example.com"))
    assert "hint" not in got and got["ok"] is True


def test_the_command_line_prints_a_working_link(monkeypatch, capsys=None):
    _, insights, main, _ = fresh(monkeypatch)
    from backend import admin
    import io, contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert admin.admin_link(None, "http://localhost:8000") == 0
    token = re.search(r"/admin#login=([A-Za-z0-9_-]+)", out.getvalue()).group(1)
    assert insights.use_admin_link(token)[0] == "owner@imy.test"
    with contextlib.redirect_stdout(io.StringIO()):
        assert admin.admin_link("someone@example.com", None) == 1
