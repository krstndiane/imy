import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { PLATFORMS } from "../platforms";
import ThemeSwitch from "./ThemeSwitch";

/* Trainer: Training Mode in five steps.
     Where you talk    -> pick the app you chat in; the chat screen will look like it
     Basics            -> 4 quick questions, one per screen (name, nicknames, who they are)
     Chats             -> import a chat export, upload screenshots or paste text
     Review            -> check who-said-what and fix any mistakes
     Get to know them  -> one card at a time: things imy noticed (tap to confirm), this-or-that taps,
                          "how would they reply?", little details, one big optional question.
   Questions come from backend/questions.json, so you can change them without touching code. */
const STEPS = ["Where you talk", "Basics", "Chats", "Review", "Get to know them"];
const GOAL = 300;   // of their messages: enough for imy to pick up how they write

export const allQuestions = (questions) =>
  questions ? questions.sections.flatMap((s) => s.questions.map((q) => ({ ...q, stage: s.stage, section: s.title }))) : [];
export const filled = (answers, id) => ((answers[id] || "") + "").trim() !== "";

/* "imy knows them X%": basics 20%, real chat messages 40% (300 of theirs = full), the rest of the interview 40% */
export function knowPercent(questions, answers, personMsgs) {
  const all = allQuestions(questions);
  if (!all.length) return 0;
  const share = (qs) => (qs.length ? qs.filter((q) => filled(answers, q.id)).length / qs.length : 0);
  const basics = share(all.filter((q) => q.stage === "basics"));
  const deep = share(all.filter((q) => q.stage === "deep"));
  return Math.round(100 * (0.2 * basics + 0.4 * Math.min(1, personMsgs / 300) + 0.4 * deep));
}

