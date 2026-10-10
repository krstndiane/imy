"""mailer.py - sending email. imy only sends two kinds: a "forgot password" link, and a short
notice when a password changes.

Any email service that offers SMTP works. Set these on the server (or in .env):

    IMY_SMTP_HOST       smtp.gmail.com, smtp-relay.brevo.com, smtp.resend.com, ...
    IMY_SMTP_PORT       587 (the usual; STARTTLS) or 465 (SSL from the start)
    IMY_SMTP_USER       the login the service gave you
    IMY_SMTP_PASSWORD   its password. For Gmail this is an App Password, never your real password.
    IMY_MAIL_FROM       who the email says it's from, e.g.  imy <support@yourdomain.com>
                        (defaults to IMY_SMTP_USER)
    IMY_PUBLIC_URL      the site's address, e.g. https://imy.example.com. The link in the email
                        points here. It is never taken from the visitor's request, because a faked
                        Host header would otherwise make imy email a real person a link to someone
                        else's website ("password reset poisoning").

Without IMY_SMTP_HOST nothing is sent. On your own computer (the site address is localhost) the
email is printed in the Terminal instead, so you can still click the link while testing. On a live
site it is NOT printed, because a reset link is as good as the password and host logs are read by
more people than the person it's for. `python -m backend.preflight` flags a live site without email.

Check your settings by sending yourself a test:   python -m backend.mailer you@example.com
"""
import html
import os
import smtplib
import ssl
import sys
import threading
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path

LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
# The imy logo, carried inside each email (not loaded from the website), so it shows even when the
# site is only on your laptop, and mail apps that block outside images still show it.
LOGO = Path(__file__).resolve().parent / "assets" / "email-logo.png"
LOGO_CID = "imy-logo"
PDYN = Path(__file__).resolve().parent / "assets" / "pdyn-logo.png"   # the maker's mark, at the foot of every email
PDYN_CID = "pdyn-logo"


class MailError(Exception):
    """The email service refused or couldn't be reached. The message says which, in plain words."""


def configured() -> bool:
    return bool(os.environ.get("IMY_SMTP_HOST", "").strip())


def sender() -> str:
    return (os.environ.get("IMY_MAIL_FROM", "").strip() or os.environ.get("IMY_SMTP_USER", "").strip())


# ---------------------------------------------------------------- where links point
def site_url(request=None) -> str:
    """The address links in emails point to, without a trailing slash, or "" if imy can't tell
    safely. IMY_PUBLIC_URL first; then an https address from IMY_ORIGINS; and only for a visitor on
    this same computer (localhost), the address they used - that's what makes testing on a laptop
    work without setting anything."""
    url = os.environ.get("IMY_PUBLIC_URL", "").strip().rstrip("/")
    if url:
        return url
    for o in os.environ.get("IMY_ORIGINS", "").split(","):
        if o.strip().startswith("https://"):
            return o.strip().rstrip("/")
    if request is not None:
        host = (request.url.hostname or "").lower()
        if host in LOCAL_HOSTS:
            return str(request.base_url).rstrip("/")
    return ""


def is_local(url: str) -> bool:
    from urllib.parse import urlsplit
    try:
        return (urlsplit(url).hostname or "").lower() in LOCAL_HOSTS
    except ValueError:
        return False


def mask(email: str) -> str:
    """d•••@gmail.com - enough to recognise in a log, not enough to collect addresses from it."""
    name, _, domain = (email or "").partition("@")
    return f"{name[:1]}•••@{domain}" if domain else "•••"


# ---------------------------------------------------------------- sending
def send(to: str, subject: str, text: str, html_body: str | None = None, print_if_off: bool = False) -> bool:
    """Send one email. True if it went to the email service; False if email isn't set up (and it
    was printed instead, when print_if_off). Raises MailError if the service said no."""
    if not configured():
        if print_if_off:
            line = "-" * 68
            print(f"\n{line}\n[imy] Email isn't set up (IMY_SMTP_HOST), so here is what would have been sent:\n"
                  f"To: {to}\nSubject: {subject}\n\n{text}\n{line}\n", flush=True)
        else:
            print(f"[imy] couldn't email {mask(to)}: email isn't set up (IMY_SMTP_HOST)", flush=True)
        return False

    host = os.environ["IMY_SMTP_HOST"].strip()
    try:
        port = int(os.environ.get("IMY_SMTP_PORT", "587") or 587)
    except ValueError:
        port = 587
    user = os.environ.get("IMY_SMTP_USER", "").strip()
    password = os.environ.get("IMY_SMTP_PASSWORD", "")
    frm = sender()
    if not frm:
        raise MailError("Set IMY_MAIL_FROM (or IMY_SMTP_USER) to the address emails come from.")
    msg = build(frm, to, subject, text, html_body)

    context = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as s:
                if user:
                    s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls(context=context)        # never send a password-reset link unencrypted
                if user:
                    s.login(user, password)
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(f"The email service didn't accept IMY_SMTP_USER / IMY_SMTP_PASSWORD ({exc.smtp_code}). "
                        "For Gmail, use an App Password, not your normal password.") from exc
    except smtplib.SMTPException as exc:
        raise MailError(f"The email service refused the message: {exc}") from exc
    except ssl.SSLCertVerificationError as exc:
        raise MailError(f"{host} answered, but its security certificate didn't check out ({exc.verify_message}), "
                        "so nothing was sent. Check IMY_SMTP_HOST is the exact name your email service gave you.") from exc
    except (OSError, ssl.SSLError) as exc:
        raise MailError(f"Couldn't reach {host}:{port} ({exc}). Check IMY_SMTP_HOST and IMY_SMTP_PORT.") from exc
    return True


