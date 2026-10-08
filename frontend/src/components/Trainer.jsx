import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { PLATFORMS } from "../platforms";
import ThemeSwitch from "./ThemeSwitch";

/* Trainer: Training Mode in four steps.
     Where you talk -> pick the app you chat in; the chat screen will look like it
     Interview      -> the 21 questions (rendered from backend/questions.json)
     Screenshots    -> upload images (OCR runs on the server) or paste text
     Review         -> check who-said-what and fix any mistakes, then Done */
const STEPS = ["Where you talk", "Interview", "Screenshots", "Review"];
const RECOMMENDED = 10;

export default function Trainer({ personId, onDone, onCancel, onSaved, theme, onTheme }) {
  const [step, setStep] = useState(personId ? 1 : 0);
  const [questions, setQuestions] = useState(null);
  const [answers, setAnswers] = useState({});
  const [platform, setPlatform] = useState(null);   // null = not chosen yet
  const [pid, setPid] = useState(personId);
  const [shots, setShots] = useState([]);
  const [flagged, setFlagged] = useState(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.questions().then(setQuestions).catch((e) => setError(e.message));
    if (personId) {
      api.person(personId).then((p) => { setAnswers(p.answers || {}); setPlatform(p.platform || ""); });
      api.screenshots(personId).then(setShots);
    }
  }, [personId]);

  const personName = (answers.q2 || answers.q1 || "Them").split(" ")[0];

  async function saveAnswers() {
    setBusy(true); setError("");
    try {
      const saved = pid ? await api.updatePerson(pid, answers, platform ?? "") : await api.createPerson(answers, platform ?? "");
      setPid(saved.id);
      onSaved?.();   // refresh the sidebar list
      return saved.id;
    } catch (e) { setError(e.message); return null; }
    finally { setBusy(false); }
  }

  async function next() {
    const id = await saveAnswers();
    if (id) setStep(2);
  }
  async function pickPlatform() {
    if (pid && !(await saveAnswers())) return;   // editing: save the new look straight away
    setStep(1);
  }

  const reload = async () => setShots(await api.screenshots(pid));
  const addShots = (list) => {
    setShots((s) => [...s, ...list]);
    setFlagged((f) => new Set([...f, ...list.filter((x) => x.needs_review).map((x) => x.id)]));
  };

  return (
    <div className="trainer">
      <header className="page-head">
        <div>
          <h1>{personId ? `Edit ${personName}` : "Create a new person"}</h1>
          <p className="muted">The more you add, the closer the replies get.</p>
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
          footer={<button className="btn primary" disabled={busy || platform === null} onClick={pickPlatform}>Continue</button>} />
      )}
      {step === 1 && (
        <Interview questions={questions} answers={answers} setAnswers={setAnswers}
          footer={<>
            <button className="btn ghost" onClick={() => setStep(0)}>Back</button>
            <button className="btn primary" disabled={busy} onClick={next}>{busy ? "Saving…" : "Save and continue"}</button>
          </>} />
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
            <button className="btn primary" onClick={() => onDone(pid)}>Done</button>
          </>} />
      )}
    </div>
  );
}

/* ---------- Step 1: where do you two talk? ---------- */
function PlatformPicker({ value, onChange, theme, onTheme, footer }) {
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
      <div className="actions">{footer}</div>
    </section>
  );
}

/* ---------- Step 2: the interview ---------- */
function Interview({ questions, answers, setAnswers, footer }) {
  if (!questions) return <p className="muted">Loading questions…</p>;
  let n = 0;
  return (
    <section className="panel">
      <p className="muted">Skip anything you don’t know. Short answers are fine.</p>
      {questions.sections.map((sec) => (
        <fieldset key={sec.title} className="qsection">
          <legend>{sec.title}</legend>
          {sec.questions.map((q) => {
            n += 1;
            return (
              <label key={q.id} className="field">
                <span className="q">{n}. {q.text}</span>
                {q.hint && <span className="hint">{q.hint}</span>}
                {q.type === "long" ? (
                  <textarea rows={8} value={answers[q.id] || ""} onChange={(e) => setAnswers({ ...answers, [q.id]: e.target.value })} />
                ) : (
                  <input value={answers[q.id] || ""} onChange={(e) => setAnswers({ ...answers, [q.id]: e.target.value })} />
                )}
              </label>
            );
          })}
        </fieldset>
      ))}
      <div className="actions">{footer}</div>
    </section>
  );
}

