# imy (I Miss You)

Teach an app how someone writes, then chat with that style again.
Everything runs on your own computer. No Claude or other cloud API is used.

**Two modes**
- **Training:** answer 21 questions, upload chat screenshots (or paste text), check what was read, save.
- **Chat:** pick a person and talk. Replies come from their *real* past messages, matched to what you say and shaped by their style.

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
2. **Use full-width, uncropped, high-resolution screenshots.** Light or dark mode is fine.
3. **Always do the Review step.** OCR is never perfect; fix wrong speakers and typos there. This is the biggest accuracy gain.
4. **Import a chat export when you can (best).** In the Screenshots step, use **Import a chat export** with the file the app itself gives you. It's exact (no OCR), keeps emojis, and usually has thousands of messages. Supported, all read by `backend/importers.py`:
   - **WhatsApp:** chat > Export chat > Without media (`.zip` or `.txt`)
   - **Messenger:** facebook.com/dyi > Messages > Format **JSON** (`.zip`, or one `message_1.json`). Newer end-to-end encrypted chats are a separate download (messenger.com > Privacy & safety > End-to-end encrypted chats > Message storage)
   - **Instagram:** Download your information > Messages > **JSON**
   - **Telegram Desktop:** Export chat history > **JSON** (`result.json`)

   The chat is split into separate "talks" wherever there were 6+ hours of silence (`SESSION_GAP_HOURS` in `importers.py`). With a big import, try `IMY_REUSE_LINES=1` so replies can be their real lines.
   Pasting `Name: message` lines also still works.
5. **More back-and-forth is better than more screenshots.** Replies come from exchanges where you said something and they answered.

## How a reply is made (`backend/engine.py`)

1. **Embed** your message into a vector.
2. **Retrieve** the most similar thing *you* said in the screenshots, and reuse what they actually answered.
3. **Intents** (greeting, "miss you", goodnight, thanks...) use their own matching messages, or a small template shaped by their style, when retrieval isn't confident.
4. **Fallback** when nothing fits: after a plain remark, one of their short all-purpose replies ("hahaha", "oo", "true"). After a question, an opinion request, or good/bad news, `backend/personality.py` composes a fitting reply from the interview answers (Q6-Q10, Q14-Q15) and their writing style.
5. **Optional:** tick *Use a local model* in "How they write" and, if [Ollama](https://ollama.com) is running (`ollama pull llama3.2`), a local LLM writes the reply using retrieved examples, the interview answers and the style summary. Change the model with `OLLAMA_MODEL`.

6. **Optional Claude mode** (`backend/claude.py`): put `ANTHROPIC_API_KEY=sk-ant-...` in a `.env` file in the project root (or set it in your shell) and restart. Claude then reads screenshots directly (platform, who said what, emojis included) instead of Tesseract, and writes replies in the person's voice from the interview answers, style summary and full parsed chat history. If a call fails, imy falls back to the steps above. Costs money per use, and **your screenshots, messages and answers are sent to Anthropic**. Set `IMY_CLAUDE=0` to switch it off without removing the key; `IMY_CLAUDE_MODEL` changes the model.

7. **Free AI mode** (`backend/llm.py`): put a free `GEMINI_API_KEY` ([get one](https://aistudio.google.com/apikey)) or `GROQ_API_KEY` ([get one](https://console.groq.com/keys)) in `.env` and restart. An AI model then writes each reply in context, in the person's style. imy picks an available model by itself (`IMY_LLM_MODEL` overrides it; `IMY_LLM=0` switches it off). Screenshots are still read locally. **Your messages, answers and parsed chats are sent to that provider.** If the call fails or the free limit is hit, imy falls back to the offline replies.

Without any key, replies come from `backend/smalltalk.py` (topic rules) and `backend/personality.py`; the screenshots are used only to learn the person's style, never as a script (`IMY_REUSE_LINES=1` brings back the old answer-with-their-own-lines behaviour described in steps 1-4).

Every reply has a **"why this reply?"** link showing which step produced it.

## Where to tweak things

| I want to... | Edit |
|---|---|
| Change the 21 questions | `backend/questions.json` (no code changes needed) |
| Change what the no-match replies say or how they're chosen | `BANKS`, `TRAIT_PATTERNS` in `backend/personality.py` |
| Make matching stricter or looser | `THRESHOLDS` in `backend/engine.py` |
| Add phrases like "goodnight" that get special handling | `INTENTS`, `TEMPLATES` in `backend/engine.py` |
| Change how style is measured | `backend/style.py` |
| Improve who-said-what detection | `backend/parser.py` (`classify_side`, `group_bubbles`) |
| Support another language better | `IMY_MODEL=paraphrase-multilingual-MiniLM-L12-v2`; add Tesseract language packs and set the `lang` in `ocr.py` |
| Re-theme | the variables at the top of `frontend/src/styles.css` |

## Project layout
```
backend/   main.py (API)  db.py (SQLite)  ocr.py  parser.py  style.py  personality.py  engine.py  questions.json  tests/
frontend/  src/App.jsx  src/components/{Sidebar,Trainer,Chat}.jsx  src/styles.css  dist/ (built)
data/      created on first run: imy.db + uploaded screenshots
```

## Known limits
- **Replies are retrieved, not invented.** They sound like the person because they *are* the person's words, but they can be off-topic when nothing in the screenshots is close. The local-LLM option is the way to get more fluent, novel replies.
- **OCR can't read emojis** and can misread stylised fonts, so use pasted text for emoji-heavy chats.
- **Speaker detection is heuristic** (bubble side, bubble colour, names above messages). Group chats and unusual themes may need corrections in the Review step.
- Your chats and screenshots stay on your machine unless you turn on Claude mode (step 6 above). The page loads two Google Fonts from the internet, and the embedding model is downloaded once from Hugging Face.