def build(frm: str, to: str, subject: str, text: str, html_body: str | None = None) -> EmailMessage:
    """The whole email: a plain-text version (for mail apps that don't show HTML, and for spam
    filters, which trust emails that have one), the designed version, and the logo inside it."""
    msg = EmailMessage()
    msg["From"] = frm
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    domain = parseaddr(frm)[1].partition("@")[2] or None
    msg["Message-ID"] = make_msgid(domain=domain)
    contact = os.environ.get("IMY_CONTACT_EMAIL", "").strip()
    if contact:
        msg["Reply-To"] = contact          # so "reply to this email" reaches a person
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
        if f"cid:{LOGO_CID}" in html_body and LOGO.is_file():
            msg.get_payload()[1].add_related(LOGO.read_bytes(), "image", "png", cid=f"<{LOGO_CID}>",
                                             disposition="inline", filename="imy-logo.png")
        if f"cid:{PDYN_CID}" in html_body and PDYN.is_file():
            msg.get_payload()[1].add_related(PDYN.read_bytes(), "image", "png", cid=f"<{PDYN_CID}>",
                                             disposition="inline", filename="pdyn-technologies.png")
    return msg


def send_later(to: str, subject: str, text: str, html_body: str | None = None, print_if_off: bool = False) -> None:
    """Send in the background. The page answers straight away - and it answers in the same time
    whether or not an account exists, which a slow email server would otherwise give away."""
    def run():
        try:
            if send(to, subject, text, html_body, print_if_off):
                print(f"[imy] emailed {mask(to)}: {subject}", flush=True)
        except MailError as exc:
            print(f"[imy] couldn't email {mask(to)}: {exc}", flush=True)
        except Exception as exc:          # a background thread must never take anything else down
            print(f"[imy] couldn't email {mask(to)}: {exc!r}", flush=True)
    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------- what the emails say
# The look follows the site: imy's dark background, the logo, white headings, the blue pill button.
# Mail apps are far pickier than browsers, so it's built the old-fashioned way: tables for layout,
# every style written on the element itself, no outside fonts or files, nothing that needs JavaScript.
FONT = "Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
INK, MUTED, FAINT, BLUE, LINK = "#ffffff", "#a1aab8", "#6b7484", "#2563eb", "#7aa7ff"


def _p(content: str, size: int = 15, color: str = MUTED, margin: str = "0 0 16px") -> str:
    return f'<p style="margin:{margin};font-family:{FONT};font-size:{size}px;line-height:1.6;color:{color}">{content}</p>'


def _button(href: str, label: str, solid: bool = True, margin: str = "6px 0 22px") -> str:
    """A button that stays a button in every mail app, Outlook included (a table cell, not CSS alone)."""
    bg = BLUE if solid else "#1c1c20"
    border = BLUE if solid else "#33333a"
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:{margin}">'
            f'<tr><td bgcolor="{bg}" style="background:{bg};border:1px solid {border};border-radius:999px">'
            f'<a href="{href}" target="_blank" style="display:inline-block;padding:13px 26px;font-family:{FONT};'
            f'font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:999px">{label}</a>'
            f'</td></tr></table>')


def _page(title: str, preheader: str, body: str, footer: str) -> str:
    logo = (f'<img src="cid:{LOGO_CID}" width="156" height="48" alt="i miss you · IMY" '
            f'style="display:block;border:0;outline:none;margin:0 0 26px -4px;width:156px;height:48px">'
            if LOGO.is_file() else
            f'<p style="margin:0 0 26px;font-family:{FONT};font-size:18px;font-weight:800;color:{INK}">IMY</p>')
    pdyn = (f'<tr><td style="padding:18px 12px 0;text-align:center"><img src="cid:{PDYN_CID}" width="140" height="35" '
            f'alt="PDYN Technologies" style="display:inline-block;border:0;outline:none;width:140px;height:35px"></td></tr>\n'
            if PDYN.is_file() else "")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><meta name="supported-color-schemes" content="dark">