/* ---------- Step 2: screenshots / pasted text ---------- */
function Upload({ pid, shots, onAdded, onImported, personName, footer }) {
  const [queue, setQueue] = useState([]);
  const [drag, setDrag] = useState(false);
  const [pasted, setPasted] = useState("");
  const [pasteName, setPasteName] = useState("");
  const [pasteMsg, setPasteMsg] = useState("");
  const input = useRef();
  const imageCount = shots.filter((s) => s.url).length;
  const exportedCount = shots.filter((s) => !s.url).reduce((n, s) => n + s.messages.length, 0);

  async function handleFiles(fileList) {
    const files = [...fileList].filter((f) => f.type.startsWith("image/"));
    const items = files.map((f) => ({ name: f.name, status: "waiting" }));
    setQueue(items.slice());
    for (let i = 0; i < files.length; i++) {      // one at a time: OCR is slow, this shows progress
      items[i] = { ...items[i], status: "reading" }; setQueue(items.slice());
      try {
        const res = await api.upload(pid, [files[i]]);
        onAdded(res);
        items[i] = { ...items[i], status: "done", count: res[0].messages.length };
      } catch (e) {
        items[i] = { ...items[i], status: "error", msg: e.message };
      }
      setQueue(items.slice());
    }
  }

  async function importText() {
    setPasteMsg("");
    try {
      const shot = await api.importText(pid, pasted, pasteName);
      onAdded([shot]);
      setPasted("");
      setPasteMsg(`Imported ${shot.messages.length} messages.`);
    } catch (e) { setPasteMsg(e.message); }
  }

  const enough = imageCount >= RECOMMENDED || exportedCount >= 200;
  return (
    <section className="panel">
      <ExportImport pid={pid} personName={personName} onImported={onImported} />

      <div className="count">
        <strong>{imageCount}</strong> screenshot{imageCount === 1 ? "" : "s"} added
        {exportedCount > 0 && <> · <strong>{exportedCount}</strong> messages from chat exports</>}
        <span className="muted"> · {enough ? "that’s a good amount" : `aim for at least ${RECOMMENDED} screenshots, or import a chat`}</span>
        <div className="bar"><div style={{ width: enough ? "100%" : Math.min(100, (imageCount / RECOMMENDED) * 100) + "%" }} /></div>
      </div>

      <div className={"drop" + (drag ? " over" : "")}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); handleFiles(e.dataTransfer.files); }}>
        <p><strong>Drop chat screenshots here</strong></p>
        <p className="muted small">PNG, JPG or WEBP from Messenger, WhatsApp, Discord, Viber, Telegram or iMessage. Put your own messages on the right.</p>
        <button className="btn" onClick={() => input.current.click()}>Choose images</button>
        <input ref={input} type="file" accept="image/*" multiple hidden onChange={(e) => { handleFiles(e.target.files); e.target.value = ""; }} />
      </div>

      {queue.length > 0 && (
        <ul className="queue">
          {queue.map((q, i) => (
            <li key={i} className={q.status}>
              <span>{q.name}</span>
              <span>
                {q.status === "waiting" && "Waiting"}
                {q.status === "reading" && "Reading text…"}
                {q.status === "done" && `${q.count} messages found`}
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
        <textarea rows={7} value={pasted} onChange={(e) => setPasted(e.target.value)} placeholder={"Mia: where are you\nMe: omw!"} />
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
  { app: "Messenger", steps: ["Go to facebook.com/dyi > Download or transfer information", "Specific types of information > Messages", "Download to device, Format: JSON, Date range: All time", "When Facebook says it's ready, download the .zip and choose it here",
      "Newer chats are end-to-end encrypted and aren't in that download. On messenger.com: profile photo > Privacy & safety > End-to-end encrypted chats > Message storage > Download secure storage data (screenshots work for those if this file isn't read)"] },
  { app: "Instagram", steps: ["Accounts Center > Your information and permissions > Download your information", "Messages only, Format: JSON", "Download the .zip and choose it here"] },
  { app: "Telegram", steps: ["Telegram Desktop app > open the chat > ⋮ > Export chat history", "Untick photos/videos, Format: Machine-readable JSON", "Choose the result.json file here"] },
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
      if (res.conversations.length === 1) pick(res.conversations[0]);
    } catch (e) { setMsg(e.message); }
    finally { setBusy(""); }
  }

  async function save() {
    setBusy("saving"); setMsg("");
    try {
      const r = await api.importCommit(pid, { token: found.token, conversation_id: chatId, person_name: them, me_name: me });
      setMsg(`Imported ${r.person_messages} messages from ${personName} and ${r.user_messages} from you, as ${r.sessions} separate talk${r.sessions === 1 ? "" : "s"}.`);
      setFound(null); setChatId(null);
      onImported?.();
    } catch (e) { setMsg(e.message); }
    finally { setBusy(""); }
  }

  const day = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString() : "");
  const list = (found?.conversations || []).filter((c) => c.title.toLowerCase().includes(filter.toLowerCase()));

  return (
    <div className="export">
      <h3>Import a chat export <span className="tag">best</span></h3>
      <p className="muted small">
        Download your chat from the app itself and add it here. Exact who-said-what, emojis included, and usually
        thousands of messages instead of a few screenshots. It stays on this computer.
      </p>
      <details className="how">
        <summary>How do I get the file?</summary>
        {HOW_TO.map((h) => (
          <div key={h.app}><strong className="small">{h.app}</strong><ol>{h.steps.map((s) => <li key={s}>{s}</li>)}</ol></div>
        ))}
      </details>

      <button className="btn" disabled={!!busy} onClick={() => input.current.click()}>
        {busy === "reading" ? "Reading the file…" : "Choose export file"}
      </button>
      <input ref={input} type="file" accept=".zip,.json,.txt" hidden onChange={(e) => { read(e.target.files[0]); e.target.value = ""; }} />
      {msg && <p className="small" role="status" style={{ marginTop: 10 }}>{msg}</p>}

      {found && found.conversations.length > 1 && (
        <>
          <p className="small" style={{ marginTop: 14 }}><strong>{found.conversations.length} chats</strong> in this file. Which one is with {personName}?</p>
          {found.conversations.length > 8 && <input placeholder="Search by name" value={filter} onChange={(e) => setFilter(e.target.value)} />}
          <ul className="chat-list">
            {list.map((c) => (
              <li key={c.id}>
                <button className={c.id === chatId ? "picked" : ""} onClick={() => pick(c)}>
                  <span>{c.title}</span>
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
              {busy === "saving" ? "Importing…" : "Import this chat"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

/* ---------- Step 3: review and fix ---------- */
const COLLAPSE_OVER = 12;   // imported talks longer than this start folded, so the page stays fast

function Review({ shots, flagged, personName, reload, footer }) {
  const total = shots.reduce((n, s) => n + s.messages.length, 0);
  const act = (fn) => async (...args) => { await fn(...args); await reload(); };
  const [open, setOpen] = useState(new Set());
  const folded = (s) => !s.url && s.messages.length > COLLAPSE_OVER && !open.has(s.id);

  return (
    <section className="panel">
      <p className="muted">
        {total} messages read from {shots.length} item{shots.length === 1 ? "" : "s"}. Check that each message is attributed to the right person.
        Tap a name to switch speaker, edit text directly, or remove anything that isn’t a message.
      </p>
      {shots.length === 0 && <p className="empty">Nothing added yet. Go back and add screenshots or pasted text.</p>}
      {shots.map((s, idx) => (
        <article key={s.id} className="shot">
          <header>
            <strong>{s.filename}</strong>
            <span className="tag">{s.platform}</span>
            {flagged.has(s.id) && <span className="tag warn">Couldn’t match names, check the speakers</span>}
            <span className="grow" />
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
