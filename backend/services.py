"""services.py - try every outside service with the keys in .env, for real, and say which work.

    python -m backend.services                      Groq (text + image), Turso, Supabase
    python -m backend.services you@example.com      ...and send a test email (Resend or SMTP)

Run it after adding a key, and once on the server after deploying. Unlike preflight, this really
talks to each service: one short Groq reply, one tiny image look, a read from Turso, and a test file
saved to Supabase and removed again. Nothing about your users is sent anywhere.
"""
import sys
import time

from PIL import Image, ImageDraw

from . import config  # noqa: F401  (loads .env)
from . import db as db_mod, llm, mailer, storage

results: list[tuple[str, str, str]] = []


def line(level: str, what: str, advice: str = "") -> None:
    results.append((level, what, advice))
    print(f"[{level:^6}] {what}" + (f"\n         -> {advice}" if advice else ""), flush=True)


def check_groq() -> None:
    if not llm.providers():
        line("SKIP", "Groq: no GROQ_API_KEY", "Add it to .env (console.groq.com/keys).")
        return
    started = time.time()
    try:
        models = llm._candidates("chat")
        if not models:
            raise llm.LLMError("no Groq model is available right now")
        text = llm._run(models[0], "Answer in one short word.", "Say hi.", temperature=0.0, max_tokens=50)
        line("OK", f"Groq replies through Pydantic AI ({models[0]}, {time.time() - started:.1f}s): {text[:40]!r}")
    except llm.LLMError as exc:
        line("FAIL", f"Groq didn't answer: {llm.explain(exc)}", str(exc)[:300])
        return
    img = Image.new("RGB", (540, 300), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((20, 40, 300, 100), 20, fill=(230, 230, 235))
    d.text((40, 62), "kain ka na?", fill=(0, 0, 0))
    d.rounded_rectangle((260, 140, 520, 200), 20, fill=(0, 132, 255))
    d.text((280, 162), "oo hehe", fill=(255, 255, 255))
    try:
        out = llm.read_screenshot(img, [])
        said = " / ".join(f"{m['speaker']}: {m['text']}" for m in out["messages"]) or "(no messages found)"
        line("OK", f"Groq reads screenshots ({out['meta'].get('reader', '?')}): {said[:80]}")
    except llm.LLMError as exc:
        line("FAIL", f"Groq couldn't read a test screenshot: {llm.explain(exc)}", str(exc)[:300])


def check_turso() -> None:
    if db_mod.db_kind() != "turso":
        line("SKIP", "Turso: not set (IMY_TURSO_URL / IMY_TURSO_TOKEN); using data/imy.db")
        return
    try:
        started = time.time()
        with db_mod.db() as conn:
            n = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
        line("OK", f"Turso answers ({n} tables, {1000 * (time.time() - started):.0f} ms per request)")
    except Exception as exc:
        line("FAIL", f"Turso: {exc}", "Check IMY_TURSO_URL (libsql://...) and IMY_TURSO_TOKEN.")


def check_supabase() -> None:
    if storage.kind() != "supabase":
        line("SKIP", "Supabase: not set (SUPABASE_URL / SUPABASE_SERVICE_KEY); pictures go in data/uploads")
        return
    problem = storage.check()
    if problem:
        line("FAIL", f"Supabase Storage: {problem}", "Check SUPABASE_URL and that SUPABASE_SERVICE_KEY is the service_role key.")
    else:
        line("OK", "Supabase Storage: a test file was saved, read back and removed")


def check_email(to: str) -> None:
    if not mailer.configured():
        line("SKIP", "Email: not set (RESEND_API_KEY)")
        return
    try:
        mailer.send(to, "imy test email", "This is a test from imy. If you can read it, emails work.\n")
        line("OK", f"Email sent to {to} via {mailer.service()} - check the inbox (and spam)")
    except mailer.MailError as exc:
        line("FAIL", f"Email: {exc}")


def main(argv: list[str]) -> int:
    db_mod.init_db()
    check_groq()
    check_turso()
    check_supabase()
    if argv and "@" in argv[0]:
        check_email(argv[0])
    failed = sum(1 for level, *_ in results if level == "FAIL")
    print(f"\n{sum(1 for l, *_ in results if l == 'OK')} working, {failed} failing, "
          f"{sum(1 for l, *_ in results if l == 'SKIP')} not set up.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
