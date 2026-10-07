import { useEffect, useRef, useState } from "react";
import { api } from "../api";

/* Trainer: Training Mode in three steps.
     Interview   -> the 21 questions (rendered from backend/questions.json)
     Screenshots -> upload images (OCR runs on the server) or paste text
     Review      -> check who-said-what and fix any mistakes, then Done */
const STEPS = ["Interview", "Screenshots", "Review"];
const RECOMMENDED = 10;

export default function Trainer({ personId, onDone, onCancel, onSaved }) {
  const [step, setStep] = useState(0);
  const [questions, setQuestions] = useState(null);
  const [answers, setAnswers] = useState({});
  const [pid, setPid] = useState(personId);
  const [shots, setShots] = useState([]);
  const [flagged, setFlagged] = useState(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.questions().then(setQuestions).catch((e) => setError(e.message));
    if (personId) {
      api.person(personId).then((p) => setAnswers(p.answers || {}));
      api.screenshots(personId).then(setShots);
    }
  }, [personId]);

  const personName = (answers.q2 || answers.q1 || "Them").split(" ")[0];

  async function saveAnswers() {
    setBusy(true); setError("");
    try {
      const saved = pid ? await api.updatePerson(pid, answers) : await api.createPerson(answers);
      setPid(saved.id);
      onSaved?.();   // refresh the sidebar list
      return saved.id;
    } catch (e) { setError(e.message); return null; }
    finally { setBusy(false); }
  }

  async function next() {
    const id = await saveAnswers();
    if (id) setStep(1);
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
              disabled={i > 0 && !pid} onClick={() => setStep(i)}>
              <span className="step-n">{i + 1}</span>{label}
            </button>
          </li>
        ))}
      </ol>

      {error && <div className="banner error">{error}</div>}

      {step === 0 && (
        <Interview questions={questions} answers={answers} setAnswers={setAnswers}
          footer={<button className="btn primary" disabled={busy} onClick={next}>{busy ? "Saving…" : "Save and continue"}</button>} />
      )}
      {step === 1 && (
        <Upload pid={pid} shots={shots} onAdded={addShots} personName={personName}
          footer={<>
            <button className="btn ghost" onClick={() => setStep(0)}>Back</button>
            <button className="btn primary" onClick={() => setStep(2)}>Review what was read</button>
          </>} />
      )}
      {step === 2 && (
        <Review shots={shots} flagged={flagged} personName={personName} reload={reload}
          footer={<>
            <button className="btn ghost" onClick={() => setStep(1)}>Back</button>
            <button className="btn primary" onClick={() => onDone(pid)}>Done</button>
          </>} />
      )}
    </div>
  );
}

/* ---------- Step 1: the interview ---------- */
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
function Upload({ pid, shots, onAdded, personName, footer }) {
  const [queue, setQueue] = useState([]);
  const [drag, setDrag] = useState(false);
  const [pasted, setPasted] = useState("");
  const [pasteName, setPasteName] = useState("");
  const [pasteMsg, setPasteMsg] = useState("");
  const input = useRef();
  const imageCount = shots.filter((s) => s.url).length;

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

  return (
    <section className="panel">
      <div className="count">
        <strong>{imageCount}</strong> screenshot{imageCount === 1 ? "" : "s"} added
        <span className="muted"> · {imageCount >= RECOMMENDED ? "that’s a good amount" : `aim for at least ${RECOMMENDED}`}</span>
        <div className="bar"><div style={{ width: Math.min(100, (imageCount / RECOMMENDED) * 100) + "%" }} /></div>
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

/* ---------- Step 3: review and fix ---------- */
function Review({ shots, flagged, personName, reload, footer }) {
  const total = shots.reduce((n, s) => n + s.messages.length, 0);
  const act = (fn) => async (...args) => { await fn(...args); await reload(); };

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
        </article>
      ))}
      <div className="actions">{footer}</div>
    </section>
  );
}