export default function Trainer({ personId, onDone, onCancel, onSaved, theme, onTheme }) {
  const [step, setStep] = useState(personId ? 1 : 0);
  const [questions, setQuestions] = useState(null);
  const [answers, setAnswers] = useState({});
  const [platform, setPlatform] = useState(null);   // null = not chosen yet
  const [pid, setPid] = useState(personId);
  const [shots, setShots] = useState([]);
  const [personMsgs, setPersonMsgs] = useState(0);
  const [guesses, setGuesses] = useState([]);
  const [flagged, setFlagged] = useState(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [consent, setConsent] = useState(!!personId);   // asked once, when someone new is added

  useEffect(() => {
    api.questions().then(setQuestions).catch((e) => setError(e.message));
    if (personId) {
      api.person(personId).then((p) => { setAnswers(p.answers || {}); setPlatform(p.platform || ""); setPersonMsgs(p.person_message_count); });
      api.screenshots(personId).then(setShots);
    }
  }, [personId]);

  useEffect(() => {   // what imy noticed in the chats, shown first in "Get to know them"
    if (step === 4 && pid) api.guesses(pid).then(setGuesses).catch(() => setGuesses([]));
  }, [step, pid]);

  const personName = (answers.q2 || answers.q1 || "them").split(" ")[0];
  const pct = knowPercent(questions, answers, personMsgs);

  async function saveAnswers(next = answers) {
    setBusy(true); setError("");
    try {
      const saved = pid ? await api.updatePerson(pid, next, platform ?? "") : await api.createPerson(next, platform ?? "", consent);
      setPid(saved.id);
      setPersonMsgs(saved.person_message_count);
      onSaved?.();   // refresh the sidebar list
      return saved.id;
    } catch (e) { setError(e.message); return null; }
    finally { setBusy(false); }
  }

  async function pickPlatform() {
    if (pid && !(await saveAnswers())) return;   // editing: save the new look straight away
    setStep(1);
  }

  const reload = async () => {
    setShots(await api.screenshots(pid));
    const p = await api.person(pid);
    setPersonMsgs(p.person_message_count);
  };
  const addShots = (list) => {
    setShots((s) => [...s, ...list]);
    setFlagged((f) => new Set([...f, ...list.filter((x) => x.needs_review).map((x) => x.id)]));
  };

  const all = allQuestions(questions);
  const basicsCards = all.filter((q) => q.stage === "basics");
  const deepCards = [
    ...guesses.map((g) => ({ ...g, id: "guess_" + g.id, type: "guess", section: "imy noticed…" })),
    ...all.filter((q) => q.stage === "deep"),
  ];

  return (
    <div className="trainer">
      <header className="page-head">
        <div>
          <h1>{personId ? `Edit ${personName}` : "Create a new person"}</h1>
          {questions && (pid || filled(answers, "q1")) ? (
            <div className="meter" aria-label={`imy knows them ${pct}%`}>
              <span>imy knows {personName === "them" ? "them" : personName} <strong>{pct}%</strong></span>
              <div className="bar"><div style={{ width: pct + "%" }} /></div>
            </div>
          ) : <p className="muted">The more you add, the closer the replies get.</p>}
        </div>
        <button className="btn ghost" onClick={onCancel}>Cancel</button>
      </header>

      <ol className="steps">
        {STEPS.map((label, i) => (
          <li key={label}>
            <button className={"step" + (i === step ? " current" : "") + (i < step ? " done" : "")}
              disabled={(i > 1 && !pid) || (i === 1 && platform === null)} onClick={() => setStep(i)}>
              <span className="step-n">{i + 1}</span>{label}
            </button>
          </li>
        ))}
      </ol>

      {error && <div className="banner error">{error}</div>}

      {step === 0 && (
        <PlatformPicker value={platform} onChange={setPlatform} theme={theme} onTheme={onTheme}
          consent={pid ? null : { value: consent, onChange: setConsent }}
          footer={<button className="btn primary" disabled={busy || platform === null || !consent} onClick={pickPlatform}>Continue</button>} />
      )}
      {step === 1 && (
        <Deck key="basics" cards={basicsCards} answers={answers} setAnswers={setAnswers} personName={personName}
          onSave={pid ? saveAnswers : null} busy={busy} finishLabel={busy ? "Saving…" : "Save and add chats"}
          onBackFirst={() => setStep(0)}
          onFinish={async (a) => { if (await saveAnswers(a)) setStep(2); }} />
      )}
      {step === 2 && (
        <Upload pid={pid} shots={shots} onAdded={addShots} onImported={reload} personName={personName}
          footer={<>
            <button className="btn ghost" onClick={() => setStep(1)}>Back</button>
            <button className="btn primary" onClick={() => setStep(3)}>Review what was read</button>
          </>} />
      )}
      {step === 3 && (
        <Review shots={shots} flagged={flagged} personName={personName} reload={reload}
          footer={<>
            <button className="btn ghost" onClick={() => setStep(2)}>Back</button>
            <button className="btn primary" onClick={() => setStep(4)}>Next: get to know them</button>
          </>} />
      )}
      {step === 4 && questions && (
        <Deck key={"deep" + guesses.length} cards={deepCards} answers={answers} setAnswers={setAnswers} personName={personName}
          onSave={saveAnswers} busy={busy} finishLabel="Done" onBackFirst={() => setStep(3)}
          onLater={async () => { if (await saveAnswers()) onDone(pid); }}
          onFinish={async (a) => { if (await saveAnswers(a)) onDone(pid); }} />
      )}
    </div>
  );
}

/* ---------- Step 1: where do you two talk? ---------- */
function PlatformPicker({ value, onChange, theme, onTheme, footer, consent }) {
  return (
    <section className="panel">
      <div className="pick-head">
        <div>
          <h2>Where do you two usually talk?</h2>
          <p className="muted">Your chat with them will look like that app, in light or dark. You can change both later.</p>
        </div>
        <ThemeSwitch theme={theme} onChange={onTheme} />
      </div>
      <div className="plats" role="radiogroup" aria-label="Messaging app">
        {PLATFORMS.map((p) => (
          <button key={p.id || "other"} type="button" role="radio" aria-checked={value === p.id}
            className={"plat skin-" + (p.id || "imy") + (value === p.id ? " picked" : "")} onClick={() => onChange(p.id)}>
            <span className="plat-preview" aria-hidden="true">
              <span className="bubble person">hey</span>
              <span className="bubble user">hii</span>
            </span>
            <span className="plat-name">{p.label}</span>
          </button>
        ))}
      </div>
      {consent && (
        <label className="check consent">
          <input type="checkbox" checked={consent.value} onChange={(e) => consent.onChange(e.target.checked)} />
          <span>
            These are conversations I was part of. I'll use imy only for myself, and never to pretend to be
            this person to anyone else.{" "}
            <a href="/terms.html" target="_blank" rel="noreferrer">Terms</a>
          </span>
        </label>
      )}
      <div className="actions">{footer}</div>
    </section>
  );
}

/* ---------- The interview: one question per screen ----------
   card types: short | long | choice | reply | guess (from /api/persons/:id/guesses)
   Each answer is saved as you go (onSave), so "Finish later" never loses anything. */
export function Deck({ cards, answers, setAnswers, personName, onSave, onFinish, onLater, onBackFirst, finishLabel, busy }) {
  const [i, setI] = useState(() => {   // reopening: start at the first card not answered yet
    const k = cards.findIndex((c) => c.type === "guess" || !filled(answers, c.id));
    return k > 0 ? k : 0;
  });
  const card = cards[Math.min(i, cards.length - 1)];
  const input = useRef();
  const last = i >= cards.length - 1;
  const name = personName === "them" ? "they" : personName;

  useEffect(() => { input.current?.focus(); }, [i]);
  if (!card) return null;

  const value = answers[card.id] || "";
  const canGo = !card.required || filled(answers, card.id);

  async function go(next = answers) {
    if (last) return onFinish(next);
    if (onSave) onSave(next);   // fire and forget; the meter updates when it comes back
    setI(i + 1);
  }
  const set = (id, v) => { const next = { ...answers, [id]: v }; setAnswers(next); return next; };
  const pick = (opt) => { const next = set(card.id, opt); setTimeout(() => go(next), 220); };
  const confirmGuess = (yes) => {
    if (!yes) return go();
    const old = (answers[card.field] || "").trim();
    const v = card.mode === "add" && old ? `${old}, ${card.value}` : card.value;
    go(set(card.field, v));
  };
  const enter = (e) => {
    if (e.key === "Enter" && !e.shiftKey && card.type !== "long") { e.preventDefault(); if (canGo) go(); }
  };

  return (
    <section className="panel deck">
      <div className="deck-top">
        <span className="tag">{card.section}</span>
        <span className="muted small">{i + 1} of {cards.length}</span>
      </div>
      <div className="bar thin"><div style={{ width: ((i + 1) / cards.length) * 100 + "%" }} /></div>

      <div className="card-in" key={card.id}>
        {card.type === "reply" && (
          <div className="mine-row"><span className="mine">{card.prompt}</span><span className="muted small">you</span></div>
        )}
        <div className="ask">
          <span className="ask-avatar" aria-hidden="true">imy</span>
          <div>
            <p className="ask-text" id="ask-text">
              {card.type === "reply" ? `What would ${name} reply?` : card.type === "guess" ? `${card.text}. Right?` : card.text}
            </p>
            {card.hint && <p className="hint">{card.hint}</p>}
          </div>
        </div>

        {card.type === "guess" && (
          <div className="chips">
            <button className="chip" onClick={() => confirmGuess(true)}>Yep 👍</button>
            <button className="chip" onClick={() => confirmGuess(false)}>Not really</button>
          </div>
        )}
        {card.type === "choice" && (
          <>
            <div className="chips" role="radiogroup" aria-labelledby="ask-text">
              {card.options.map((o) => (
                <button key={o} role="radio" aria-checked={value === o} className={"chip" + (value === o ? " on" : "")} onClick={() => pick(o)}>{o}</button>
              ))}
            </div>
            {card.other && (
              <input className="answer" aria-labelledby="ask-text" ref={input} placeholder="Something else…" onKeyDown={enter}
                value={card.options.includes(value) ? "" : value} onChange={(e) => set(card.id, e.target.value)} />
            )}
          </>
        )}
        {card.type === "short" && (
          <input className="answer" aria-labelledby="ask-text" ref={input} value={value} placeholder={card.placeholder || "Type your answer"}
            onChange={(e) => set(card.id, e.target.value)} onKeyDown={enter} />
        )}
        {card.type === "reply" && (
          <>
            <textarea className="answer" aria-labelledby="ask-text" ref={input} rows={2} value={value} placeholder={`Type it the way ${name} would`}
              onChange={(e) => set(card.id, e.target.value)} onKeyDown={enter} />
            <p className="hint">Shift+Enter puts a second message on a new line. imy will use this when you text something like it.</p>
          </>
        )}
        {card.type === "long" && (
          <textarea className="answer" aria-labelledby="ask-text" ref={input} rows={7} value={value} onChange={(e) => set(card.id, e.target.value)} />
        )}
      </div>

      <div className="actions deck-nav">
        {onLater && <button className="link" disabled={busy} onClick={onLater}>Finish later</button>}
        <span className="grow" />
        <button className="btn ghost" onClick={() => (i === 0 ? onBackFirst?.() : setI(i - 1))}>Back</button>
        {card.type !== "guess" && (
          !filled(answers, card.id) && !card.required && !last
            ? <button className="btn" onClick={() => go()}>Skip</button>
            : <button className="btn primary" disabled={!canGo || busy} onClick={() => go()}>{last ? finishLabel : "Next"}</button>
        )}
      </div>
    </section>
  );
}

/* ---------- Step 3: chats (export import, screenshots, pasted text) ---------- */
export function Upload({ pid, shots, onAdded, onImported, personName, footer }) {
  const [queue, setQueue] = useState([]);
  const [drag, setDrag] = useState(false);
  const [pasted, setPasted] = useState("");
  const [pasteName, setPasteName] = useState("");
  const [pasteMsg, setPasteMsg] = useState("");
  const input = useRef();
  // progress is measured in THEIR messages: one uploaded conversation usually covers it, screenshots add a few each
  const theirs = shots.reduce((n, s) => n + s.messages.filter((m) => m.speaker === "person").length, 0);

  // No limit on how many screenshots: they join one queue and are read one at a time (OCR is slow),
  // so you can keep dropping more while earlier ones are still being read.
  const pending = useRef([]);
  const running = useRef(false);

  function handleFiles(fileList) {
    const files = [...fileList].filter((f) => f.type.startsWith("image/") || /\.(png|jpe?g|webp|gif|bmp)$/i.test(f.name));
    const items = files.map((f) => ({ key: `${Date.now()}-${Math.random()}`, name: f.name, status: "waiting", file: f }));
    setQueue((q) => [...q, ...items.map(({ file, ...rest }) => rest)]);
    pending.current.push(...items);
    if (!running.current) work();
  }

  async function work() {
    running.current = true;
    while (pending.current.length) {
      const it = pending.current.shift();
      const update = (patch) => setQueue((q) => q.map((x) => (x.key === it.key ? { ...x, ...patch } : x)));
      update({ status: "reading" });
      try {
        const [shot] = await api.upload(pid, [it.file]);
        if (!shot || shot.error) throw new Error(shot?.error || "Couldn't read this screenshot.");
        if (!shot.duplicate) onAdded([shot]);
        const m = shot.meta || {};
        update({ status: "done", count: (shot.messages || []).length, duplicate: shot.duplicate, skipped: shot.overlap_skipped,
                 app: m.platform && m.platform !== "unknown" ? (PLATFORMS.find((p) => p.id === m.platform)?.label || m.platform) : "",
                 mode: m.mode, title: m.title, warning: shot.warning });
      } catch (e) {
        update({ status: "error", msg: e.message });
      }
    }
    running.current = false;
  }
  const finished = queue.filter((q) => q.status === "done" || q.status === "error").length;

  async function importText() {
    setPasteMsg("");
    try {
      const shot = await api.importText(pid, pasted, pasteName);
      onAdded([shot]);
      setPasted("");
      setPasteMsg(`Imported ${shot.messages.length} messages.`);
    } catch (e) { setPasteMsg(e.message); }
  }

  return (
    <section className="panel">
      <div className="count">
        <strong>{theirs}</strong> of {personName === "them" ? "their" : `${personName}’s`} messages so far
        <span className="muted"> · {theirs >= GOAL ? "that’s plenty, and more still helps" : "no limit: the more you add, the closer the replies"}</span>
        <div className="bar"><div style={{ width: Math.min(100, (theirs / GOAL) * 100) + "%" }} /></div>
      </div>

      <ExportImport pid={pid} personName={personName} onImported={onImported} />

      <h3 className="sub-head">No file? Use screenshots</h3>
      <p className="muted small">For chats that aren’t in the download, like newer encrypted Messenger chats or Viber, or a few favorite moments.</p>
      <div className={"drop" + (drag ? " over" : "")}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); handleFiles(e.dataTransfer.files); }}>
        <p><strong>Drop as many screenshots as you want</strong></p>
        <p className="muted small">PNG, JPG or WEBP from any chat app, as many as you like. Repeats and overlapping screenshots are skipped automatically.</p>
        <button className="btn" onClick={() => input.current.click()}>Choose screenshots</button>
        <input ref={input} type="file" accept="image/*" multiple hidden onChange={(e) => { handleFiles(e.target.files); e.target.value = ""; }} />
      </div>

      {queue.length > 0 && (
        <p className="small queue-sum" role="status">
          {finished < queue.length
            ? `Reading ${Math.min(finished + 1, queue.length)} of ${queue.length}… you can keep adding more.`
            : `Done: ${queue.length} screenshot${queue.length === 1 ? "" : "s"} read.`}
        </p>
      )}
      {queue.length > 0 && (
        <ul className="queue">
          {queue.map((q) => (
            <li key={q.key} className={q.status}>
              <span>{q.name}</span>
              <span>
                {q.status === "waiting" && "Waiting"}
                {q.status === "reading" && "Reading text…"}
                {q.status === "done" && (q.duplicate ? "Already added" :
                  `${q.count} message${q.count === 1 ? "" : "s"} found` + (q.skipped ? ` · ${q.skipped} already in another screenshot` : "")
                  + (q.app ? ` · ${q.app}${q.mode ? `, ${q.mode}` : ""}` : ""))}
                {q.status === "done" && q.warning && <em className="queue-warn">{q.warning}</em>}
                {q.status === "error" && q.msg}
              </span>
            </li>
          ))}
        </ul>
      )}

      <details className="paste">
        <summary>Or paste chat text instead</summary>
        <p className="muted small">
          Text is more accurate than OCR and keeps emojis, which screenshots can’t capture. Use one message per line like
          <code> Name: message</code>, or paste a WhatsApp export.
        </p>
        <label className="field">
          <span className="q">Their name as it appears in the chat</span>
          <input value={pasteName} onChange={(e) => setPasteName(e.target.value)} placeholder={personName} />
        </label>
        <textarea rows={7} aria-label="Pasted chat text" value={pasted} onChange={(e) => setPasted(e.target.value)} placeholder={"Mia: where are you\nMe: omw!"} />
        <div className="actions">
          <button className="btn" disabled={!pasted.trim()} onClick={importText}>Import text</button>
          {pasteMsg && <span className="muted small">{pasteMsg}</span>}
        </div>
      </details>

      <div className="actions">{footer}</div>
    </section>
  );
}

