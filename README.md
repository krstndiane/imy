# imy (I Miss You)

Teach an app how someone writes, then chat with that style again.
It runs on your own computer, or on a server once deployed. With a free Groq key, an AI helps with
replies the trained data can't handle (see **Smart replies** below); without one, nothing leaves the machine.

**Two modes**
- **Training:** 4 quick basics, then add chats (import a chat export, upload screenshots or paste text), check what was read, then "Get to know them": one question per screen (confirm what imy noticed, this-or-that taps, "how would they reply?", little details). Questions live in `backend/questions.json`; "how would they reply?" answers become training examples the engine uses first.
- **Chat:** pick a person and talk. The chat looks like the app you talk in (Messenger, iMessage, Instagram, WhatsApp, Telegram or Viber, light or dark, even your own bubble colours, all copied from your screenshots), and they "read" and "type" like a real person: "ok" comes back in a second or two, a paragraph takes as long as typing it would.

---

## Setup (about 10 minutes)

You need Python 3.10+ and Tesseract (the OCR engine).

### 1. Install Tesseract
| System | Command |
|---|---|
| macOS | `brew install tesseract` |
| Ubuntu/Debian | `sudo apt install tesseract-ocr` |
| Windows | Install from https://github.com/UB-Mannheim/tesseract/wiki, then set `TESSERACT_CMD` to `C:\Program Files\Tesseract-OCR\tesseract.exe` |

### 2. Install the Python packages
```bash
cd imy
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
```
`sentence-transformers` is large (it pulls in PyTorch) and downloads a ~90 MB model on first use.
If it can't be installed or downloaded, imy automatically falls back to a lighter TF-IDF matcher
(a bit less smart, still works). Force that with `IMY_EMBEDDER=tfidf`.

### 3. Run it
```bash
uvicorn backend.main:app --port 8000
```
Open **http://localhost:8000**. The built frontend is already included in `frontend/dist`.

The first time, make an account (email and a password of 8+ characters). Everything you set up
belongs to that account. If you're upgrading a copy of imy from before accounts existed, the first
account you make adopts the people already in it, so nothing is lost.

### Optional: edit the frontend
```bash
cd frontend && npm install && npm run dev     # http://localhost:5173 (proxies to port 8000)
npm run build                                  # rebuild frontend/dist
```

### Run the tests
```bash
pytest backend/tests -q
```

---

## Getting good results

1. **Put your own messages on the right.** That's how the parser decides who is who. Use "Swap sides" in the review step if a screenshot is the other way round.
2. **Use full-width, uncropped, high-resolution screenshots.** Light or dark mode is fine. imy recognises the app (Messenger, iMessage, Instagram, WhatsApp, Telegram, Viber), skips the header, pinned messages, quoted replies and the message box, keeps only the person's messages in group chats, and warns you if a screenshot is from a chat with someone else.
3. **Always do the Review step.** OCR is never perfect; fix wrong speakers and typos there. This is the biggest accuracy gain.
4. **Import a chat export when you can (best).** In the Screenshots step, use **Import a chat export** with the file the app itself gives you. It's exact (no OCR), keeps emojis, and usually has thousands of messages. Supported, all read by `backend/importers.py`:
   - **WhatsApp:** chat > Export chat > Without media (`.zip` or `.txt`)
   - **Messenger:** facebook.com/dyi > Messages > Format **JSON** or HTML (`.zip`, or one `message_1.json`). Newer end-to-end encrypted chats are a separate download (messenger.com > Privacy & safety > End-to-end encrypted chats > Message storage)
   - **Instagram:** Download your information > Messages > **JSON** or HTML
   - **Telegram Desktop:** Export chat history (`messages.html` or `result.json`)
   - **LINE:** Export chat history (`.txt`); **Discord:** DiscordChatExporter JSON
   - Files are recognised by what's inside, not their name, and Facebook/Instagram downloads in HTML work too. The chat whose name matches the person is listed first and picked for you.

   The chat is split into separate "talks" wherever there were 6+ hours of silence (`SESSION_GAP_HOURS` in `importers.py`). With a big import, try `IMY_REUSE_LINES=1` so replies can be their real lines.
   Pasting `Name: message` lines also still works.
