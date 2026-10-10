"""Forgot password: the emailed link, what it can and can't do, and the email itself.

Run with:  pytest backend/tests -q   (from the project root)
"""
import importlib
import os
import re
import tempfile

import pytest


def fresh(monkeypatch, site="https://imy.example.com", smtp=True):
    os.environ["IMY_DATA_DIR"] = tempfile.mkdtemp()
    from backend import auth, db as db_mod, mailer, main
    importlib.reload(db_mod)
    importlib.reload(auth)
    importlib.reload(main)
    main.init_db()
    monkeypatch.setenv("IMY_RATE_LIMIT", "0")
    monkeypatch.delenv("IMY_ORIGINS", raising=False)
    if site:
        monkeypatch.setenv("IMY_PUBLIC_URL", site)
    else:
        monkeypatch.delenv("IMY_PUBLIC_URL", raising=False)
    if smtp:
        monkeypatch.setenv("IMY_SMTP_HOST", "smtp.example.com")
    else:
        monkeypatch.delenv("IMY_SMTP_HOST", raising=False)
    outbox = []
    # Catch emails instead of sending them (and right away, not in a background thread).
    monkeypatch.setattr(mailer, "send_later",
                        lambda to, subject, text, html=None, print_if_off=False: outbox.append(
                            {"to": to, "subject": subject, "text": text, "html": html, "printed": print_if_off}))
    return auth, main, outbox


def link_token(mail) -> str:
    return re.search(r"#reset=([A-Za-z0-9_-]+)", mail["text"]).group(1)


def account(auth, email="diane@example.com", password="oldpassword1"):
    return auth.create_user(email, password, True, terms=True)


# ---------------------------------------------------------------- asking for a link
def test_a_link_is_emailed_and_the_answer_never_says_who_has_an_account(monkeypatch):
    auth, main, outbox = fresh(monkeypatch)
    account(auth)
    yes = main.forgot_password(main.ForgotIn(email="  Diane@Example.com "))
    no = main.forgot_password(main.ForgotIn(email="nobody@example.com"))
    assert yes == no                                        # same answer either way
    assert len(outbox) == 1 and outbox[0]["to"] == "diane@example.com"
    mail = outbox[0]
    assert mail["subject"] == "Reset your imy password"
    assert "https://imy.example.com/app#reset=" in mail["text"]
    assert "30 minutes" in mail["text"] and "ignore this email" in mail["text"]
    assert link_token(mail) in mail["html"]


def test_bad_email_is_refused(monkeypatch):
    _, main, outbox = fresh(monkeypatch)
    with pytest.raises(main.HTTPException) as got:
        main.forgot_password(main.ForgotIn(email="not an email"))
    assert got.value.status_code == 400 and not outbox


def test_only_three_emails_an_hour_per_account(monkeypatch):
    auth, main, outbox = fresh(monkeypatch)
    account(auth)
    for _ in range(5):
        main.forgot_password(main.ForgotIn(email="diane@example.com"))     # still answers "ok" each time
    assert len(outbox) == 3


def test_only_a_hash_of_the_link_is_stored(monkeypatch):
    auth, main, outbox = fresh(monkeypatch)
    account(auth)
    main.forgot_password(main.ForgotIn(email="diane@example.com"))
    token = link_token(outbox[0])
    from backend.db import db
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM password_resets")]
    assert len(rows) == 1 and token not in str(rows)


# ---------------------------------------------------------------- using the link
def test_the_link_sets_a_new_password_once_and_signs_out_everywhere(monkeypatch):
    auth, main, outbox = fresh(monkeypatch)
    user = account(auth)
    phone = auth.start_session(user["id"])
    main.forgot_password(main.ForgotIn(email="diane@example.com"))
    main.forgot_password(main.ForgotIn(email="diane@example.com"))           # asked twice
    first, second = link_token(outbox[0]), link_token(outbox[1])

    assert main.check_reset_link(main.ResetCheckIn(token=first))["email"] == "diane@example.com"
    resp = main.reset_password(main.ResetIn(token=first, password="brandnew123"))
    assert resp.status_code == 200 and "imy_session=" in resp.headers.get("set-cookie", "")   # signed in here

    assert auth.verify_user("diane@example.com", "brandnew123")
    with pytest.raises(auth.AuthError):
        auth.verify_user("diane@example.com", "oldpassword1")
    assert auth.load_session(phone) is None                                  # every other device signed out
    for used in (first, second):                                             # the link, and the other one too
        with pytest.raises(main.HTTPException) as got:
            main.reset_password(main.ResetIn(token=used, password="another1234"))
        assert got.value.status_code == 400 and "expired" in got.value.detail
    notice = outbox[-1]
    assert notice["subject"] == "Your imy password was changed" and notice["to"] == "diane@example.com"