/* ---------- Chat export import (WhatsApp / Messenger / Instagram / Telegram) ----------
   1. you pick the file you downloaded from the app
   2. the server lists the chats inside it; you pick one
   3. you say which sender is them and which is you, then Import */
const HOW_TO = [
  { app: "WhatsApp", steps: ["Open the chat with them", "Tap their name (iPhone) or ⋮ > More (Android)", "Export chat > Without media", "Save or AirDrop the file to this Mac, then choose it here (.zip or .txt)"] },
  { app: "Messenger", steps: ["Go to facebook.com/dyi > Download or transfer information", "Specific types of information > Messages", "Download to device, Format: JSON (HTML also works), Date range: All time", "When Facebook says it's ready, download the .zip and choose it here",
      "Newer chats are end-to-end encrypted and aren't in that download. On messenger.com: profile photo > Privacy & safety > End-to-end encrypted chats > Message storage > Download secure storage data (screenshots work for those if this file isn't read)"] },
  { app: "Instagram", steps: ["Accounts Center > Your information and permissions > Download your information", "Messages only, Format: JSON", "Download the .zip and choose it here"] },
  { app: "Telegram", steps: ["Telegram Desktop app > open the chat > ⋮ > Export chat history", "Untick photos/videos (HTML or JSON both work)", "Choose result.json or messages.html here"] },
  { app: "LINE", steps: ["Open the chat > ≡ > Settings > Export chat history", "Save the .txt and choose it here"] },
  { app: "Viber", steps: ["Viber can't export one chat as text, so use screenshots for Viber (below)"] },
];