5. **More back-and-forth is better than more screenshots.** Replies come from exchanges where you said something and they answered.

## Accounts and privacy

Every person, message and photo belongs to one account, and no other account can reach them — not
the pages, not the API, and not the uploaded files, which are served only to the account that owns
them rather than to anyone who knows the address.

- **Passwords** are stored as a `scrypt` hash with its own random salt (`backend/auth.py`). The
  password itself is never written down, so it can't be read back out of the database.
- **Staying signed in** is one cookie holding a random token; only a hash of that token is stored,
  and it's `HttpOnly` (JavaScript can't read it), `SameSite=Lax`, and `Secure` when imy is on
  HTTPS. Sessions last 30 days. Signing out deletes the session on the server.
- **Changing your password** signs out every other browser, and emails a short "your password was
  changed" notice so a stranger changing it doesn't go unnoticed.
- **Forgot password** (on the sign-in page) emails a link, never a password: a password in an inbox
  stays readable forever. The link opens "Choose a new password", works **once**, for **30 minutes**,
  and using it signs out every device and cancels any other links. Only a hash of it is stored, the
  page answers the same whether or not an account exists, and each account gets at most 3 emails an
  hour. The link points at `IMY_PUBLIC_URL`, never at whatever address the request claims to come
  from. Setting up email is under **Going live** below.
- **Sign-in attempts** are rate limited, and a wrong email and a wrong password give the same answer
  (and take the same time), so nobody can use the form to find out which emails have accounts.
- **"Delete everything"** (account menu) removes the account, every person, every message and every
  uploaded file. It asks for the password and the typed word DELETE, and there's no undo.
- **Terms and consent.** Making an account means ticking "I'm 18 or over and agree to the Terms";
  the version and time are kept with the account (`TERMS_VERSION` in `auth.py`). Adding a person
  asks "these are conversations I was part of, and I won't use imy to pretend to be them" once, and
  keeps that time too. imy can't check that the person being copied agreed — the Terms put that on
  the account, and anyone who's been copied can ask for removal.
- **Every chat says what it is.** The top of each conversation reads "Replies are written by AI from
  their old messages. It isn't really them."
- **The privacy and terms pages** are `frontend/public/privacy.html` and `terms.html`, served at
  `/privacy.html` and `/terms.html` and linked from sign-up, the account menu and the landing page.
  Your contact address is filled in from **`IMY_CONTACT_EMAIL`** — set it before you go live.

## How many people can it handle?

Measured on a database of 500 accounts, 1,500 people and ~1 million messages (90 MB), on one
uvicorn process, with everyone sending a message every 15 seconds like a real conversation:

| People chatting at once | Median reply | Slowest | Failed |
|---|---|---|---|
| 50 | 49 ms | 139 ms | 0 |
| 150 | 43 ms | 345 ms | 0 |
| 300 | 247 ms | 2.5 s | 0 |
| 400 | 2.4 s | 6.6 s | 0 |

**About 300 people chatting at the same time** is comfortable on one process. Past that it gets
slower but never breaks — nothing errored at any level, it just queues. As a rough guide that
covers somewhere in the region of a few thousand people a day, since only a slice of them are ever
chatting at the same moment.

### What actually runs out first

**Not the database.** Every query is under a millisecond even at a million messages, because they
all use an index. In order, the real limits are:

1. **The AI quota.** The free Groq tier is a few hundred replies a day *shared across your whole
   site*. Each account now gets a daily share (`IMY_AI_DAILY_PER_ACCOUNT`, default 50, shown in the
   account menu), so one busy person can't use it up for everyone; past it, replies come from imy
   itself. That makes it fair, not bigger — for real numbers of people you need Groq's paid plan,
   and `IMY_AI_DAILY_SITE` caps the bill. `python -m backend.admin usage` shows today's use.
2. **Reading screenshots.** OCR costs about **6 seconds of CPU per screenshot** — by far the
   heaviest thing imy does. A thousand new people uploading 20 screenshots each is roughly 33 hours
   of CPU. Spread over a day on a few cores that's survivable; arriving in one hour it is not.
   Uploads queue rather than fail, so a rush shows up as a slow upload screen.
   **This is why the Training screen leads with "Upload your conversation".** One export file is
   measured at ~380x less CPU than a set of screenshots and gives ~100x more messages to learn
   from. Every person who uses an export instead of screenshots is a person who costs you almost
   nothing to onboard.