def test_an_expired_link_does_nothing(monkeypatch):
    auth, main, outbox = fresh(monkeypatch)
    account(auth)
    main.forgot_password(main.ForgotIn(email="diane@example.com"))
    token = link_token(outbox[0])
    from backend.db import db
    with db() as conn:
        conn.execute("UPDATE password_resets SET expires_at = '2000-01-01T00:00:00+00:00'")
    for call in (lambda: main.check_reset_link(main.ResetCheckIn(token=token)),
                 lambda: main.reset_password(main.ResetIn(token=token, password="brandnew123"))):
        with pytest.raises(main.HTTPException):
            call()
    assert auth.verify_user("diane@example.com", "oldpassword1")


def test_made_up_or_empty_links_do_nothing(monkeypatch):
    auth, main, _ = fresh(monkeypatch)
    account(auth)
    for token in ("", "x" * 43, "not-a-real-token-at-all-0000000000000000"):
        with pytest.raises(main.HTTPException) as got:
            main.reset_password(main.ResetIn(token=token, password="brandnew123"))
        assert got.value.status_code == 400
    assert auth.verify_user("diane@example.com", "oldpassword1")


def test_a_too_short_password_keeps_the_link_working(monkeypatch):
    auth, main, outbox = fresh(monkeypatch)
    account(auth)
    main.forgot_password(main.ForgotIn(email="diane@example.com"))
    token = link_token(outbox[0])
    with pytest.raises(main.HTTPException) as got:
        main.reset_password(main.ResetIn(token=token, password="short"))
    assert "8 characters" in got.value.detail
    main.reset_password(main.ResetIn(token=token, password="longenough1"))   # fixing it still works
    assert auth.verify_user("diane@example.com", "longenough1")


def test_deleting_the_account_removes_its_links(monkeypatch):
    auth, main, outbox = fresh(monkeypatch)
    user = account(auth)
    main.forgot_password(main.ForgotIn(email="diane@example.com"))
    main.wipe_account(user["id"])
    from backend.db import db
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM password_resets").fetchone()[0] == 0


def test_changing_the_password_in_the_app_also_sends_a_notice(monkeypatch):
    auth, main, outbox = fresh(monkeypatch)
    user = account(auth)
    auth.CURRENT.set(user)
    main.set_password(main.PasswordIn(old_password="oldpassword1", new_password="brandnew123"))
    assert [m["subject"] for m in outbox] == ["Your imy password was changed"]


# ---------------------------------------------------------------- where the link points
class Req:
    def __init__(self, host, scheme="http", port=8000):
        self.url = type("U", (), {"hostname": host, "scheme": scheme})()
        self.base_url = f"{scheme}://{host}:{port}/"
        self.headers = {}


def test_the_link_never_comes_from_the_visitors_host_header(monkeypatch):
    from backend import mailer
    monkeypatch.delenv("IMY_PUBLIC_URL", raising=False)
    monkeypatch.delenv("IMY_ORIGINS", raising=False)
    assert mailer.site_url(Req("evil.example")) == ""                       # a faked Host is ignored
    assert mailer.site_url(Req("localhost")) == "http://localhost:8000"     # your own computer is fine
    monkeypatch.setenv("IMY_ORIGINS", "http://localhost:5173,https://imy.example.com")
    assert mailer.site_url(Req("evil.example")) == "https://imy.example.com"
    monkeypatch.setenv("IMY_PUBLIC_URL", "https://imy.ph/")
    assert mailer.site_url(Req("evil.example")) == "https://imy.ph"


def test_a_live_site_without_email_says_so_to_everyone_alike(monkeypatch):
    auth, main, outbox = fresh(monkeypatch, smtp=False)
    account(auth)
    monkeypatch.setenv("IMY_CONTACT_EMAIL", "help@imy.example.com")
    details = set()
    for email in ("diane@example.com", "nobody@example.com"):
        with pytest.raises(main.HTTPException) as got:
            main.forgot_password(main.ForgotIn(email=email))
        assert got.value.status_code == 503
        details.add(got.value.detail)
    assert len(details) == 1 and "help@imy.example.com" in details.pop()
    assert not outbox


