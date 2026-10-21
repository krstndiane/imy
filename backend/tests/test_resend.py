"""Email through Resend (mailer.py), against a stand-in Resend that speaks the same HTTP.

Run with:  pytest backend/tests -q   (from the project root)
"""
import base64
import importlib

import pytest

from backend.tests.fakes import FakeResend


def mailer_with(monkeypatch, fake, key=None, frm="imy <no-reply@pdyntech.com>"):
    from backend import mailer
    monkeypatch.setenv("RESEND_API_KEY", key or fake.key)
    monkeypatch.setenv("IMY_MAIL_FROM", frm)
    monkeypatch.setenv("IMY_CONTACT_EMAIL", "pdyntechnology@gmail.com")
    monkeypatch.setattr(mailer, "RESEND_URL", fake.url)
    return mailer


def test_a_reset_email_goes_through_resend_with_the_logos_inside(monkeypatch):
    with FakeResend() as resend:
        mailer = mailer_with(monkeypatch, resend)
        assert mailer.configured() and mailer.service() == "resend"
        subject, text, html_body = mailer.reset_email("https://imy.pdyntech.com/reset#t=abc", 30, "diane@example.com")
        assert mailer.send("diane@example.com", subject, text, html_body) is True
        [sent] = resend.sent
        assert sent["from"] == "imy <no-reply@pdyntech.com>" and sent["to"] == ["diane@example.com"]
        assert sent["reply_to"] == "pdyntechnology@gmail.com" and "imy.pdyntech.com/reset" in sent["text"]
        cids = {a["content_id"] for a in sent["attachments"]}
        assert cids == {"imy-logo", "pdyn-logo"} and all(f"cid:{c}" in sent["html"] for c in cids)
        assert all(base64.b64decode(a["content"])[:4] == b"\x89PNG" for a in sent["attachments"])


def test_resend_problems_are_explained(monkeypatch):
    with FakeResend() as resend:
        mailer = mailer_with(monkeypatch, resend, key="re_wrong")
        with pytest.raises(mailer.MailError, match="RESEND_API_KEY"):
            mailer.send("a@example.com", "hi", "hi")
        mailer = mailer_with(monkeypatch, resend, frm="imy <no-reply@not-verified.com>")
        with pytest.raises(mailer.MailError, match="Route 53"):
            mailer.send("a@example.com", "hi", "hi")
        monkeypatch.setenv("IMY_MAIL_FROM", "")
        with pytest.raises(mailer.MailError, match="IMY_MAIL_FROM"):
            mailer.send("a@example.com", "hi", "hi")
    assert resend.sent == []


def test_without_resend_the_smtp_settings_still_work(monkeypatch):
    from backend import mailer
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("IMY_SMTP_HOST", "smtp.gmail.com")
    assert mailer.service() == "smtp"
    monkeypatch.setenv("IMY_SMTP_HOST", "")
    assert mailer.service() == "" and not mailer.configured()
