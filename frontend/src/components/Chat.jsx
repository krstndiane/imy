import { Fragment, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { initials } from "./Sidebar";
import { PLATFORMS, platformOf } from "../platforms";
import { load, save } from "../storage";
import Icon from "./Icon";
import TrainPanel from "./TrainPanel";

const wait = (ms) => new Promise((r) => setTimeout(r, Math.max(0, ms)));
const rand = (a, b) => a + Math.random() * (b - a);
const clock = (iso) => (iso ? new Date(iso) : new Date()).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const WHY_KEY = "imy.showWhy";

/* ---------- timing: reply like a person, not a bot ----------
   1. READ   they "see" your message after a moment (longer messages take longer to read)
   2. THINK  a short pause before they start typing
   3. TYPE   "typing…" lasts as long as a person needs to type that bubble (~5 letters a second on a phone):
             "ok" comes back in a second or two, a paragraph takes much longer; long ones sometimes pause midway
   4. MORE   each extra bubble gets its own little pause + typing
   The server's own time is counted as typing time, so a fast server never makes replies feel instant. */
export function typingMs(text, cps) {
  const chars = [...(text || "")].length;
  const ms = (chars / cps) * 1000 * rand(0.85, 1.2);
  return Math.round(Math.min(45000, Math.max(chars <= 3 ? 450 : 700, ms)));
}
export function readMs(texts) {
  const chars = texts.join(" ").length;
  return Math.round(rand(450, 1200) + Math.min(2200, chars * 12));
}

/* ---------- separators: a time after a pause (Messenger, iMessage, Instagram) or a date chip (WhatsApp, Telegram) ---------- */
const sameDay = (a, b) => a.toDateString() === b.toDateString();
function dayLabel(d) {
  const now = new Date();
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (sameDay(d, now)) return "Today";
  if (sameDay(d, y)) return "Yesterday";
  if (now - d < 6 * 864e5) return d.toLocaleDateString([], { weekday: "long" });
  return d.toLocaleDateString([], { month: "long", day: "numeric", year: d.getFullYear() === now.getFullYear() ? undefined : "numeric" });
}
function sepLabel(prev, cur, kind) {
  const t = cur.created_at ? new Date(cur.created_at) : new Date();
  const p = prev?.created_at ? new Date(prev.created_at) : null;
  if (kind === "day") return p && sameDay(p, t) ? null : dayLabel(t);
  if (p && t - p < 15 * 60 * 1000) return null;
  const time = clock(t);
  return sameDay(t, new Date()) ? time : `${dayLabel(t)} ${time}`;
}

/* Explain, in plain words, how a reply was chosen (the "why?" popover). */
function explain(d) {
  if (!d) return "";
  const score = d.score ? ` (similarity ${d.score})` : "";
  switch (d.source) {
    case "retrieval": return `Found something similar in your chats${score}: you wrote “${d.matched_prompt}”, and this is how they really replied.`;
    case "intent": return `Recognised “${d.intent?.replace("_", " ")}” and reused one of their own messages like it.`;
    default: return d.note || d.source;
  }
}

export default function Chat({ person, persons = [], theme = "dark", onChanged, onEdit, onDelete, onBack }) {
  const [mode, setMode] = useState("chat");      // "chat" | "train"
  const [menu, setMenu] = useState(false);
  const [panel, setPanel] = useState(null);      // null | "style" | "look"
  const [fixing, setFixing] = useState(null);    // id of the reply being corrected
  const [fixText, setFixText] = useState("");
  const [taught, setTaught] = useState({});      // reply id -> "good" | "fixed"
  const [msgs, setMsgs] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [input, setInput] = useState("");
  const [typing, setTyping] = useState(false);
  const [why, setWhy] = useState(null);
  const [style, setStyle] = useState(null);
  const [error, setError] = useState("");
  const [showWhy, setShowWhy] = useState(() => load(WHY_KEY) === "1");   // "why this reply?" links give the game away: off by default
  const [hideHint, setHideHint] = useState(() => load(`imy.lookHint.${person.id}`) === "1");

  const queue = useRef([]);        // your messages waiting for a reply: [{id, text}]
  const busy = useRef(false);      // a reply is being read / typed right now
  const timer = useRef(null);
  const typedAt = useRef(0);
  const field = useRef();
  const alive = useRef(true);
  const cps = useRef(rand(4.6, 6.2));   // this person's typing speed (letters per second) for this chat
  const end = useRef();

  const name = person.nickname || person.name;
  const look = person.look || {};
  const skin = person.platform || look.platform || "";        // the app you picked, else the one your screenshots are from
  const app = platformOf(skin);
  const skinned = !!app.id;
  const chatMode = look.pref_mode && look.pref_mode !== "auto" ? look.pref_mode : (skinned && look.mode) || theme;
  const toggleWhy = (on) => { setShowWhy(on); save(WHY_KEY, on ? "1" : "0"); };

  // Copy your real chat: the bubble colours measured from your screenshots (custom themes included).
  const vars = {};
  if (skinned && look.use_colors !== false && look.user && look.platform === skin && look.mode === chatMode) {
    vars["--c-user"] = look.user_top ? `linear-gradient(180deg, ${look.user_top}, ${look.user_bottom})` : look.user;
    vars["--c-user-solid"] = look.user_bottom || look.user;
    vars["--c-user-ink"] = look.user_ink;
    if (look.them) { vars["--c-them"] = look.them; vars["--c-them-ink"] = look.them_ink; }
    if (skin === "messenger" || skin === "viber") vars["--c-accent"] = look.user_bottom || look.user;
  }

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; clearTimeout(timer.current); };
  }, []);
  useEffect(() => {
    api.chat(person.id)
      .then((list) => alive.current && setMsgs(Array.isArray(list) ? list.map((m) => ({ ...m, status: "seen" })) : []))
      .catch((e) => alive.current && setError(e.message))
      .finally(() => alive.current && setLoaded(true));
  }, [person.id]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs, typing]);
  useEffect(() => {
    if (panel === "style") api.style(person.id).then(setStyle).catch((e) => setError(e.message));
  }, [panel, person.id]);

  /* Wait until you've finished a burst of messages (people often send 2-3 in a row), then reply to all of them. */
  function schedule(delay) {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      if (Date.now() - typedAt.current < 1200 && field.current?.value.trim()) return schedule(900);   // you're still typing
      flush();
    }, delay);
  }

  async function flush() {
    if (busy.current || !queue.current.length) return;
    busy.current = true;
    const batch = queue.current.splice(0);
    const ids = batch.map((b) => b.id);
    const texts = batch.map((b) => b.text);
    const call = api.sayMany(person.id, texts).then((r) => ({ ok: true, r }), (e) => ({ ok: false, e }));
    try {
      await wait(readMs(texts));                                                     // 1. read
      if (!alive.current) return;
      const seenAt = new Date().toISOString();
      setMsgs((m) => m.map((x) => (ids.includes(x.id) ? { ...x, status: "seen", seenAt } : x)));
      await wait(rand(250, 800));                                                    // 2. think
      if (!alive.current) return;
      setTyping(true);
      const typingFrom = Date.now();
      const res = await call;
      if (!alive.current) return;
      if (!res.ok) {
        setTyping(false);
        setMsgs((m) => m.map((x) => (ids.includes(x.id) ? { ...x, status: "failed" } : x)));
        setError(res.e.message);
        return;
      }
      const bubbles = res.r.bubbles || [];
      for (let k = 0; k < bubbles.length; k++) {                                     // 3 + 4. type each bubble
        let ms = typingMs(bubbles[k].text, cps.current);
        if (k === 0) ms -= Date.now() - typingFrom;
        else {
          setTyping(false);
          await wait(rand(250, 800));
          if (!alive.current) return;
          setTyping(true);
        }
        if (ms > 9000 && Math.random() < 0.35) {                                     // a pause mid-paragraph
          const part = ms * rand(0.3, 0.6);
          await wait(part);
          if (!alive.current) return;
          setTyping(false);
          await wait(rand(700, 1600));
          if (!alive.current) return;
          setTyping(true);
          ms -= part;
        }
        await wait(ms);
        if (!alive.current) return;
        setTyping(false);
        const b = bubbles[k];
        setMsgs((m) => [...m, { ...b, created_at: new Date().toISOString(), status: "seen" }]);
      }
    } finally {
      busy.current = false;
      if (alive.current && queue.current.length) schedule(rand(600, 1200));         // you texted again meanwhile
    }
  }

  function submit(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text) return;
    setInput(""); setError("");
    const id = `tmp${Date.now()}${Math.random()}`;
    setMsgs((m) => [...m, { id, role: "user", text, created_at: new Date().toISOString(), status: "sent" }]);
    queue.current.push({ id, text });
    if (!busy.current) schedule(rand(650, 1100));
  }

  function retryFailed() {
    const failed = msgs.filter((m) => m.status === "failed");
    if (!failed.length) return;
    setMsgs((m) => m.map((x) => (x.status === "failed" ? { ...x, status: "sent" } : x)));
    queue.current.push(...failed.map((m) => ({ id: m.id, text: m.text })));
    setError("");
    if (!busy.current) schedule(300);
  }

  async function clear() {
    if (!confirm(`Clear your chat history with ${name}? Their learned data stays.`)) return;
    try { await api.clearChat(person.id); setMsgs([]); } catch (e) { setError(e.message); }
  }
  async function toggleLlm(on) {
    try { await api.setLlm(person.id, on); onChanged(); } catch (e) { setError(e.message); }
  }
  async function setLook(patch) {
    if (patch.platform !== undefined) dismissHint();   // you chose an app yourself: no need to suggest one
    try { await api.setLook(person.id, patch); onChanged(); } catch (e) { setError(e.message); }
  }
  function dismissHint() { setHideHint(true); save(`imy.lookHint.${person.id}`, "1"); }

  /* Teaching from the chat: what you said just before a reply, and the reply's bubbles. */
  function exchange(i) {
    let j = i;
    while (j > 0 && msgs[j - 1].role === "person") j--;
    let k = j;
    while (k > 0 && msgs[k - 1].role === "user") k--;
    return { prompt: msgs.slice(k, j).map((m) => m.text).join(" "), reply: msgs.slice(j, i + 1).map((m) => m.text).join("\n") };
  }
  async function teach(i, id, reply) {
    const { prompt } = exchange(i);
    if (!prompt) return;
    try {
      await api.teach(person.id, prompt, reply);
      setTaught((t) => ({ ...t, [id]: reply === exchange(i).reply ? "good" : "fixed" }));
      setFixing(null); setFixText(""); onChanged();
    } catch (err) { setError(err.message); }
  }

  const untrained = person.person_message_count === 0;
  const learned = untrained ? "No chats learned yet"
    : `${person.person_message_count} of their messages learned from ${person.screenshot_count} screenshot${person.screenshot_count === 1 ? "" : "s"}`;
  const status = typing && app.typingIn === "header" ? app.typingText : skinned ? app.status : learned;
  const detected = look.detected && look.detected !== person.platform ? platformOf(look.detected) : null;
  const lastIndex = msgs.length - 1;

  const item = (label, fn) => <button className="btn small" role={skinned ? "menuitem" : undefined} onClick={fn}>{label}</button>;
  const actions = (
    <>
      {item(mode === "train" ? "Back to chat" : "Train", () => setMode(mode === "train" ? "chat" : "train"))}
      {item(panel === "look" ? "Hide chat look" : "Chat look", () => { setMode("chat"); setPanel(panel === "look" ? null : "look"); })}
      {item(panel === "style" ? "Hide style" : "How they write", () => { setMode("chat"); setPanel(panel === "style" ? null : "style"); })}
      {item("Edit profile", onEdit)}
      <button className="btn small ghost" role={skinned ? "menuitem" : undefined} onClick={clear}>Clear chat</button>
      <button className="btn small ghost danger" role={skinned ? "menuitem" : undefined} onClick={() => confirm(`Delete ${name} and everything learned about them?`) && onDelete()}>Delete</button>
    </>
  );

  return (
    <div className={"chat skin-" + (app.id || "imy")} data-mode={chatMode} style={vars}>
      <header className="chat-head">
        {skinned && <button className="ic-btn back" aria-label="Back to people" onClick={onBack}><Icon name="back" /></button>}
        <span className="avatar big">{initials(name)}{app.dot && <i className="dot" />}</span>
        <div className="grow who-line">
          <div className="name-row">
            <h1>{name}</h1>
            <span className="imy-mark" title="An AI version made with imy, not the real person">imy</span>
          </div>
          <p className={"muted small status" + (typing && app.typingIn === "header" ? " is-typing" : "")}>{status}</p>
        </div>
        {skinned ? (
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

      <div className="mode-switch" role="tablist" aria-label="Chat or train">
        <button role="tab" aria-selected={mode === "chat"} className={mode === "chat" ? "on" : ""} onClick={() => setMode("chat")}>Chat</button>
        <button role="tab" aria-selected={mode === "train"} className={mode === "train" ? "on" : ""} onClick={() => setMode("train")}>Train</button>
      </div>

      {mode === "train" ? (
        <TrainPanel person={person} persons={persons} onChanged={onChanged} onEditProfile={onEdit} onBackToChat={() => setMode("chat")} />
      ) : (<>
      {panel === "style" && (
        <div className="style-panel">
          {!style ? <p className="muted">Reading their style…</p> : (
            <>
              <ul>{style.lines.length ? style.lines.map((l) => <li key={l}>{l}</li>) : <li>Nothing to measure yet.</li>}</ul>
              <p className="muted small">{learned} · {style.exchanges} back-and-forth exchanges indexed · matching with {style.embedder}</p>
              <p className="small">
                {style.ai?.providers?.length
                  ? `Smart replies are on (${style.ai.providers.join(" + ")}): when what they taught imy isn't enough, an AI answers in their style.`
                  : "Smart replies are off. Add a free Groq key to the .env file to turn them on (see the README)."}
              </p>
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

      {panel === "look" && <LookPanel person={person} look={look} skin={skin} onChange={setLook} onClose={() => setPanel(null)} />}

      {detected && person.platform && !hideHint && panel !== "look" && (
        <div className="banner">
          Your screenshots look like {detected.label}.{" "}
          <button className="link" onClick={() => setLook({ platform: detected.id })}>Make this chat look like {detected.label}</button>
          {" · "}
          <button className="link" onClick={dismissHint}>Keep {app.label}</button>
        </div>
      )}

      {untrained && (
        <div className="banner">
          Replies will be generic until you add chats. <button className="link" onClick={() => setMode("train")}>Train {name} now</button>
        </div>
      )}

      <div className="thread" role="log" aria-live="polite">
        {loaded && msgs.length === 0 && <p className="empty">Say hello to {name}.</p>}
        {msgs.map((m, i) => {
          const prev = msgs[i - 1], next = msgs[i + 1];
          const sep = sepLabel(prev, m, app.seps);
          const nextSep = next ? sepLabel(m, next, app.seps) : null;
          const first = !prev || prev.role !== m.role || !!sep;      // first / last bubble of a run from one sender
          const last = !next || next.role !== m.role || !!nextSep;
          const lastOfGroup = m.role === "person" && last;
          const isLatest = i === lastIndex;
          return (
            <Fragment key={m.id}>
              {sep && <div className={"sep " + app.seps}><span>{sep}</span></div>}
              <div className={"bubble-row " + m.role + (first ? " first" : "") + (last ? " last" : "")}>
                {lastOfGroup && <span className="avatar mini" aria-hidden="true">{initials(name)}</span>}
                <div className={"bubble " + m.role}>
                  {m.text}
                  <span className="meta">{clock(m.created_at)}{m.role === "user" && <Ticks status={m.status} skin={skin} />}</span>
                </div>
                {m.role === "user" && m.status === "failed" && (
                  <button className="receipt failed" onClick={retryFailed}>Not delivered. Tap to try again</button>
                )}
                {m.role === "user" && isLatest && m.status !== "failed" && <Receipt m={m} app={app} name={name} />}
                {showWhy && lastOfGroup && m.debug && (
                  <button className="why" aria-expanded={why === m.id} onClick={() => setWhy(why === m.id ? null : m.id)}>why this reply?</button>
                )}
                {showWhy && why === m.id && <p className="why-text">{explain(m.debug)}</p>}
                {lastOfGroup && typeof m.id === "number" && i > 0 && (
                  taught[m.id] ? (
                    <p className="teach-done">{taught[m.id] === "good" ? "Noted: sounds like them 👍" : `Saved. Next time ${name} will answer like this.`}</p>
                  ) : fixing === m.id ? (
                    <form className="teach-fix" onSubmit={(e) => { e.preventDefault(); fixText.trim() && teach(i, m.id, fixText.trim()); }}>
                      <textarea autoFocus rows={2} value={fixText} onChange={(e) => setFixText(e.target.value)}
                        placeholder={`What would ${name} really say?`}
                        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); fixText.trim() && teach(i, m.id, fixText.trim()); } }} />
                      <div className="teach-actions">
                        <button type="button" className="link" onClick={() => setFixing(null)}>Cancel</button>
                        <button className="btn small primary" disabled={!fixText.trim()}>Teach</button>
                      </div>
                    </form>
                  ) : (
                    <div className="teach">
                      <button title={`This sounds like ${name}`} onClick={() => teach(i, m.id, exchange(i).reply)}>👍</button>
                      <button title={`Teach imy what ${name} would really say`} onClick={() => { setFixing(m.id); setFixText(""); }}>Fix</button>
                    </div>
                  )
                )}
              </div>
            </Fragment>
          );
        })}
        {typing && app.typingIn === "thread" && (
          <div className="bubble-row person first last typing-row" aria-label={`${name} is typing`}>
            <span className="avatar mini" aria-hidden="true">{initials(name)}</span>
            <div className="bubble person typing"><i /><i /><i /></div>
          </div>
        )}
        <div ref={end} />
      </div>

      {error && (
        <div className="banner error" role="alert">
          {error} {msgs.some((m) => m.status === "failed") && <button className="link" onClick={retryFailed}>Try again</button>}
        </div>
      )}
      <form className="composer" onSubmit={submit}>
        {app.leftIcons?.map((n) => <span key={n} className="ic"><Icon name={n} /></span>)}
        <div className="field-wrap">
          <input ref={field} value={input} onChange={(e) => { setInput(e.target.value); typedAt.current = Date.now(); }}
            placeholder={app.placeholder || `Message ${name}`} aria-label="Message" autoFocus maxLength={4000} />
          {app.inField && <span className="ic in-field"><Icon name={app.inField} /></span>}
        </div>
        <button className={"btn primary send" + (input.trim() ? " ready" : "")} aria-label="Send" disabled={!input.trim()}>
          {skinned ? <Icon name={input.trim() ? app.sendIcon : app.idleIcon} /> : "Send"}
        </button>
      </form>
      </>)}
    </div>
  );
}

/* ✓✓ inside your bubble (WhatsApp, Telegram): grey/one tick when sent, blue/two ticks once they've read it. */
function Ticks({ status, skin }) {
  if (skin === "telegram") return <b className={"ticks " + status}>{status === "seen" ? " ✓✓" : " ✓"}</b>;
  return <b className={"ticks " + status}> ✓✓</b>;
}

/* Under your latest message: how each app shows it was read. */
function Receipt({ m, app, name }) {
  switch (app.receipt) {
    case "avatar":
      return m.status === "seen"
        ? <span className="receipt seen-av" title={`Seen by ${name}`}><span className="avatar">{initials(name)}</span></span>
        : <span className="receipt sent-dot" title="Sent">✓</span>;
    case "delivered":
      return <span className="receipt">{m.status === "seen" ? `Read ${clock(m.seenAt || m.created_at)}` : "Delivered"}</span>;
    case "text":
      return m.status === "seen" ? <span className="receipt">{app.seenText || "Seen"}</span> : null;
    default:
      return null;
  }
}

/* "Chat look": which app the chat looks like, light/dark, and whether to copy your screenshots' colours. */
function LookPanel({ person, look, skin, onChange, onClose }) {
  const pref = look.pref_mode || "auto";
  return (
    <div className="style-panel look-panel">
      <p className="small"><strong>Make it look like</strong></p>
      <div className="chips">
        {PLATFORMS.map((p) => (
          <button key={p.id || "imy"} className={"chip" + (skin === p.id ? " on" : "")} onClick={() => onChange({ platform: p.id })}>
            {p.id ? p.label : "imy"}{look.detected === p.id && p.id ? " · from your screenshots" : ""}
          </button>
        ))}
      </div>
      <p className="small"><strong>Light or dark</strong></p>
      <div className="chips">
        {[["auto", look.mode ? `Like my screenshots (${look.mode})` : "Same as imy"], ["light", "Light"], ["dark", "Dark"]].map(([id, label]) => (
          <button key={id} className={"chip" + (pref === id ? " on" : "")} onClick={() => onChange({ mode: id })}>{label}</button>
        ))}
      </div>
      {look.user && (
        <label className="check">
          <input type="checkbox" checked={look.use_colors !== false} onChange={(e) => onChange({ colors: e.target.checked })} />
          Use the bubble colours from my screenshots
          <span className="swatch" style={{ background: look.user_top ? `linear-gradient(${look.user_top}, ${look.user_bottom})` : look.user }} />
          <span className="swatch" style={{ background: look.them }} />
        </label>
      )}
      <div className="actions"><button className="btn small" onClick={onClose}>Done</button></div>
    </div>
  );
}