3. **Disk.** About 185 KB of messages per account, plus **~700 KB per screenshot**, which is the
   bulk of it. Budget roughly 15–20 GB per thousand active people.
4. **One Python process** working out replies.

Screenshots are deliberately *not* compressed. Saving them as JPEG or scaling them down is 26–60%
smaller, but tested against the real reader on real screenshots it changed what imy read out of
7 of 9 of them. Disk is cheaper than reading someone's messages wrong.

### How it stays fast

**Writes are queued, not scrambled.** SQLite allows one writer at a time, and left alone it settles
that by letting whoever loses sleep and retry — which, under load, left some writes waiting four
seconds while the typical one took twenty milliseconds. imy now forms an orderly queue inside the
process (`backend/db.py`), which brought the worst case down to about 0.4 s. That queue only covers
one process, which is why the Dockerfile runs a single worker.

**How each person writes is worked out once and kept.** Building it takes ~40 ms and ~0.4 MB; a
reply from someone already in that cache takes ~9 ms. imy keeps the 200 most recent
(`IMY_CACHE_PEOPLE`, about 75 MB) and only reads their training messages back out of the database
when it genuinely has to rebuild. Give it 1 GB of memory rather than 512 MB with this on.

### When you outgrow one machine

In this order, not any other: move the database to **Postgres** first, *then* run several workers.
The write queue and the index cache both live inside one process, so adding workers while still on
SQLite puts writes back to fighting each other and duplicates the memory.

## Backups

```bash
python -m backend.backup                 # one backup now, into data/backups/
python -m backend.backup --db-only       # skip the photos (much smaller)
python -m backend.backup --list          # what you have
python -m backend.backup --check FILE    # prove one: checksums, database check, what's inside
python -m backend.backup --restore FILE  # unpack into a NEW folder and check it (never touches live data)
```

Each backup is a single zip: the database plus every uploaded file. The database is copied with
SQLite's own online backup, so it's safe to run while imy is serving people. The newest 7 are kept
(`--keep N`, or `IMY_BACKUP_KEEP`). Set `IMY_BACKUP_DAILY=1` and imy makes one every 24 hours by
itself.

**To restore:** run `--restore` on the backup (it unpacks into `data/restored-<time>/` and checks
it), then stop imy, move the current `data/imy.db` (with its `-wal`/`-shm` files) and `data/uploads/`
aside, move the restored ones into `data/`, and start imy. Try it once before you need it.

A backup on the same disk saves you from a bad deploy or a mistaken delete, not from losing the
disk. Also turn on your host's snapshots, and download a zip now and then.

## In the chat

Everything below is kept on the server (SQLite + `data/uploads/`), so closing the browser, switching
device or restarting imy loses nothing. Tap their name at the top to open **Profile & nicknames**.

- **Profile pictures.** Theirs and yours. Stored as small squares; theirs shows in the sidebar, the header,
  beside their messages and as the Messenger "seen" circle.
- **Nicknames**, both ways, like Messenger. The one you give them replaces their name everywhere in the chat;
  the one they call you is given to the AI, so it comes out in their replies.
- **Chat colour and quick reaction** — the Messenger palette, and which emoji the 👍 button sends.
- **Send photos and files** with the 📎 button, or several at once. If an AI with sight is set up (see below),
  imy writes down what the photo shows, so the reply is *about the photo*. Without it, photos still send and save.
- **React** to any message (tap it), **Reply** to quote it, **Unsend** your own — the bubble stays as
  "You unsent a message", like Messenger.
- **Media & files** lists everything shared in the chat; tap a photo to see it full size.
- **What they remember.** After every few messages imy quietly notes the things worth remembering from your
  conversation (`backend/notes.py`) — an exam on Friday, a dog's name — and gives them to the AI with every
  reply, so they can bring it up later. You can read that list and clear it from the same menu.

## How a reply is made (`backend/engine.py`)

