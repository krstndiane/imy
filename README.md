# imy (I Miss You)

Teach an app how someone writes, then chat with that style again.

**Two modes**
- **Training:** a few quick basics, then add chats (upload a chat export, screenshots or pasted text), check what was read, then "Get to know them": one question per screen. Questions live in `backend/questions.json`; "how would they reply?" answers become training examples the engine uses first.
- **Chat:** pick a person and talk. The chat looks like the app you talk in (Messenger, iMessage, Instagram, WhatsApp, Telegram or Viber, light or dark, even your own bubble colours), and they "read" and "type" like a real person.

**What it runs on**

| Part | Service | Without it (your own computer) |
|---|---|---|
| Replies, reading screenshots, describing photos | **Groq**, through **Pydantic AI** (`backend/llm.py`) | replies are imy's own pattern-based ones; screenshots can't be read |
| Database | **Turso** (hosted SQLite, `backend/turso.py`) | the file `data/imy.db` |
| Screenshots, profile pictures, shared photos | **Supabase Storage** (`backend/storage.py`) | the folder `data/uploads/` |
| Email (forgot password, admin sign-in) | **Resend**, from `pdyntech.com` (`backend/mailer.py`) | the link is printed in the Terminal |

Each one switches on when its key is in `.env`, so imy runs on your laptop with no accounts at all,
and the live site keeps nothing on the server itself.

imy never connects to Messenger, Instagram or any other account and never asks for their
passwords: people download a chat themselves, upload it, and choose which parts imy learns from.

---

## Setup (about 10 minutes)

You need Python 3.10 or newer.

```bash
cd imy
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
cp .env.example .env               # then fill in what you have (see "The services" below)
uvicorn backend.main:app --reload
```

Open **http://127.0.0.1:8000**. The built website is already in `frontend/dist`. Make an account
(email and a password of 8+ characters); everything you set up belongs to it.

`sentence-transformers` is large (it pulls in PyTorch) and downloads a ~90 MB model on first use. If it
can't, imy uses a lighter TF-IDF matcher instead (force it with `IMY_EMBEDDER=tfidf`; the server does).

**Check what's working:** `python -m backend.services` tries Groq, Turso and Supabase with your keys
for real (add your email to also send a test email). `python -m backend.preflight` checks every setting.

**Edit the website:**
```bash
cd frontend && npm install && npm run dev     # http://localhost:5173 (talks to port 8000)
npm run build                                  # rebuild frontend/dist
```

**Run the tests:** `pytest backend/tests -q`. They need no internet and no keys: Turso, Supabase,
Resend and Pydantic AI are replaced by small stand-ins that speak the same HTTP (`backend/tests/fakes.py`).
Your real `.env` keys are blanked for the test run (`backend/tests/conftest.py`).

---

## The services

### Groq (the AI)
1. console.groq.com/keys → **Create API Key** → `GROQ_API_KEY=` in `.env`.
2. Turn on **Zero Data Retention** (Settings → Data Controls): by default Groq may keep requests up to 30 days to investigate abuse.

imy asks Groq which models exist, uses the best one, and rests a model for a while when it hits a
limit (`backend/llm.py`). Screenshots and photos go to whichever current model can see images.
Pick other models with `IMY_LLM_MODEL` / `IMY_VISION_MODEL`.

### Turso (the database)
1. turso.tech → sign up → create a database in the region closest to your server (Tokyo or Singapore for the Philippines).
2. `turso db show <name> --url` → `IMY_TURSO_URL` (starts with `libsql://`).
3. `turso db tokens create <name>` → `IMY_TURSO_TOKEN`.

imy creates its tables on the first start. It talks to Turso over its HTTP API, so nothing extra is
installed, and every query is one quick request (the server and the database in the same region keeps that fast).

### Supabase (the pictures)
1. supabase.com → New project.
2. Project Settings → API: the **Project URL** → `SUPABASE_URL`, the **service_role** key → `SUPABASE_SERVICE_KEY`.
   That key is secret: it lives only on the server, never in the website.

imy creates a **private** bucket (`imy-uploads`) by itself. Pictures are only ever shown through imy at
`/uploads/...`, which checks the person they belong to is yours.

### Resend (email from pdyntech.com)
1. resend.com → Domains → **Add Domain** → `pdyntech.com`.
2. Resend lists DNS records (a TXT for SPF, a TXT for DKIM, an MX for bounces). Add each one in
   **Route 53** → Hosted zones → `pdyntech.com` → Create record, exactly as shown. Wait for **Verified**.
3. API Keys → Create (Sending access) → `RESEND_API_KEY`. Keep `IMY_MAIL_FROM=imy <no-reply@pdyntech.com>`.
4. Test: `python -m backend.mailer you@example.com`.

Replies to imy's emails go to `IMY_CONTACT_EMAIL`. Without Resend, any SMTP service works (`IMY_SMTP_*` in `.env.example`).