function ExportImport({ pid, personName, onImported }) {
  const [busy, setBusy] = useState("");          // "", "reading", "saving"
  const [found, setFound] = useState(null);      // server reply: { token, conversations }
  const [chatId, setChatId] = useState(null);
  const [them, setThem] = useState("");
  const [me, setMe] = useState("");
  const [filter, setFilter] = useState("");
  const [msg, setMsg] = useState("");
  const input = useRef();

  const chat = found?.conversations.find((c) => c.id === chatId);
  const pick = (c) => { setChatId(c.id); setThem(c.guess_person || ""); setMe(c.guess_me || ""); };

  async function read(file) {
    if (!file) return;
    setBusy("reading"); setMsg(""); setFound(null); setChatId(null);
    try {
      const res = await api.importFile(pid, file);
      setFound(res);
      // The chat with this person is listed first (matched by name): pick it, so usually it's one click.
      const best = res.conversations[0];
      if (res.conversations.length === 1 || best?.match) pick(best);
    } catch (e) { setMsg(e.message); }
    finally { setBusy(""); }
  }

  async function save() {
    setBusy("saving"); setMsg("");
    try {
      const r = await api.importCommit(pid, { token: found.token, conversation_id: chatId, person_name: them, me_name: me });
      setMsg(`Added ${r.person_messages} messages from ${personName} and ${r.user_messages} from you, as ${r.sessions} separate talk${r.sessions === 1 ? "" : "s"}.`);
      setFound(null); setChatId(null);
      onImported?.();
    } catch (e) { setMsg(e.message); }
    finally { setBusy(""); }
  }

  const day = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString() : "");
  const list = (found?.conversations || []).filter((c) => c.title.toLowerCase().includes(filter.toLowerCase()));

  return (
    <div className="export">
      <h3>Upload your conversation <span className="tag">best</span></h3>
      <p className="muted small">
        Download your chat with them from the app, then upload the file here. imy gets every message exactly as
        written, emojis included, and you pick which conversation to use. It stays on this computer.
      </p>
      <details className="how">
        <summary>How do I download my conversation?</summary>
        {HOW_TO.map((h) => (
          <div key={h.app}><strong className="small">{h.app}</strong><ol>{h.steps.map((s) => <li key={s}>{s}</li>)}</ol></div>
        ))}
      </details>

      <button className="btn" disabled={!!busy} onClick={() => input.current.click()}>
        {busy === "reading" ? "Reading the file…" : "Choose the file"}
      </button>
      <input ref={input} type="file" accept=".zip,.json,.txt,.html,.htm" hidden onChange={(e) => { read(e.target.files[0]); e.target.value = ""; }} />
      {msg && <p className="small" role="status" style={{ marginTop: 10 }}>{msg}</p>}

      {found && found.conversations.length > 1 && (
        <>
          <p className="small" style={{ marginTop: 14 }}><strong>{found.conversations.length} chats</strong> in this file. Which one is with {personName}?</p>
          {found.conversations.length > 8 && <input aria-label="Search by name" placeholder="Search by name" value={filter} onChange={(e) => setFilter(e.target.value)} />}
          <ul className="chat-list">
            {list.map((c) => (
              <li key={c.id}>
                <button className={c.id === chatId ? "picked" : ""} onClick={() => pick(c)}>
                  <span>{c.title}{c.match && <span className="tag">looks like {personName}</span>}</span>
                  <span className="muted small">{c.message_count} messages{c.last ? ` · until ${day(c.last)}` : ""}</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      {chat && (
        <>
          <p className="small" style={{ marginTop: 14 }}>
            <strong>{chat.title}</strong>: {chat.message_count} messages{chat.first ? `, ${day(chat.first)} to ${day(chat.last)}` : ""}.
            {chat.participants.length > 2 && " This is a group chat, so only the two people you pick below are used."}
          </p>
          <div className="who-pick">
            <label className="field">
              <span className="q">Which one is {personName}?</span>
              <select value={them} onChange={(e) => setThem(e.target.value)}>
                <option value="">Choose…</option>
                {chat.participants.map((p) => <option key={p.name} value={p.name}>{p.name} ({p.count})</option>)}
              </select>
            </label>
            <label className="field">
              <span className="q">Which one is you?</span>
              <select value={me} onChange={(e) => setMe(e.target.value)}>
                <option value="">Choose…</option>
                {chat.participants.map((p) => <option key={p.name} value={p.name}>{p.name} ({p.count})</option>)}
              </select>
            </label>
          </div>
          <div className="actions" style={{ marginTop: 4 }}>
            <button className="btn primary" disabled={!!busy || !them || !me || them === me} onClick={save}>
              {busy === "saving" ? "Uploading…" : "Use this conversation"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

/* ---------- Step 4: review and fix ---------- */
const COLLAPSE_OVER = 12;   // imported talks longer than this start folded, so the page stays fast

export function Review({ shots, flagged, personName, reload, footer }) {
  const total = shots.reduce((n, s) => n + s.messages.length, 0);
  const act = (fn) => async (...args) => { await fn(...args); await reload(); };
  const [open, setOpen] = useState(new Set());
  const [redo, setRedo] = useState(null);      // reading every screenshot again: { done, total, failed }
  const folded = (s) => !s.url && s.messages.length > COLLAPSE_OVER && !open.has(s.id);
  const images = shots.filter((s) => s.url);

  async function rereadAll() {
    if (!confirm(`Read all ${images.length} screenshots again with imy's newest reader? Edits you made to their messages will be replaced.`)) return;
    let failed = 0;
    setRedo({ done: 0, total: images.length, failed });
    for (let i = 0; i < images.length; i++) {
      try { await api.reread(images[i].id); } catch { failed++; }
      setRedo({ done: i + 1, total: images.length, failed });
    }
    await reload();
  }

  return (
    <section className="panel">
      <p className="muted">
        {total} messages read from {shots.length} item{shots.length === 1 ? "" : "s"}. Check that each message is attributed to the right person.
        Tap a name to switch speaker, edit text directly, or remove anything that isn’t a message.
      </p>
      {images.length > 1 && (
        <p className="small">
          {redo && redo.done < redo.total ? `Reading again… ${redo.done} of ${redo.total}`
            : redo ? `Done. ${redo.total - redo.failed} screenshot${redo.total - redo.failed === 1 ? "" : "s"} read again${redo.failed ? `, ${redo.failed} couldn't be read` : ""}.`
            : <>Added screenshots a while ago? <button className="link" onClick={rereadAll}>Read all {images.length} again</button> with the newest reader.</>}
        </p>
      )}
      {shots.length === 0 && <p className="empty">Nothing added yet. Go back and add screenshots or pasted text.</p>}
      {shots.map((s, idx) => (
        <article key={s.id} className="shot">
          <header>
            <strong>{s.filename}</strong>
            <span className="tag">{PLATFORMS.find((p) => p.id === s.platform)?.label || s.platform}</span>
            {s.meta?.title && s.url && <span className="tag">chat: {s.meta.title}</span>}
            {s.meta?.group && <span className="tag">group chat</span>}
            {flagged.has(s.id) && <span className="tag warn">Couldn’t match names, check the speakers</span>}
            <span className="grow" />
            {s.url && <button className="btn small" title="Read this screenshot again with imy's latest reader"
              onClick={act(() => api.reread(s.id))}>Read again</button>}
            <button className="btn small" onClick={act(() => api.flipScreenshot(s.id))}>Swap sides</button>
            <button className="btn small ghost" onClick={act(() => api.deleteScreenshot(s.id))}>Remove</button>
          </header>
          {folded(s) ? (
            <div className="shot-body">
              <button className="link" onClick={() => setOpen(new Set([...open, s.id]))}>Show {s.messages.length} messages</button>
            </div>
          ) : (
          <div className="shot-body">
            {s.url && <img src={s.url} alt={`Screenshot ${idx + 1}`} loading="lazy" />}
            <ul className="msgs">
              {s.messages.length === 0 && <li className="empty">No messages found in this image. Try a sharper or less cropped screenshot.</li>}
              {s.messages.map((m) => (
                <li key={m.id} className={m.speaker}>
                  <button className="who" title="Switch speaker"
                    onClick={act(() => api.editMessage(m.id, { speaker: m.speaker === "user" ? "person" : "user" }))}>
                    {m.speaker === "user" ? "You" : personName}
                  </button>
                  <textarea rows={Math.max(1, Math.ceil(m.text.length / 48))} defaultValue={m.text} key={m.id + m.text}
                    onBlur={(e) => e.target.value !== m.text && act(() => api.editMessage(m.id, { text: e.target.value }))()} />
                  <span className="row-actions">
                    <button className="icon" title="Join with the message above" onClick={act(() => api.mergeUp(m.id))}>↑</button>
                    <button className="icon" title="Delete message" onClick={act(() => api.deleteMessage(m.id))}>×</button>
                  </span>
                </li>
              ))}
            </ul>
          </div>
          )}
        </article>
      ))}
      <div className="actions">{footer}</div>
    </section>
  );
}