1. **What you taught it** comes first, word for word: "how would they reply?" answers from the interview, and 👍 / **Fix** in the chat.
2. **Simple moments** (hi, haha, goodnight, thanks...) are answered locally from their style (`smalltalk.py`), in their casing, Taglish, endearments and emojis.
3. **Everything else** goes to the free AI (if a key is set, see below), which writes the reply *as them*: it gets their measured style, a spread of their real messages and the most similar real back-and-forths. It can actually answer questions and give advice, still in their voice.
4. **No key, no internet, or the free limit reached:** imy's own reply from `smalltalk.py` / `personality.py`. Nothing breaks.

Every reply can show a **"why this reply?"** link (turn it on in *How they write*).

## Smart replies (free)

1. Get a free key at https://console.groq.com/keys (sign in, *Create API Key*, copy it).
2. In the imy folder, copy `.env.example` to `.env` and paste the key after `GROQ_API_KEY=`.
3. Restart imy. The terminal says `AI brain on: groq`.

**Why Groq and not Gemini by default:** Groq is free, answers in about a second, and by default doesn't keep what you send.
Google's *free* Gemini tier says not to send personal information, may let people read what's sent, and can't be used for
users in Europe or the UK. imy sends chat messages, so only use Gemini with billing turned on (then add `GEMINI_API_KEY`
and `IMY_GEMINI_PAID=1`). With both keys, Groq is used first and Gemini takes over when Groq's free limit is hit.

Free limits are shared by everyone using your copy of imy (a few hundred AI replies a day on Groq's free plan). imy only uses
the AI when its own data isn't enough, gives each account a daily share (`IMY_AI_DAILY_PER_ACCOUNT`, see `usage.py`), rests a
model for a minute when its limit is hit, and falls back to its own replies.

**Turn on Zero Data Retention at Groq** (console → Settings → Data Controls). By default Groq doesn't keep what's sent, but it
may keep it up to 30 days to investigate errors or abuse; ZDR switches that off too.
For a big public launch you'll want a paid plan (Groq's is inexpensive). `IMY_AI_MODE=always` uses the AI for every reply.

Optional, still supported: a local model with Ollama (*How they write* > "Use a local model"), and Claude (`ANTHROPIC_API_KEY`,
paid; it also reads screenshots).

## Where to tweak things

| I want to... | Edit |
|---|---|
| Change the interview questions | `backend/questions.json` (no code changes needed) |
| Change how fast they "type" | `typingMs` / `readMs` at the top of `frontend/src/components/Chat.jsx` |
| Change an app's look | its `.skin-<app>` block in `frontend/src/styles.css`; its behaviour (receipts, "typing…") in `frontend/src/platforms.js` |
| Teach imy to recognise an app in screenshots | `PALETTES` and `HINTS` in `backend/looks.py` |
| Change what the AI is told | `ai_prompt` in `backend/engine.py` |
| Change what the no-match replies say or how they're chosen | `BANKS`, `TRAIT_PATTERNS` in `backend/personality.py` |
| Make matching stricter or looser | `THRESHOLDS` in `backend/engine.py` |
| Add phrases like "goodnight" that get special handling | `INTENTS`, `TEMPLATES` in `backend/engine.py` |
| Change how style is measured | `backend/style.py` |
| Improve who-said-what detection | `backend/parser.py` (`parse_screen`, `bubble_side`) and `backend/ocr.py` |
| Support another language better | `IMY_MODEL=paraphrase-multilingual-MiniLM-L12-v2`; add Tesseract language packs and set the `lang` in `ocr.py` |
| Re-theme | the variables at the top of `frontend/src/styles.css` |

## Project layout
```
backend/   main.py (API)  db.py (SQLite)  auth.py (accounts + sign-in)  backup.py  usage.py (daily AI limits)
           mailer.py (forgot-password emails)  preflight.py (launch checks)  admin.py (accounts from the command line)
           ocr.py + parser.py + looks.py (screenshots)  importers.py (chat files)
           engine.py + smalltalk.py + personality.py + style.py (replies)  llm.py (free AI)
           media.py (photos shared in a chat + describing them)  notes.py (what they remember)  questions.json  tests/
frontend/  src/App.jsx  src/platforms.js  src/styles.css  dist/ (built)  public/{privacy,terms,landing}.html
           src/components/{Auth,ResetPassword,Account,Sidebar,Trainer,TrainPanel,Chat,ChatPanels,Avatar,Icon,ErrorBoundary}.jsx
data/      created on first run: imy.db + uploads/ (screenshots, profile pictures, shared photos) + backups/
```