<title>{html.escape(title)}</title></head>
<body style="margin:0;padding:0;background:#0a0a0c" bgcolor="#0a0a0c">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:#0a0a0c">{html.escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#0a0a0c" style="background:#0a0a0c">
<tr><td align="center" style="padding:36px 14px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:480px">
<tr><td bgcolor="#121214" style="background:#121214;border:1px solid #24242a;border-radius:22px;padding:30px 30px 10px">
{logo}
<h1 style="margin:0 0 12px;font-family:{FONT};font-size:24px;line-height:1.25;font-weight:700;letter-spacing:-0.02em;color:{INK}">{html.escape(title)}</h1>
{body}
</td></tr>
<tr><td style="padding:22px 12px 0;text-align:center">{footer}</td></tr>
{pdyn}</table>
</td></tr></table>
</body></html>"""


def _footer(site: str) -> str:
    links = ""
    if site:
        s = html.escape(site, quote=True)
        links = (f'<br><a href="{s}/privacy.html" style="color:{FAINT};text-decoration:underline">What imy stores</a>'
                 f' &nbsp;·&nbsp; <a href="{s}/terms.html" style="color:{FAINT};text-decoration:underline">Terms</a>')
    return _p("imy will never ask for your password by email." + links, size=12, color=FAINT, margin="0")


def _site_of(link: str) -> str:
    """https://imy.ph/app#reset=... -> https://imy.ph"""
    base = link.split("#", 1)[0].rstrip("/")
    return base[:-len("/app")] if base.endswith("/app") else base


def reset_email(link: str, minutes: int, email: str = "") -> tuple[str, str, str]:
    """(subject, text, html) for a "forgot password" link."""
    subject = "Reset your imy password"
    who = f" for {email}" if email else ""
    text = (f"Reset your imy password\n\n"
            f"Someone (hopefully you) asked to reset the password{who}.\n\n"
            f"Choose a new password here:\n{link}\n\n"
            f"The link works once, for the next {minutes} minutes.\n\n"
            "Didn't ask for this? You can ignore this email. Your password stays the same, and nobody can "
            "use the link without getting into your inbox.\n\n"
            "imy will never ask for your password by email.\n")
    safe = html.escape(link, quote=True)
    who_html = f' for <span style="color:{INK};font-weight:600">{html.escape(email)}</span>' if email else ""
    body = (_p(f"Someone (hopefully you) asked to reset the password{who_html}. Tap the button to choose a new one.")
            + _button(safe, "Choose a new password")
            + _p(f"The link works once, for the next {minutes} minutes.", size=14)
            + '<div style="height:1px;background:#24242a;margin:6px 0 18px;line-height:1px;font-size:1px">&nbsp;</div>'
            + _p("Button not working? Copy this link into your browser:", size=13, margin="0 0 6px")
            + _p(f'<a href="{safe}" target="_blank" style="color:{LINK};word-break:break-all;text-decoration:underline">{safe}</a>',
                 size=13, margin="0 0 16px")
            + _p("Didn’t ask for this? You can ignore this email. Your password stays the same, and nobody can use "
                 "the link without getting into your inbox.", size=13, margin="0 0 22px"))
    return subject, text, _page("Reset your password", f"Choose a new password. The link works once, for {minutes} minutes.",
                                body, _footer(_site_of(link)))


def changed_email(site: str, email: str = "") -> tuple[str, str, str]:
    """(subject, text, html) telling someone their password just changed - so if it wasn't them,
    they find out now rather than when they can't sign in."""
    subject = "Your imy password was changed"
    contact = os.environ.get("IMY_CONTACT_EMAIL", "").strip()
    help_line = f"reply to this email or write to {contact}" if contact else "reply to this email"
    who = f" for {email}" if email else ""
    where = f" at {site}" if site else ""
    text = (f"Your imy password was changed\n\n"
            f"The password{who} was just changed, and every other device was signed out.\n\n"
            "If this was you, there's nothing else to do.\n\n"
            f"If it wasn't you, use \"Forgot password?\" on the sign-in page{where} straight away to choose a new "
            f"one, then {help_line}.\n\n"
            "imy will never ask for your password by email.\n")
    who_html = f' for <span style="color:{INK};font-weight:600">{html.escape(email)}</span>' if email else ""
    body = (_p(f"The password{who_html} was just changed, and every other device was signed out.")
            + _p("If this was you, there’s nothing else to do.")
            + ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:4px 0 22px">'
               '<tr><td bgcolor="#1a1a1f" style="background:#1a1a1f;border:1px solid #2a2a31;border-radius:16px;padding:18px 18px 4px">'
               + _p("Wasn’t you?", size=15, color=INK, margin="0 0 6px")
               + _p(f"Choose a new password straight away with “Forgot password?”, then {html.escape(help_line)}.",
                    size=14, margin="0 0 14px")
               + (_button(html.escape(site, quote=True) + "/app", "Go to sign-in", solid=False, margin="0 0 14px")
                  if site else "")
               + '</td></tr></table>'))
    return subject, text, _page("Your password was changed",
                                "Your imy password was just changed. If it wasn't you, here's what to do.",
                                body, _footer(site))


