import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { initials } from "./Sidebar";
import { platformOf } from "../platforms";
import Icon from "./Icon";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const clock = (iso) => (iso ? new Date(iso) : new Date()).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const WHY_KEY = "imy.showWhy";

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

export default function Chat({ person, onChanged, onEdit, onDelete, onBack }) {
  const [menu, setMenu] = useState(false);
  const [msgs, setMsgs] = useState([]);
  const [input, setInput] = useState("");
  const [typing, setTyping] = useState(false);
  const [busy, setBusy] = useState(false);
  const [why, setWhy] = useState(null);
  const [style, setStyle] = useState(null);
  const [showStyle, setShowStyle] = useState(false);
  const [error, setError] = useState("");
  // The "why this reply?" links give the game away, so they're off unless asked for.
  const [showWhy, setShowWhy] = useState(() => localStorage.getItem(WHY_KEY) === "1");
  const end = useRef();
  const name = person.nickname || person.name;
  const app = platformOf(person.platform);
  const toggleWhy = (on) => { setShowWhy(on); localStorage.setItem(WHY_KEY, on ? "1" : "0"); };

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
  const learned = untrained ? "No chats learned yet"
    : `${person.person_message_count} of their messages learned from ${person.screenshot_count} screenshot${person.screenshot_count === 1 ? "" : "s"}`;

  const actions = (
    <>
      <button className="btn small" role={app.id ? "menuitem" : undefined} onClick={() => setShowStyle((s) => !s)}>{showStyle ? "Hide style" : "How they write"}</button>
      <button className="btn small" role={app.id ? "menuitem" : undefined} onClick={onEdit}>Edit profile</button>
      <button className="btn small ghost" role={app.id ? "menuitem" : undefined} onClick={clear}>Clear chat</button>
      <button className="btn small ghost danger" role={app.id ? "menuitem" : undefined} onClick={() => confirm(`Delete ${name} and everything learned about them?`) && onDelete()}>Delete</button>
    </>
  );

  return (
    <div className={"chat skin-" + (app.id || "imy")}>
      <header className="chat-head">
        {app.id && <button className="ic-btn back" aria-label="Back to people" onClick={onBack}><Icon name="back" /></button>}
        <span className="avatar big">{initials(name)}{app.dot && <i className="dot" />}</span>
        <div className="grow who-line">
          <h1>{name}</h1>
          <p className="muted small">{app.id ? app.status : learned}</p>
        </div>
        {app.id ? (
          /* App skins: decorative call buttons like the real thing; the last icon opens imy's options. */
          <div className="head-icons">
            {app.headIcons.map((n) => <span key={n} className="ic"><Icon name={n} /></span>)}
            <div className="menu-wrap">
              <button className="ic-btn" aria-label="Chat options" aria-expanded={menu} onClick={() => setMenu((m) => !m)}><Icon name={app.menuIcon} /></button>
              {menu && <div className="menu" role="menu" onClick={() => setMenu(false)}>{actions}</div>}
            </div>
          </div>
        ) : (
          <div className="head-actions">{actions}</div>
        )}
      </header>

      {showStyle && (
        <div className="style-panel">
          {!style ? <p className="muted">Reading their style…</p> : (
            <>
              <ul>{style.lines.length ? style.lines.map((l) => <li key={l}>{l}</li>) : <li>Nothing to measure yet.</li>}</ul>
              <p className="muted small">{learned} · {style.exchanges} back-and-forth exchanges indexed · matching with {style.embedder}</p>
              <label className="check">
                <input type="checkbox" checked={showWhy} onChange={(e) => toggleWhy(e.target.checked)} />
                Show a “why this reply?” link under their messages.
              </label>
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
          const first = msgs[i - 1]?.role !== m.role;          // first / last bubble of a run from one sender
          const last = msgs[i + 1]?.role !== m.role;
          const lastOfGroup = m.role === "person" && last;
          return (
            <div key={m.id} className={"bubble-row " + m.role + (first ? " first" : "") + (last ? " last" : "")}>
              {lastOfGroup && <span className="avatar mini" aria-hidden="true">{initials(name)}</span>}
              <div className={"bubble " + m.role}>
                {m.text}
                <span className="meta">{clock(m.created_at)}{m.role === "user" && <b> ✓✓</b>}</span>
              </div>
              {m.role === "user" && i === msgs.length - 1 && !busy && <span className="receipt">Delivered</span>}
              {showWhy && lastOfGroup && m.debug && (
                <button className="why" aria-expanded={why === m.id} onClick={() => setWhy(why === m.id ? null : m.id)}>why this reply?</button>
              )}
              {showWhy && why === m.id && <p className="why-text">{explain(m.debug)}</p>}
            </div>
          );
        })}
        {typing && <div className="bubble-row person last"><span className="avatar mini" aria-hidden="true">{initials(name)}</span><div className="bubble person typing"><i /><i /><i /></div></div>}
        <div ref={end} />
      </div>

      {error && <div className="banner error">{error}</div>}
      <form className="composer" onSubmit={submit}>
        {app.leftIcons?.map((n) => <span key={n} className="ic"><Icon name={n} /></span>)}
        <div className="field-wrap">
          <input value={input} onChange={(e) => setInput(e.target.value)} placeholder={app.placeholder || `Message ${name}`} aria-label="Message" autoFocus />
          {app.inField && <span className="ic in-field"><Icon name={app.inField} /></span>}
        </div>
        <button className={"btn primary send" + (input.trim() ? " ready" : "")} aria-label="Send" disabled={busy || !input.trim()}>
          {app.id ? <Icon name={input.trim() ? app.sendIcon : app.idleIcon} /> : "Send"}
        </button>
      </form>
    </div>
  );
}