### The address: imy.pdyntech.com
In Route 53 → `pdyntech.com` → Create record: name `imy`, type **A**, value = the server's public IP,
TTL 300. Caddy (in `deploy/`) then gets the HTTPS certificate by itself. `sinag.pdyntech.com` isn't
affected: each subdomain has its own record.

---

## Getting good results

1. **Upload the chat export when you can (best).** It's exact, keeps every emoji, and usually has thousands of messages. Read by `backend/importers.py`:
   - **WhatsApp:** chat > Export chat > Without media (`.zip` or `.txt`)
   - **Messenger:** facebook.com/dyi > Messages > Format **JSON** or HTML. Newer end-to-end encrypted chats are a separate download (messenger.com > Privacy & safety > End-to-end encrypted chats > Message storage)
   - **Instagram:** Download your information > Messages > **JSON** or HTML
   - **Telegram Desktop:** Export chat history (`messages.html` or `result.json`)
   - **LINE:** Export chat history (`.txt`); **Discord:** DiscordChatExporter JSON
2. **Choose the parts.** After picking the chat and who's who, imy shows it split into parts (a new one after 6+ hours of quiet). Untick an argument, someone else's business, anything private, or pick a date range.
3. **Screenshots** are for chats that aren't in the download. Groq reads them (no OCR), so full-width, uncropped ones work best, and crop out anything private first.
4. **Always do the Review step.** Fix wrong speakers and typos there.
5. **More back-and-forth beats more screenshots.** Replies learn from exchanges where you said something and they answered.

## Private details

Every message is checked as it's saved (`backend/sensitive.py`): passwords, one-time codes and PINs,
card numbers (checksum-verified), bank and e-wallet account numbers, PH government IDs and student
numbers, usernames, API keys, phone numbers and emails become markers like `[password hidden]`. It
understands Taglish ("password ko ay...", while "pw ka ba?" meaning *pwede* is left alone) and a
password sent in its own bubble after "password ko:". It covers uploads, pasted text, edits, things
taught in the chat and what's typed in the chat. Chats saved before it existed are cleaned once on start.

A screenshot's *picture* can't be cleaned, and Groq sees it to read it, so the upload screen says to
crop those out, and Review marks screenshots that still show a hidden detail.

## Accounts and privacy

- **Passwords** are `scrypt` hashes with their own salt (`backend/auth.py`); the password itself is never stored.
- **Staying signed in** is one `HttpOnly`, `SameSite=Lax` cookie (`Secure` on HTTPS) holding a random token; only its hash is stored. 30 days.
- **Forgot password** emails a one-time link (30 minutes), never a password. It answers the same whether or not an account exists, and each account gets at most 3 an hour.
- **Sign-in attempts** are rate limited, and a wrong email and a wrong password look the same.
- **"Delete everything"** removes the account, every person, every message and every uploaded file (from Turso and Supabase). It asks for the password and the typed word DELETE.
- **"Download my data"** builds a zip of everything the account holds, pictures included (`backend/account_export.py`).
- **The outside-AI switch** (account menu, on by default): off means nothing from that account goes to Groq: no AI replies, no photo descriptions, no screenshot reading, no memory notes. One check, `auth.cloud_ai_allowed()`, covers every call.
- **Terms and consent:** sign-up records the Terms version; adding a person asks "these are conversations I was part of" once. When the Terms change, bump `TERMS_VERSION` in `auth.py`.
- **Every chat says what it is:** "Replies are written by AI from their old messages. It isn't really them."
- The **privacy and terms pages** are `frontend/public/privacy.html` and `terms.html`; the contact address comes from `IMY_CONTACT_EMAIL`.

## How a reply is made (`backend/engine.py`)

1. **What you taught it** comes first, word for word: "how would they reply?" answers and 👍 / **Fix** in the chat.
2. **Groq writes everything else** as them: their measured style, a spread of their real messages, the most similar real back-and-forths, what they remember, and any photo you just sent. It can really answer questions, in their voice. (`IMY_AI_MODE=fallback` asks Groq only when imy's own reply isn't confident.)
3. **Groq busy, over the limit, or no key:** imy's own reply from `smalltalk.py` / `personality.py`. Nothing breaks.

Each account gets a daily share of AI replies (`IMY_AI_DAILY_PER_ACCOUNT`, default 50); reading
screenshots doesn't use it up. `IMY_AI_DAILY_SITE` caps the whole site once you pay for Groq.

## In the chat

Profile pictures and nicknames (both ways), chat colour and quick reaction, photos and files (Groq
says what a photo shows so the reply is about it), reactions, replies, unsend, a media list, and
**What they remember**: notes imy keeps from your conversation (`backend/notes.py`) so they can bring
things up later. You can read and clear them from the chat menu.

## Backups

```bash
python -m backend.backup                 # one backup now, into data/backups/
python -m backend.backup --list          # what you have
python -m backend.backup --check FILE    # prove one opens and is complete
python -m backend.backup --restore FILE  # unpack into a NEW folder and check it (never touches live data)
```

