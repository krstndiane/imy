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
- **Changing your password** signs out every other browser.
- **Sign-in attempts** are rate limited, and a wrong email and a wrong password give the same answer
  (and take the same time), so nobody can use the form to find out which emails have accounts.
- **"Delete everything"** (account menu) removes the account, every person, every message and every
  uploaded file. It asks for the password and the typed word DELETE, and there's no undo.
- **The privacy page** is `frontend/public/privacy.html`, served at `/privacy.html` and linked from
  the sign-in screen, the account menu and the landing page. **Put your contact email in it before
  you publish** — it ships with a placeholder.

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
   site*. At a thousand users that's gone in minutes. Per-account limits and a paid tier are the
   work to do before charging anyone.
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
```

Each backup is a single zip: the database plus every uploaded file. The database is copied with
SQLite's own online backup, so it's safe to run while imy is serving people. The newest 7 are kept
(`--keep N`, or `IMY_BACKUP_KEEP`). Set `IMY_BACKUP_DAILY=1` and imy makes one every 24 hours by
itself.

**To restore:** stop imy, unzip the backup over your `data/` folder so `imy.db` and `uploads/` land
back where they were, start imy. Try this once before you need it.

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
the AI when its own data isn't enough, rests a model for a minute when its limit is hit, and falls back to its own replies.
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
backend/   main.py (API)  db.py (SQLite)  auth.py (accounts + sign-in)  backup.py
           ocr.py + parser.py + looks.py (screenshots)  importers.py (chat files)
           engine.py + smalltalk.py + personality.py + style.py (replies)  llm.py (free AI)
           media.py (photos shared in a chat + describing them)  notes.py (what they remember)  questions.json  tests/
frontend/  src/App.jsx  src/platforms.js  src/styles.css  dist/ (built)  public/privacy.html
           src/components/{Auth,Account,Sidebar,Trainer,TrainPanel,Chat,ChatPanels,Avatar,Icon,ErrorBoundary}.jsx
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
point its health check at `/api/health`. The container uses the lighter TF-IDF matcher, so ~512 MB of memory is enough.

Built to stay up: short database locks (uploads and chats never block each other), one bad screenshot doesn't fail a batch,
a failed reply falls back to a simpler one, every error comes back as a friendly message, a broken screen shows "Try again"
instead of a blank page, and the website reconnects by itself if the server restarts. There's a per-visitor speed limit too.

### Before you open it to the public
- **Your contact email** in `frontend/public/privacy.html` (it ships with a placeholder), then rebuild the frontend.
- **HTTPS** — most hosts give you this. Set `IMY_ORIGINS` to your `https://` address so the sign-in cookie
  is marked `Secure`; `IMY_SECURE_COOKIES=1` forces it either way.
- **Backups**: set `IMY_BACKUP_DAILY=1`, and turn on your host's own snapshots of the `/data` disk.
- **Paying for the AI.** The free Groq limits are shared by *everyone* using your site, not per person — a
  few hundred replies a day in total. Count usage per account before you charge anyone.
- **A privacy page**: what's stored (messages, profile pictures and every photo shared in a chat), that replies may be
  written by Groq (or Gemini), that a shared photo is sent to them to be described, and how to delete everything
  (Delete in the chat menu).
- **Ages:** the AI providers' terms are for adults; make imy 18+.

## Known limits
- **Without a key, replies are pattern-based**: they sound like the person but can be generic when nothing fits. With a key they understand context.
- **Photos need a key to be understood.** With one, imy asks a free model that can see images what the photo shows and
  replies about it. Without one it falls back to reading any words in the picture, then to "a photo" — the reply still
  arrives, it just can't be specific. Which model can see changes over time; imy asks Groq for its current list and
  only falls back to the names in `llm.py` if that fails.
- **OCR can't read emojis** and can misread stylised fonts, so use a chat export file for emoji-heavy chats.
- **Speaker detection is heuristic** (bubble side and colour, names above messages). Unusual themes may need corrections in Review.
- The page loads two Google Fonts from the internet, and the optional sentence-transformers model is downloaded once from Hugging Face.
