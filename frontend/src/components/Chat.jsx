import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { initials } from "./Sidebar";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* Explain, in plain words, how a reply was chosen (the "why?" popover). */
function explain(d) {
  if (!d) return "";
  const score = d.score ? ` (similarity ${d.score})` : "";
  switch (d.source) {
    case "retrieval": return `Found something similar in your screenshots${score}: you wrote “${d.matched_prompt}”, and this is how they really replied.`;
    case "intent": return `Recognised “${d.intent?.replace("_", " ")}” and reused one of their own messages like it.`;
    default: return d.note || d.source;
  }
}

export default function Chat({ person, onChanged, onEdit, onDelete }) {
  const [msgs, setMsgs] = useState([]);
  const [input, setInput] = useState("");
  const [typing, setTyping] = useState(false);
  const [busy, setBusy] = useState(false);
  const [why, setWhy] = useState(null);
  const [style, setStyle] = useState(null);
  const [showStyle, setShowStyle] = useState(false);
  const [error, setError] = useState("");
  const end = useRef();
  const name = person.nickname || person.name;

  useEffect(() => { api.chat(person.id).then(setMsgs); }, [person.id]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs, typing]);
  useEffect(() => { if (showStyle) api.style(person.id).then(setStyle); }, [showStyle, person.id]);

  async function submit(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    setInput(""); setError(""); setBusy(true);
    setMsgs((m) => [...m, { id: "tmp" + Date.now(), role: "user", text }]);
    try {
      const res = await api.say(person.id, text);
      for (const b of res.bubbles) {            // reveal bubbles one by one, like someone typing
        setTyping(true);
        await sleep(450 + Math.min(1400, b.text.length * 35));
        setTyping(false);
        setMsgs((m) => [...m, b]);
      }
    } catch (err) { setError(err.message); }
    setTyping(false); setBusy(false);
  }

  async function clear() {
    if (!confirm(`Clear your chat history with ${name}? Their learned data stays.`)) return;
    await api.clearChat(person.id); setMsgs([]);
  }
  async function toggleLlm(on) { await api.setLlm(person.id, on); onChanged(); }

  const untrained = person.person_message_count === 0;

  return (
    <div className="chat">
      <header className="chat-head">
        <span className="avatar big">{initials(name)}</span>
        <div className="grow">
          <h1>{name}</h1>
          <p className="muted small">
            {untrained ? "No chats learned yet" : `${person.person_message_count} of their messages learned from ${person.screenshot_count} screenshot${person.screenshot_count === 1 ? "" : "s"}`}
          </p>
        </div>
        <div className="head-actions">
          <button className="btn small" onClick={() => setShowStyle((s) => !s)}>{showStyle ? "Hide style" : "How they write"}</button>
          <button className="btn small" onClick={onEdit}>Edit profile</button>
          <button className="btn small ghost" onClick={clear}>Clear chat</button>
          <button className="btn small ghost danger" onClick={() => confirm(`Delete ${name} and everything learned about them?`) && onDelete()}>Delete</button>
        </div>
      </header>

      {showStyle && (
        <div className="style-panel">
          {!style ? <p className="muted">Reading their style…</p> : (
            <>
              <ul>{style.lines.length ? style.lines.map((l) => <li key={l}>{l}</li>) : <li>Nothing to measure yet.</li>}</ul>
              <p className="muted small">{style.exchanges} back-and-forth exchanges indexed · matching with {style.embedder}</p>
              <label className="check">
                <input type="checkbox" checked={person.use_llm} onChange={(e) => toggleLlm(e.target.checked)} />
                Use a local model (Ollama) to write replies. Needs Ollama running; falls back automatically if not.
              </label>
            </>
          )}
        </div>
      )}

      {untrained && (
        <div className="banner">
          Replies will be generic until you add screenshots or pasted chats. <button className="link" onClick={onEdit}>Add them now</button>
        </div>
      )}

      <div className="thread" role="log" aria-live="polite">
        {msgs.length === 0 && <p className="empty">Say hello to {name}.</p>}
        {msgs.map((m, i) => {
          const lastOfGroup = m.role === "person" && msgs[i + 1]?.role !== "person";
          return (
            <div key={m.id} className={"bubble-row " + m.role}>
              <div className={"bubble " + m.role}>{m.text}</div>
              {lastOfGroup && m.debug && (
                <button className="why" aria-expanded={why === m.id} onClick={() => setWhy(why === m.id ? null : m.id)}>why this reply?</button>
              )}
              {why === m.id && <p className="why-text">{explain(m.debug)}</p>}
            </div>
          );
        })}
        {typing && <div className="bubble-row person"><div className="bubble person typing"><i /><i /><i /></div></div>}
        <div ref={end} />
      </div>

      {error && <div className="banner error">{error}</div>}
      <form className="composer" onSubmit={submit}>
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder={`Message ${name}`} aria-label="Message" autoFocus />
        <button className="btn primary" disabled={busy || !input.trim()}>Send</button>
      </form>
    </div>
  );
}
