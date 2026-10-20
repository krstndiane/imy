import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { platformOf } from "../platforms";
import { load, save } from "../storage";
import Avatar from "./Avatar";
import Icon from "./Icon";
import TrainPanel from "./TrainPanel";
import { LookPanel, MediaPanel, MemoryPanel, PhotoViewer, ProfilePanel } from "./ChatPanels";

const wait = (ms) => new Promise((r) => setTimeout(r, Math.max(0, ms)));
const rand = (a, b) => a + Math.random() * (b - a);
const clock = (iso) => (iso ? new Date(iso) : new Date()).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const WHY_KEY = "imy.showWhy";
const kb = (n) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

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
  const [panel, setPanel] = useState(null);      // null | style | look | profile | media | memory
  const [fixing, setFixing] = useState(null);    // id of the reply being corrected
  const [fixText, setFixText] = useState("");
  const [taught, setTaught] = useState({});      // reply id -> "good" | "fixed"
  const [msgs, setMsgs] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [older, setOlder] = useState(false);     // are there messages further back?
  const [input, setInput] = useState(() => load(`imy.draft.${person.id}`, ""));
  const [typing, setTyping] = useState(false);
  const [why, setWhy] = useState(null);
  const [style, setStyle] = useState(null);
  const [error, setError] = useState("");
  const [hiddenNote, setHiddenNote] = useState("");   // "1 password": hidden from what you just sent
  const [attach, setAttach] = useState([]);      // photos picked but not sent yet
  const [busyUpload, setBusyUpload] = useState(false);
  const [replyTo, setReplyTo] = useState(null);  // the message you're replying to
  const [bar, setBar] = useState(null);          // id of the message whose reaction bar is open
  const [photo, setPhoto] = useState(null);      // photo opened full size
  const [find, setFind] = useState(null);        // search text, or null when the search box is closed
  const [showWhy, setShowWhy] = useState(() => load(WHY_KEY) === "1");   // "why this reply?" links give the game away: off by default
  const [hideHint, setHideHint] = useState(() => load(`imy.lookHint.${person.id}`) === "1");

  const queue = useRef([]);        // your messages waiting for a reply
  const busy = useRef(false);      // a reply is being read / typed right now
  const timer = useRef(null);
  const typedAt = useRef(0);
  const field = useRef();
  const picker = useRef();
  const alive = useRef(true);
  const cps = useRef(rand(4.6, 6.2));   // this person's typing speed (letters per second) for this chat
  const thread = useRef();
  const end = useRef();

  const name = person.nickname || person.name;
  const look = person.look || {};
  const colour = person.theme?.color || "";
  const quick = person.theme?.emoji || "👍";
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
  if (colour) {   // a colour you picked yourself wins over everything
    vars["--c-user"] = colour; vars["--c-user-solid"] = colour; vars["--c-user-ink"] = "#fff"; vars["--c-accent"] = colour;
  }

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; clearTimeout(timer.current); };
  }, []);

  /* The conversation is kept on the server, so it's all still here when you come back. */
  useEffect(() => {
    api.chat(person.id)
      .then((r) => {
        if (!alive.current) return;
        const list = Array.isArray(r) ? r : r.messages || [];
        setMsgs(list.map((m) => ({ ...m, status: "seen" })));
        setOlder(!!r.has_more);
      })
      .catch((e) => alive.current && setError(e.message))
      .finally(() => alive.current && setLoaded(true));
  }, [person.id]);

  useEffect(() => { save(`imy.draft.${person.id}`, input); }, [input, person.id]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs.length, typing]);
  useEffect(() => {
    if (panel === "style") api.style(person.id).then(setStyle).catch((e) => setError(e.message));
  }, [panel, person.id]);

  /* Scrolled to the top of a long chat: quietly bring in the older messages. */
  const loadOlder = useCallback(async () => {
    const box = thread.current;
    if (!box || !older || !msgs.length) return;
    setOlder(false);
    const was = box.scrollHeight;
    try {
      const r = await api.chat(person.id, msgs[0].id);
      if (!alive.current) return;
      setMsgs((m) => [...(r.messages || []).map((x) => ({ ...x, status: "seen" })), ...m]);
      setOlder(!!r.has_more);
      requestAnimationFrame(() => { if (thread.current) thread.current.scrollTop = thread.current.scrollHeight - was; });
    } catch (e) {
      setOlder(true);
      setError(e.message);
    }
  }, [older, msgs, person.id]);

  function onScroll(e) {
    if (e.currentTarget.scrollTop < 80) loadOlder();
  }

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
    const ids = batch.flatMap((b) => b.ids);
    const texts = batch.map((b) => b.text).filter(Boolean);
    const mediaIds = batch.flatMap((b) => b.mediaIds || []);
    const answering = batch.find((b) => b.replyTo)?.replyTo || null;
    const call = api.sayMany(person.id, texts, { media_ids: mediaIds, reply_to: answering })
      .then((r) => ({ ok: true, r }), (e) => ({ ok: false, e }));
    try {
      await wait(readMs(texts.length ? texts : ["photo"]));                          // 1. read
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
      if (res.r.hidden) setHiddenNote(res.r.hidden);
      if (res.r.sent?.length) {   // swap your temporary bubbles for the saved ones (real ids, real photos)
        setMsgs((m) => [...m.filter((x) => !ids.includes(x.id)),
                        ...res.r.sent.map((x) => ({ ...x, status: "seen", seenAt }))]);
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
      if (res.r.reaction) {                                                          // they reacted to your message
        await wait(rand(400, 1400));
        if (!alive.current) return;
        const { id, emoji } = res.r.reaction;
        setMsgs((m) => m.map((x) => (x.id === id ? { ...x, reactions: { ...(x.reactions || {}), person: emoji } } : x)));
      }
    } finally {
      busy.current = false;
      if (alive.current && queue.current.length) schedule(rand(600, 1200));         // you texted again meanwhile
    }
  }

  function submit(e) {
    e?.preventDefault();
    const text = input.trim();
    if (!text && !attach.length) return;
    const stamp = new Date().toISOString();
    const tmp = (n) => `tmp${Date.now()}${n}${Math.random()}`;
    const quote = replyTo ? { id: replyTo.id, role: replyTo.role, text: replyTo.text, photo: !!replyTo.media } : null;
    const bubbles = attach.map((m, i) => ({ id: tmp("m" + i), role: "user", kind: "media", text: "", media: m,
                                            created_at: stamp, status: "sent", quote }));
    if (text) bubbles.push({ id: tmp("t"), role: "user", kind: "text", text, created_at: stamp, status: "sent",
                             quote: bubbles.length ? null : quote });
    setInput(""); setAttach([]); setReplyTo(null); setError("");
    setMsgs((m) => [...m, ...bubbles]);
    queue.current.push({ ids: bubbles.map((b) => b.id), text, mediaIds: attach.map((m) => m.id), replyTo: replyTo?.id || null });
    if (!busy.current) schedule(rand(650, 1100));
  }

  function retryFailed() {
    const failed = msgs.filter((m) => m.status === "failed");
    if (!failed.length) return;
    setMsgs((m) => m.map((x) => (x.status === "failed" ? { ...x, status: "sent" } : x)));
    queue.current.push({ ids: failed.map((m) => m.id), text: failed.map((m) => m.text).filter(Boolean).join("\n"),
                         mediaIds: failed.filter((m) => m.media).map((m) => m.media.id), replyTo: null });
    setError("");
    if (!busy.current) schedule(300);
  }

  /* Photos and files: uploaded as soon as you pick them, so you see them before you hit send. */
  async function pickFiles(list) {
    const files = [...list].slice(0, 10);
    if (!files.length) return;
    setBusyUpload(true); setError("");
    try {
      const saved = await api.uploadMedia(person.id, files);
      const good = (saved || []).filter((m) => !m.error);
      const bad = (saved || []).filter((m) => m.error);
      if (bad.length) setError(`${bad[0].filename}: ${bad[0].error}`);
      setAttach((a) => [...a, ...good].slice(0, 10));
      field.current?.focus();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyUpload(false);
    }
  }
  function onPaste(e) {
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) { e.preventDefault(); pickFiles(files); }
  }

  async function toggleReaction(m, emoji) {
    setBar(null);
    const mine = m.reactions?.user;
    const next = { ...(m.reactions || {}) };
    if (mine === emoji) delete next.user; else next.user = emoji;
    setMsgs((list) => list.map((x) => (x.id === m.id ? { ...x, reactions: next } : x)));
    try { await api.react(m.id, emoji); } catch (e) { setError(e.message); }
  }
  async function unsend(m) {
    setBar(null);
    if (!confirm("Unsend this message?")) return;
    setMsgs((list) => list.map((x) => (x.id === m.id ? { ...x, deleted: true, text: "", media: null } : x)));
    try { await api.unsend(m.id); } catch (e) { setError(e.message); }
  }

  async function clear() {
    if (!confirm(`Clear your chat history with ${name}? Their learned data stays.`)) return;
    try { await api.clearChat(person.id); setMsgs([]); setOlder(false); onChanged(); } catch (e) { setError(e.message); }
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
  const shown = find ? msgs.filter((m) => (m.text || "").toLowerCase().includes(find.toLowerCase())) : msgs;
  const lastIndex = msgs.length - 1;
  const openPanel = (p) => { setMode("chat"); setPanel((cur) => (cur === p ? null : p)); };

  const item = (label, fn) => <button className="btn small" role={skinned ? "menuitem" : undefined} onClick={fn}>{label}</button>;
  const actions = (
    <>
      {item(mode === "train" ? "Back to chat" : "Train", () => setMode(mode === "train" ? "chat" : "train"))}
      {item("Profile & nicknames", () => openPanel("profile"))}
      {item("Media & files", () => openPanel("media"))}
      {item("What they remember", () => openPanel("memory"))}
      {item(panel === "look" ? "Hide chat look" : "Chat look", () => openPanel("look"))}
      {item(panel === "style" ? "Hide style" : "How they write", () => openPanel("style"))}
      {item(find === null ? "Search in chat" : "Close search", () => setFind(find === null ? "" : null))}
      {item("Edit profile", onEdit)}
      <button className="btn small ghost" role={skinned ? "menuitem" : undefined} onClick={clear}>Clear chat</button>
      <button className="btn small ghost danger" role={skinned ? "menuitem" : undefined} onClick={() => confirm(`Delete ${name} and everything learned about them?`) && onDelete()}>Delete</button>
    </>
  );

  return (
    <div className={"chat skin-" + (app.id || "imy")} data-mode={chatMode} style={vars}>
      <header className="chat-head">
        {skinned && <button className="ic-btn back" aria-label="Back to people" onClick={onBack}><Icon name="back" /></button>}
        <button className="avatar-btn" aria-label={`${name}'s profile`} onClick={() => openPanel("profile")}>
          <Avatar src={person.avatar} name={name} className="big" dot={app.dot} />
        </button>
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
                  : style.ai?.opted_out
                    ? "Smart replies are off for your account: nothing leaves imy. You can switch them back on in the account menu."
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

      {panel === "profile" && <ProfilePanel person={person} onSaved={onChanged} onError={setError}
        onOpen={openPanel} onClose={() => setPanel(null)} />}
      {panel === "look" && <LookPanel look={look} skin={skin} onChange={setLook} onClose={() => setPanel(null)} />}
      {panel === "media" && <MediaPanel person={person} onError={setError} onOpenPhoto={setPhoto} onClose={() => setPanel(null)} />}
      {panel === "memory" && <MemoryPanel person={person} onError={setError} onChanged={onChanged} onClose={() => setPanel(null)} />}

      {find !== null && (
        <div className="find-bar">
          <Icon name="search" />
          <input autoFocus value={find} aria-label="Search this chat" placeholder={`Search your chat with ${name}`} onChange={(e) => setFind(e.target.value)} />
          <span className="muted small">{find ? `${shown.length} found` : ""}</span>
          <button className="ic-btn" aria-label="Close search" onClick={() => setFind(null)}><Icon name="close" /></button>
        </div>
      )}

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

      {hiddenNote && (
        <div className="banner" role="status">
          imy hid {hiddenNote} from your message, so it isn’t saved or sent anywhere. Please don’t share passwords
          or account numbers here.{" "}
          <button className="link" onClick={() => setHiddenNote("")}>OK</button>
        </div>
      )}

      <div className="thread" role="log" aria-live="polite" ref={thread} onScroll={onScroll}>
        {older && <button className="btn small block older" onClick={loadOlder}>Load earlier messages</button>}
        {/* At the very top of the conversation, the way Messenger shows its encryption note:
            a plain reminder of what this is. */}
        {loaded && !older && !find && (
          <div className="sep event ai-note"><span>Replies are written by AI from {name}'s old messages. It isn't really {name}.</span></div>
        )}
        {loaded && msgs.length === 0 && <p className="empty">Say hello to {name}.</p>}
        {find && !shown.length && <p className="empty">Nothing found.</p>}
        {shown.map((m, i) => {
          const prev = shown[i - 1], next = shown[i + 1];
          if (m.kind === "event") return <div className="sep event" key={m.id}><span>{m.text}</span></div>;
          const sep = sepLabel(prev, m, app.seps);
          const nextSep = next ? sepLabel(m, next, app.seps) : null;
          const first = !prev || prev.role !== m.role || !!sep;      // first / last bubble of a run from one sender
          const last = !next || next.role !== m.role || !!nextSep;
          const lastOfGroup = m.role === "person" && last;
          const isLatest = msgs[lastIndex]?.id === m.id;
          const reacts = Object.values(m.reactions || {});
          const saved = typeof m.id === "number";
          const at = find ? msgs.findIndex((x) => x.id === m.id) : i;   // where it really sits in the chat
          return (
            <Fragment key={m.id}>
              {sep && <div className={"sep " + app.seps}><span>{sep}</span></div>}
              <div className={"bubble-row " + m.role + (first ? " first" : "") + (last ? " last" : "") + (m.media ? " has-media" : "")}>
                {lastOfGroup && <Avatar src={person.avatar} name={name} className="mini" />}
                {m.quote && (
                  <div className="quote" title={m.quote.text}>
                    <Icon name="reply" />
                    <span>{m.quote.role === "user" ? "You" : name}: {m.quote.photo && !m.quote.text ? "photo" : m.quote.text}</span>
                  </div>
                )}
                <div className={"bubble " + m.role + (m.deleted ? " unsent" : "") + (m.media?.kind === "image" ? " photo" : "")}
                  onClick={() => saved && !m.deleted && !String(window.getSelection?.() || "") && setBar(bar === m.id ? null : m.id)}>
                  {m.deleted ? <i>{m.role === "user" ? "You unsent a message" : `${name} unsent a message`}</i> : <>
                    {m.media && <MediaBubble media={m.media} onOpen={setPhoto} />}
                    {m.text}
                  </>}
                  <span className="meta">{clock(m.created_at)}{m.role === "user" && <Ticks status={m.status} skin={skin} />}</span>
                  {reacts.length > 0 && <span className="reacts">{reacts.join("")}</span>}
                </div>

                {bar === m.id && !m.deleted && (
                  <div className="react-bar" onClick={(e) => e.stopPropagation()}>
                    {[quick, "❤️", "😂", "😮", "🥹", "😡"].filter((e, k, a) => a.indexOf(e) === k).map((e) => (
                      <button key={e} className={m.reactions?.user === e ? "on" : ""} aria-label={`React with ${e}`} aria-pressed={m.reactions?.user === e} onClick={() => toggleReaction(m, e)}>{e}</button>
                    ))}
                    <button className="txt" onClick={() => { setReplyTo(m); setBar(null); field.current?.focus(); }}>Reply</button>
                    {m.role === "user" && <button className="txt" onClick={() => unsend(m)}>Unsend</button>}
                  </div>
                )}

                {m.role === "user" && m.status === "failed" && (
                  <button className="receipt failed" onClick={retryFailed}>Not delivered. Tap to try again</button>
                )}
                {m.role === "user" && isLatest && m.status !== "failed" && <Receipt m={m} app={app} name={name} person={person} />}
                {showWhy && lastOfGroup && m.debug && (
                  <button className="why" aria-expanded={why === m.id} onClick={() => setWhy(why === m.id ? null : m.id)}>why this reply?</button>
                )}
                {showWhy && why === m.id && <p className="why-text">{explain(m.debug)}</p>}
                {lastOfGroup && saved && at > 0 && !m.deleted && (
                  taught[m.id] ? (
                    <p className="teach-done">{taught[m.id] === "good" ? "Noted: sounds like them 👍" : `Saved. Next time ${name} will answer like this.`}</p>
                  ) : fixing === m.id ? (
                    <form className="teach-fix" onSubmit={(e) => { e.preventDefault(); fixText.trim() && teach(at, m.id, fixText.trim()); }}>
                      <textarea autoFocus rows={2} value={fixText} onChange={(e) => setFixText(e.target.value)}
                        placeholder={`What would ${name} really say?`}
                        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); fixText.trim() && teach(at, m.id, fixText.trim()); } }} />
                      <div className="teach-actions">
                        <button type="button" className="link" onClick={() => setFixing(null)}>Cancel</button>
                        <button className="btn small primary" disabled={!fixText.trim()}>Teach</button>
                      </div>
                    </form>
                  ) : (
                    <div className="teach">
                      <button title={`This sounds like ${name}`} aria-label={`This sounds like ${name}`} onClick={() => teach(at, m.id, exchange(at).reply)}>👍</button>
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
            <Avatar src={person.avatar} name={name} className="mini" />
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

      {replyTo && (
        <div className="replying">
          <Icon name="reply" />
          <span className="grow">Replying to {replyTo.role === "user" ? "yourself" : name}: <i>{replyTo.text || "photo"}</i></span>
          <button className="ic-btn" aria-label="Cancel reply" onClick={() => setReplyTo(null)}><Icon name="close" /></button>
        </div>
      )}
      {(attach.length > 0 || busyUpload) && (
        <div className="attachments">
          {attach.map((m) => (
            <div key={m.id} className="chip-media">
              {m.kind === "image" ? <img src={m.url} alt="" /> : <span className="file-ic"><Icon name="file" /></span>}
              <button aria-label="Remove" onClick={() => setAttach((a) => a.filter((x) => x.id !== m.id))}><Icon name="close" /></button>
            </div>
          ))}
          {busyUpload && <span className="muted small">Adding…</span>}
        </div>
      )}

      <form className="composer" onSubmit={submit}>
        <button type="button" className="ic-btn attach" aria-label="Send a photo or file" disabled={busyUpload}
          onClick={() => picker.current?.click()}>
          <Icon name={app.leftIcons?.[0] || "plus"} />
        </button>
        {app.leftIcons?.slice(1).map((n) => <span key={n} className="ic"><Icon name={n} /></span>)}
        <input ref={picker} type="file" multiple hidden accept="image/*,video/*,audio/*,.pdf,.txt,.doc,.docx,.xls,.xlsx,.csv,.zip"
          onChange={(e) => { pickFiles(e.target.files); e.target.value = ""; }} />
        <div className="field-wrap">
          <input ref={field} value={input} onPaste={onPaste}
            onChange={(e) => { setInput(e.target.value); typedAt.current = Date.now(); }}
            placeholder={app.placeholder || `Message ${name}`} aria-label="Message" autoFocus maxLength={4000} />
          {app.inField && <span className="ic in-field"><Icon name={app.inField} /></span>}
        </div>
        <button className={"btn primary send" + (input.trim() || attach.length ? " ready" : "")} aria-label="Send"
          disabled={!input.trim() && !attach.length}>
          {skinned ? <Icon name={input.trim() || attach.length ? app.sendIcon : app.idleIcon} /> : "Send"}
        </button>
      </form>
      </>)}
      <PhotoViewer media={photo} onClose={() => setPhoto(null)} />
    </div>
  );
}

/* A shared photo, video, voice note or file inside a bubble. */
function MediaBubble({ media, onOpen }) {
  if (media.kind === "image") {
    return <img className="sent-photo" src={media.url} alt={media.description || "shared photo"} loading="lazy"
      onClick={(e) => { e.stopPropagation(); onOpen(media); }} />;
  }
  if (media.kind === "video") return <video className="sent-photo" src={media.url} controls preload="metadata" />;
  if (media.kind === "audio") return <audio className="sent-audio" src={media.url} controls preload="metadata" />;
  return (
    <a className="file-row" href={media.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
      <Icon name="file" /><span>{media.filename}</span><small>{kb(media.bytes || 0)}</small>
    </a>
  );
}

/* ✓✓ inside your bubble (WhatsApp, Telegram): grey/one tick when sent, blue/two ticks once they've read it. */
function Ticks({ status, skin }) {
  if (skin === "telegram") return <b className={"ticks " + status}>{status === "seen" ? " ✓✓" : " ✓"}</b>;
  return <b className={"ticks " + status}> ✓✓</b>;
}

/* Under your latest message: how each app shows it was read. */
function Receipt({ m, app, name, person }) {
  switch (app.receipt) {
    case "avatar":
      return m.status === "seen"
        ? <span className="receipt seen-av" title={`Seen by ${name}`}><Avatar src={person.avatar} name={name} /></span>
        : <span className="receipt sent-dot" title="Sent">✓</span>;
    case "delivered":
      return <span className="receipt">{m.status === "seen" ? `Read ${clock(m.seenAt || m.created_at)}` : "Delivered"}</span>;
    case "text":
      return m.status === "seen" ? <span className="receipt">{app.seenText || "Seen"}</span> : null;
    default:
      return null;
  }
}
