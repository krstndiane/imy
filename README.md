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
backend/   main.py (API)  db.py (SQLite)  ocr.py + parser.py + looks.py (screenshots)  importers.py (chat files)
           engine.py + smalltalk.py + personality.py + style.py (replies)  llm.py (free AI)  questions.json  tests/
frontend/  src/App.jsx  src/components/{Sidebar,Trainer,TrainPanel,Chat,ErrorBoundary}.jsx  src/platforms.js  src/styles.css  dist/ (built)
data/      created on first run: imy.db + uploaded screenshots
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
- **Accounts.** Right now everyone who opens the site sees every person set up in it, which is fine on your own laptop and
  not fine on the internet. Add sign-in (each user sees only their own people) before sharing the link.
- **HTTPS** (most hosts give you this) and **backups** of `/data`.
- **A privacy page**: what's stored, that replies may be written by Groq (or Gemini), and how to delete everything (Delete in the chat menu).
- **Ages:** the AI providers' terms are for adults; make imy 18+.

## Known limits
- **Without a key, replies are pattern-based**: they sound like the person but can be generic when nothing fits. With a key they understand context.
- **OCR can't read emojis** and can misread stylised fonts, so use a chat export file for emoji-heavy chats.
- **Speaker detection is heuristic** (bubble side and colour, names above messages). Unusual themes may need corrections in Review.
- The page loads two Google Fonts from the internet, and the optional sentence-transformers model is downloaded once from Hugging Face.