## Deploying

The `Dockerfile` packs everything (API, website, Tesseract) into one container:

```bash
docker build -t imy .
docker run -p 8000:8000 -v imy-data:/data --env-file .env imy
```

Any host that runs Docker works (Render, Railway, Fly.io, a small VPS). Give it a **persistent disk mounted at `/data`**
(database + screenshots), set the variables from `.env.example` in the host's settings (never upload `.env` itself), and
point its health check at `/api/health`. Give it **1 GB of memory** (the cache of how people write is ~75 MB, and reading
screenshots needs room).

Built to stay up: short database locks (uploads and chats never block each other), one bad screenshot doesn't fail a batch,
a failed reply falls back to a simpler one, every error comes back as a friendly message, a broken screen shows "Try again"
instead of a blank page, and the website reconnects by itself if the server restarts. There's a per-visitor speed limit too.

### Free hosting: Oracle Cloud "Always Free"

A permanent free server with a real disk (2 processors, 12 GB memory as of mid-2026). `deploy/` has everything:
Caddy in front for https (free certificate, renewed by itself), imy behind it, data on a Docker volume.

1. **Make the server** (oracle.com/cloud/free): Compute → Instances → Create. Image **Ubuntu 24.04**, shape
   **Ampere A1 Flex** (1–2 OCPU, 6–12 GB). Download the **private key**. Note the **public IP**.
2. **Open the doors:** the instance's subnet → Security List → add Ingress rules for TCP **80** and **443**
   from `0.0.0.0/0`.
3. **Free address:** at duckdns.org, make a subdomain (e.g. `imy-pdyn`) and set its IP to the server's.
4. **From the imy folder on your Mac:**
   ```bash
   bash deploy/push-to-server.sh ubuntu@SERVER_IP ~/Downloads/ssh-key.key
   ```
   The first time, it asks for the address, the Gmail App Password and the Groq key (kept only on the server),
   installs Docker, starts everything and runs the launch checks. **Run the same command to update the site.**
   It never sends `.env`, `data/` or `.venv`, and an update never touches the server's database.

Oracle may stop free servers it considers idle; upgrading the account to Pay As You Go (still free within the
Always Free limits) is the usual way to avoid that.

### Going live

**1. Settings on the host** (from `.env.example`; never upload `.env` itself):
`GROQ_API_KEY`, `IMY_CONTACT_EMAIL`, `IMY_PUBLIC_URL`, the email settings below, `IMY_BACKUP_DAILY=1`,
`TZ=Asia/Manila`, and leave `IMY_SECURE_COOKIES` unset.
Use a **fresh database** on the server — don't copy your own `data/` folder up, it has your test accounts and chats in it.

**Email, for "Forgot password".** Without it, anyone who forgets their password is locked out for good.
imy sends through any email service that offers SMTP:

| Setting | Gmail (fine to start) | Any other service |
|---|---|---|
| `IMY_SMTP_HOST` | `smtp.gmail.com` | the SMTP host it gives you |
| `IMY_SMTP_PORT` | `587` | usually `587` |
| `IMY_SMTP_USER` | the Gmail address | the SMTP login it gives you |
| `IMY_SMTP_PASSWORD` | an **App Password** (Google Account → Security → 2-Step Verification must be on → App passwords) | the SMTP key it gives you |
| `IMY_MAIL_FROM` | `imy <that Gmail address>` | an address on a domain you've verified with it |
| `IMY_PUBLIC_URL` | `https://your.address` | same |

Make a separate Gmail account for imy rather than using your own: its address is what people see, and an App
Password can send as that account. A personal Gmail can send to about 500 people a day, which is plenty for
password resets at the start; when you outgrow it, a sending service (Brevo, Resend, Amazon SES and others)
with your own domain lands in fewer spam folders. Then send yourself a test:

```bash
python -m backend.mailer you@example.com
```

On your own computer you don't need any of this: with no email set up, the link is printed in the Terminal
window running imy, and the page says so.

**2. Run the checks.** On the server (most hosts have a "shell" or "console" button):

```bash
python -m backend.preflight                                # settings, database, backups, disk, build
python -m backend.preflight --url https://your.address     # and the live site, from outside
```