def test_on_your_own_computer_the_link_is_printed_instead(monkeypatch):
    auth, main, outbox = fresh(monkeypatch, site="http://localhost:8000", smtp=False)
    account(auth)
    out = main.forgot_password(main.ForgotIn(email="diane@example.com"))
    assert out["printed"] is True and outbox[0]["printed"] is True


# ---------------------------------------------------------------- the email itself
def test_the_email_goes_out_encrypted_with_both_versions(monkeypatch):
    from backend import mailer
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent.update(host=host, port=port)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, context=None): sent["tls"] = True
        def login(self, user, password): sent["login"] = (user, password)
        def send_message(self, msg): sent["msg"] = msg

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("IMY_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("IMY_SMTP_PORT", "587")
    monkeypatch.setenv("IMY_SMTP_USER", "imy.app@gmail.com")
    monkeypatch.setenv("IMY_SMTP_PASSWORD", "app-password")
    monkeypatch.delenv("IMY_MAIL_FROM", raising=False)
    monkeypatch.setenv("IMY_CONTACT_EMAIL", "help@imy.example.com")
    subject, text, html = mailer.reset_email('https://imy.ph/#reset=abc"<x>', 30)
    assert mailer.send("diane@example.com", subject, text, html) is True
    msg = sent["msg"]
    assert sent["tls"] and sent["login"] == ("imy.app@gmail.com", "app-password") and sent["port"] == 587
    assert msg["From"] == "imy.app@gmail.com" and msg["To"] == "diane@example.com"
    assert msg["Reply-To"] == "help@imy.example.com" and msg["Message-ID"]
    kinds = [p.get_content_type() for p in msg.walk()]
    assert kinds == ["multipart/alternative", "text/plain", "multipart/related", "text/html", "image/png", "image/png"]   # imy logo + PDYN mark
    logo = [p for p in msg.walk() if p.get_content_type() == "image/png"][0]
    assert logo["Content-ID"] == "<imy-logo>" and 'src="cid:imy-logo"' in html     # the logo travels inside
    assert logo.get_content() == mailer.LOGO.read_bytes()
    assert '"<x>' not in html and "&quot;&lt;x&gt;" in html                  # the link can't break the HTML
    assert "diane@example.com" not in text                                   # no address given, none shown


def test_a_wrong_email_password_gives_a_clear_message(monkeypatch):
    from backend import mailer

    class Refuses:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, context=None): pass
        def login(self, user, password): raise mailer.smtplib.SMTPAuthenticationError(535, b"bad")
        def send_message(self, msg): raise AssertionError("shouldn't get here")

    monkeypatch.setattr(mailer.smtplib, "SMTP", Refuses)
    monkeypatch.setenv("IMY_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("IMY_SMTP_USER", "imy.app@gmail.com")
    with pytest.raises(mailer.MailError) as got:
        mailer.send("diane@example.com", "s", "t")
    assert "App Password" in str(got.value)


# ---------------------------------------------------------------- the launch check
def test_preflight_fails_a_live_site_without_email(monkeypatch):
    from backend import preflight
    for name in ("IMY_SMTP_HOST", "IMY_PUBLIC_URL", "IMY_ORIGINS", "IMY_MAIL_FROM", "IMY_SMTP_USER"):
        monkeypatch.delenv(name, raising=False)

    def run():
        preflight.results.clear()
        preflight.check_email()
        return {(lvl, what.split(" (")[0]) for lvl, what, _ in preflight.results}
    got = run()
    assert ("FAIL", "No IMY_PUBLIC_URL") in got and ("FAIL", "Email isn't set up") in got
    monkeypatch.setenv("IMY_PUBLIC_URL", "http://imy.example.com")
    assert ("FAIL", "IMY_PUBLIC_URL is plain http") in run()
    monkeypatch.setenv("IMY_PUBLIC_URL", "https://imy.example.com")
    monkeypatch.setenv("IMY_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("IMY_SMTP_USER", "imy.app@gmail.com")
    monkeypatch.setenv("IMY_SMTP_PASSWORD", "x")
    assert all(lvl == "OK" for lvl, _ in run())