def admin_link_email(link: str, minutes: int) -> tuple[str, str, str]:
    """(subject, text, html) for the admin page's one-time sign-in link."""
    subject = "Your imy admin sign-in link"
    text = ("Sign in to the imy admin page\n\n"
            f"Open this link to sign in:\n{link}\n\n"
            f"It works once, for the next {minutes} minutes, in the browser you open it in.\n\n"
            "Didn't ask for this? Ignore this email - nobody can sign in without the link.\n")
    safe = html.escape(link, quote=True)
    body = (_p("Tap the button to open the admin page. You’ll stay signed in for 12 hours.")
            + _button(safe, "Sign in to admin")
            + _p(f"The link works once, for the next {minutes} minutes.", size=14)
            + '<div style="height:1px;background:#24242a;margin:6px 0 18px;line-height:1px;font-size:1px">&nbsp;</div>'
            + _p("Button not working? Copy this link into your browser:", size=13, margin="0 0 6px")
            + _p(f'<a href="{safe}" target="_blank" style="color:{LINK};word-break:break-all;text-decoration:underline">{safe}</a>',
                 size=13, margin="0 0 16px")
            + _p("Didn’t ask for this? Ignore this email. Nobody can sign in without the link.", size=13, margin="0 0 22px"))
    site = link.split("#", 1)[0].rstrip("/")
    site = site[:-len("/admin")] if site.endswith("/admin") else site
    return subject, text, _page("Sign in to admin", f"Your one-time admin link. It works for {minutes} minutes.",
                                body, _footer(site))


def feedback_email(kind_label: str, message: str, rating: int | None, reply_to: str | None, site: str) -> tuple[str, str, str]:
    """(subject, text, html) telling the admin that new feedback came in."""
    stars = f"{rating}/5" if rating else "no rating"
    first = " ".join(message.split())[:60]
    subject = f"New imy feedback: {kind_label}" + (f" - {first}" if first else "")
    who = reply_to or "Didn't ask for a reply"
    admin = f"{site}/admin" if site else ""
    text = (f"New feedback ({kind_label}, {stars})\n\n{message}\n\nFrom: {who}\n"
            + (f"\nSee all feedback: {admin}\n" if admin else ""))
    quote = html.escape(message).replace("\n", "<br>")
    body = (_p(f'<span style="color:{INK};font-weight:600">{html.escape(kind_label)}</span> &nbsp;·&nbsp; {stars}', margin="0 0 12px")
            + ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 18px">'
               '<tr><td bgcolor="#1a1a1f" style="background:#1a1a1f;border:1px solid #2a2a31;border-radius:16px;padding:16px 18px 2px">'
               + _p(quote, size=15, color=INK, margin="0 0 14px") + '</td></tr></table>')
            + _p(("From " + f'<a href="mailto:{html.escape(reply_to, quote=True)}" style="color:{LINK}">{html.escape(reply_to)}</a>')
                 if reply_to else "They didn’t ask for a reply.", size=14)
            + (_button(html.escape(admin, quote=True), "Open the feedback inbox", solid=False) if admin else ""))
    return subject, text, _page("New feedback", f"{kind_label}: {first}", body, _footer(site))


# ---------------------------------------------------------------- python -m backend.mailer you@example.com
if __name__ == "__main__":
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        print(__doc__)
        sys.exit(2)
    if not configured():
        print("Email isn't set up: IMY_SMTP_HOST is empty. See the top of this file.")
        sys.exit(1)
    try:
        send(sys.argv[1], "imy test email",
             "This is a test from imy. If you can read it, password reset emails will arrive too.\n",
             _page("It works", "A test email from imy.",
                   _p("This is a test from imy. If you can read it, password reset emails will arrive too.",
                      margin="0 0 22px"), _footer(site_url())))
    except MailError as exc:
        print(f"Not sent: {exc}")
        sys.exit(1)
    print(f"Sent to {sys.argv[1]} from {sender()} via {os.environ['IMY_SMTP_HOST']}. Check the inbox (and spam).")
    if not site_url():
        print("Also set IMY_PUBLIC_URL to the site's https address, or reset links can't be made on the live site.")