It checks everything a computer can: email for "forgot password" (and, live, that the site can send it),
the contact address on both pages, HTTPS and the http→https redirect,
that signed-out requests for people and files are refused, the secure cookie setting, daily backups and that
the newest one opens, free disk, test-looking accounts, the daily AI limit, Gemini's free tier, and that
`.env` was never committed. It changes nothing, and exits with an error if anything must be fixed.

**3. What only you can do:**
- **Rotate any API key** that has ever been pasted into a chat, a screenshot or a shared file: make a new one at
  console.groq.com/keys, put it in the host's settings, delete the old one.
- **Turn on Zero Data Retention** at Groq (Settings → Data Controls).
- **Turn on your host's disk snapshots**, and download a backup somewhere else once a week.
- **Uptime monitoring:** point a free monitor (UptimeRobot, Better Stack…) at `https://your.address/api/health`
  with an email alert.
- **Groq's limits and billing:** check your plan's daily limits before launch day; switch to the paid plan before
  you market to a crowd.
- **The law:** imy stores personal chats. In the Philippines that's the Data Privacy Act of 2012; the National
  Privacy Commission requires registration once you process sensitive personal information of 1,000 or more
  people. Chats can contain exactly that, so check with the NPC or a lawyer before you charge money or pass a
  thousand users. The Terms in `terms.html` are a plain-language starting point, not legal advice.

### Running it

- **Who can reach people's data:** whoever can open the host's dashboard or shell, and whoever has the backups.
  Keep that to you; use two-factor sign-in on the host, GitHub and Groq.
- **Deletion requests by email:** `python -m backend.admin remove their@email` shows what would go,
  add `--yes` to delete it (the same as "Delete everything" in the app). Make a backup first.
- **Launching:** [deploy/LAUNCH.md](deploy/LAUNCH.md) is the tick-off list for the things only you
  can do — the Gmail App Password, the Groq key, testing on a real phone, the link preview, uptime
  alerts, backups and the Data Privacy Act.
- **Keep a copy of the backups off the server:** `bash deploy/get-backup.sh ubuntu@SERVER_IP key.key`
  makes a fresh backup on the server, downloads it to `~/imy-backups` and checks it opens. The
  server's own daily backups sit on the same disk, so they don't save you from losing the server.
  Once a week is plenty; put the downloaded zip in an iCloud or Google Drive folder too.
- **The admin page:** open `/admin` (e.g. http://localhost:8000/admin) and type an admin email
  (`IMY_ADMIN_EMAILS`, or `IMY_CONTACT_EMAIL` if that's not set). imy emails a one-time sign-in link; on
  your own computer it's also printed in the Terminal. The page shows who's using imy right now, active
  accounts, sign-ups and homepage visitors for the last 30 days, every account (counts only, never chats)
  and the feedback inbox. Clients send feedback with "Send feedback" in the app's sidebar.
- **Accounts and usage:** `python -m backend.admin accounts`, `python -m backend.admin usage`.
- **A key leaked:** make a new key, set it on the host, restart imy, delete the old key. Nothing else needs to change.
- **After changing the Terms:** bump `TERMS_VERSION` in `backend/auth.py` so new sign-ups record the new version.

## Known limits
- **Without a key, replies are pattern-based**: they sound like the person but can be generic when nothing fits. With a key they understand context.
- **Photos need a key to be understood.** With one, imy asks a free model that can see images what the photo shows and
  replies about it. Without one it falls back to reading any words in the picture, then to "a photo" — the reply still
  arrives, it just can't be specific. Which model can see changes over time; imy asks Groq for its current list and
  only falls back to the names in `llm.py` if that fails.
- **OCR can't read emojis** and can misread stylised fonts, so use a chat export file for emoji-heavy chats.
- **Speaker detection is heuristic** (bubble side and colour, names above messages). Unusual themes may need corrections in Review.
- The page loads two Google Fonts from the internet, and the optional sentence-transformers model is downloaded once from Hugging Face.

## License

Copyright © 2026 Kristina Diane Aviles / PDYN Technologies. All rights reserved — see
[LICENSE](LICENSE). The code is here to be read, not reused; ask first at pdyntechnology@gmail.com.
