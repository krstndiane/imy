"""Shared test setup.

The tests must never reach a real service, even on a computer whose .env has real keys in it. imy
reads .env when it starts, but never overrides a setting that's already there - so these are set to
empty here first, before any test imports imy. A test that needs one of them sets it itself.
"""
import os

for _key in (
    "GROQ_API_KEY", "IMY_LLM_MODEL", "IMY_VISION_MODEL", "IMY_AI_MODE",
    "IMY_TURSO_URL", "IMY_TURSO_TOKEN", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN",
    "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY",
    "RESEND_API_KEY", "IMY_SMTP_PASSWORD", "IMY_SMTP_USER",
    "IMY_CONTACT_EMAIL", "IMY_ADMIN_EMAILS", "IMY_FEEDBACK_TO", "IMY_MAIL_FROM", "IMY_PUBLIC_URL",
):
    os.environ.setdefault(_key, "") if os.environ.get("IMY_TESTS_KEEP_ENV") else os.environ.__setitem__(_key, "")