With Turso, a backup is the whole Turso database copied into one SQLite file, so the zip is the same
either way; Turso also keeps its own short point-in-time history. Pictures stay in Supabase. Locally,
the zip holds `data/imy.db` and `data/uploads/`. `IMY_BACKUP_DAILY=1` makes one every 24 hours, and
`bash deploy/get-backup.sh ubuntu@SERVER_IP key.key` downloads a fresh one to your Mac.

## Deploying

The `Dockerfile` packs the API and the website into one container. With Turso and Supabase set, the
container keeps nothing, so it can be rebuilt or moved freely.

```bash
docker build -t imy .
docker run -p 8000:8000 --env-file .env imy
```

### On a server with deploy/ (Oracle Cloud Always Free, AWS Lightsail, any Ubuntu VPS)
1. Make an Ubuntu server and open TCP **80** and **443** to the internet (Oracle: the subnet's Security List).
2. Point `imy.pdyntech.com` at its IP (Route 53, above).
3. From the imy folder on your Mac:
   ```bash
   bash deploy/push-to-server.sh ubuntu@SERVER_IP ~/Downloads/ssh-key.key
   ```
   The first time it asks for the address and the Groq, Turso, Supabase and Resend keys (kept only on
   the server in `~/imy/.env`), installs Docker, starts imy with Caddy in front for HTTPS, tries every
   service, and runs the launch checks. **Run the same command to update the site.**

Oracle may reclaim free servers it considers idle; upgrading to Pay As You Go (still free within the
Always Free limits) is the usual way to avoid that. [deploy/LAUNCH.md](deploy/LAUNCH.md) is the tick-off list.

### Before launch
```bash
python -m backend.services                            # every service really answers
python -m backend.preflight --url https://imy.pdyntech.com   # settings, email, HTTPS, the live site from outside
```
Then: rotate any key that was ever pasted somewhere, turn on Groq's Zero Data Retention, set up a free
uptime monitor on `https://imy.pdyntech.com/api/health`, and check the Data Privacy Act of 2012 (the
National Privacy Commission requires registration once you process sensitive personal information
of 1,000 or more people).

## Running it

- **The admin page:** `/admin`. Type an admin email (`IMY_ADMIN_EMAILS`, or `IMY_CONTACT_EMAIL`) and imy emails a one-time sign-in link; on your own computer, `python -m backend.admin link` prints one. Usage, accounts (counts only, never chats), a map of where people are, and the feedback inbox.
- **Deletion requests:** `python -m backend.admin remove their@email` previews, `--yes` deletes (same as "Delete everything").
- **Accounts and usage:** `python -m backend.admin accounts`, `python -m backend.admin usage`.
- **A key leaked:** make a new one, put it in `~/imy/.env` on the server, run `deploy/push-to-server.sh` again, delete the old one.

## Where to tweak things

| I want to... | Edit |
|---|---|
| Change the interview questions | `backend/questions.json` |
| Change what the AI is told for replies | `ai_prompt` in `backend/engine.py` |
| Change how screenshots are read | `SCREEN_INSTRUCTIONS` and `ScreenRead` in `backend/llm.py` |
| Hide another kind of private detail | the patterns in `backend/sensitive.py` (add a test in `tests/test_sensitive.py`) |
| Change how fast they "type" | `typingMs` / `readMs` in `frontend/src/components/Chat.jsx` |
| Change an app's look | `.skin-<app>` in `frontend/src/styles.css`; behaviour in `frontend/src/platforms.js` |
| Change how style is measured | `backend/style.py` |
| Re-theme | the variables at the top of `frontend/src/styles.css` |

## Project layout
```
backend/   main.py (API)  config.py (.env)  db.py + turso.py (database)  storage.py (pictures)
           auth.py (accounts)  mailer.py (email)  llm.py (Groq via Pydantic AI)  sensitive.py (private details)
           engine.py + smalltalk.py + personality.py + style.py (replies)  importers.py + parser.py (chat files)
           looks.py (chat look)  media.py (shared photos)  notes.py (what they remember)  usage.py (daily AI limits)
           backup.py  preflight.py  services.py  admin.py  questions.json  tests/
frontend/  src/App.jsx  src/components/*.jsx  src/styles.css  dist/ (built)  public/{landing,privacy,terms,admin}.html
deploy/    Caddyfile  docker-compose.yml  server-setup.sh  push-to-server.sh  get-backup.sh  LAUNCH.md
data/      local only (never in git): imy.db, uploads/, backups/
```

## Known limits
- **Screenshots need Groq.** Without a key (or with the outside AI switched off) upload the chat file or paste the text instead.
- **Without Groq, replies are pattern-based:** they sound like the person but can be generic.
- **Private-detail hiding is pattern matching.** It can miss something or hide a harmless word; Review is where you catch the rest.
- **Speed figures** in earlier versions were measured on the local database file. On Turso every query is a network request, so keep the server and the database in the same region.

## License

Copyright © 2026 Kristina Diane Aviles / PDYN Technologies. All rights reserved — see
[LICENSE](LICENSE). The code is here to be read, not reused; ask first at pdyntechnology@gmail.com.
