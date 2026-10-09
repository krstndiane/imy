/* The panels behind the chat's ⋮ menu - the same things the real apps keep there:

     Profile      their photo and yours, nicknames, chat colour, the quick-reaction emoji
     Chat look    which app the chat is dressed as, light/dark, screenshot colours
     Media        every photo and file shared in this chat
     Memory       what they remember from your conversations

   They're plain panels that slide in under the header, so they work the same on a phone. */
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { PLATFORMS } from "../platforms";
import Avatar from "./Avatar";
import Icon from "./Icon";

/* Messenger-ish palette: a few good colours instead of a colour picker nobody enjoys. */
export const COLORS = [
  ["", "Default"], ["#0084ff", "Blue"], ["#44bec7", "Teal"], ["#ffc300", "Yellow"], ["#fa3c4c", "Red"],
  ["#d696bb", "Pink"], ["#7646ff", "Purple"], ["#20cef5", "Sky"], ["#67b868", "Green"], ["#ff7ca8", "Rose"],
  ["#e68a3c", "Orange"], ["#13cf13", "Lime"],
];
const EMOJIS = ["👍", "❤️", "😂", "🥹", "🔥", "😭", "✨", "🫶", "💙", "😘"];

function PhotoPicker({ label, src, name, onPick, onClear, busy }) {
  const input = useRef();
  return (
    <div className="photo-pick">
      <Avatar src={src} name={name} className="big" />
      <div>
        <strong className="small">{label}</strong>
        <div className="row">
          <button className="btn small" disabled={busy} onClick={() => input.current?.click()}>
            {src ? "Change photo" : "Add photo"}
          </button>
          {src && <button className="btn small ghost" disabled={busy} onClick={onClear}>Remove</button>}
        </div>
      </div>
      <input ref={input} type="file" accept="image/*" hidden
        onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; f && onPick(f); }} />
    </div>
  );
}

export function ProfilePanel({ person, onSaved, onClose, onError, onOpen }) {
  const [them, setThem] = useState(person.nick_them || "");
  const [me, setMe] = useState(person.nick_me || "");
  const [busy, setBusy] = useState(false);
  const theme = person.theme || {};
  const real = person.name;

  useEffect(() => { setThem(person.nick_them || ""); setMe(person.nick_me || ""); }, [person.id]);

  const run = async (fn) => {
    setBusy(true);
    try { await fn(); onSaved(); } catch (e) { onError(e.message); } finally { setBusy(false); }
  };
  const saveNicks = () => run(() => api.setProfile(person.id, { nick_them: them.trim(), nick_me: me.trim() }));

  return (
    <div className="style-panel profile-panel">
      <PhotoPicker label={`${real}'s photo`} src={person.avatar} name={person.nickname || real} busy={busy}
        onPick={(f) => run(() => api.setAvatar(person.id, f, "person"))}
        onClear={() => run(() => api.clearAvatar(person.id, "person"))} />
      <PhotoPicker label="Your photo" src={person.my_avatar} name="You" busy={busy}
        onPick={(f) => run(() => api.setAvatar(person.id, f, "me"))}
        onClear={() => run(() => api.clearAvatar(person.id, "me"))} />

      <p className="small"><strong>Nicknames</strong></p>
      <label className="field">
        <span className="small muted">What you call {real}</span>
        <input value={them} maxLength={40} placeholder={real} onChange={(e) => setThem(e.target.value)} onBlur={saveNicks}
          onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()} />
      </label>
      <label className="field">
        <span className="small muted">What {real} calls you</span>
        <input value={me} maxLength={40} placeholder="Your nickname" onChange={(e) => setMe(e.target.value)} onBlur={saveNicks}
          onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()} />
      </label>
      <p className="fine">They'll use your nickname in replies, the way a real person would.</p>

      <p className="small"><strong>Chat colour</strong></p>
      <div className="swatches">
        {COLORS.map(([c, label]) => (
          <button key={label} title={label} aria-label={label}
            className={"swatch-btn" + ((theme.color || "") === c ? " on" : "") + (c ? "" : " none")}
            style={c ? { background: c } : undefined} disabled={busy}
            onClick={() => run(() => api.setProfile(person.id, { color: c }))} />
        ))}
      </div>

      <p className="small"><strong>Quick reaction</strong></p>
      <div className="chips">
        {EMOJIS.map((e) => (
          <button key={e} className={"chip emoji" + ((theme.emoji || "👍") === e ? " on" : "")} disabled={busy}
            onClick={() => run(() => api.setProfile(person.id, { emoji: e }))}>{e}</button>
        ))}
      </div>

      <div className="row wrap">
        <button className="btn small" onClick={() => onOpen("media")}>Media & files{person.media_count ? ` (${person.media_count})` : ""}</button>
        <button className="btn small" onClick={() => onOpen("memory")}>What they remember{person.remembers ? ` (${person.remembers})` : ""}</button>
        <button className="btn small" onClick={() => onOpen("look")}>Chat look</button>
      </div>
      <div className="actions"><button className="btn small" onClick={onClose}>Done</button></div>
    </div>
  );
}

/* "Chat look": which app the chat looks like, light/dark, and whether to copy your screenshots' colours. */
export function LookPanel({ look, skin, onChange, onClose }) {
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

/* Everything shared in this chat, newest first - like "Media, files and links". */
export function MediaPanel({ person, onClose, onError, onOpenPhoto }) {
  const [items, setItems] = useState(null);
  useEffect(() => { api.mediaList(person.id).then(setItems).catch((e) => { setItems([]); onError(e.message); }); }, [person.id]);
  const photos = (items || []).filter((m) => m.kind === "image");
  const files = (items || []).filter((m) => m.kind !== "image");
  return (
    <div className="style-panel media-panel">
      <p className="small"><strong>Media & files</strong></p>
      {!items ? <p className="muted">Loading…</p> : !items.length ? (
        <p className="muted small">Nothing shared yet. Use the 📎 button in the message box to send a photo.</p>
      ) : (
        <>
          <div className="media-grid">
            {photos.map((m) => (
              <button key={m.id} className="media-tile" onClick={() => onOpenPhoto(m)} title={m.description || m.filename}>
                <img src={m.url} alt={m.description || m.filename} loading="lazy" />
              </button>
            ))}
          </div>
          {files.map((m) => (
            <a key={m.id} className="file-row" href={m.url} target="_blank" rel="noreferrer">
              <Icon name="file" /><span>{m.filename}</span>
            </a>
          ))}
        </>
      )}
      <div className="actions"><button className="btn small" onClick={onClose}>Done</button></div>
    </div>
  );
}

/* What they remember from your chats (kept by notes.py on the server). */
export function MemoryPanel({ person, onClose, onError, onChanged }) {
  const [notes, setNotes] = useState(null);
  const load = () => api.memory(person.id).then((r) => setNotes(r.notes || [])).catch((e) => { setNotes([]); onError(e.message); });
  useEffect(load, [person.id]);
  async function forget() {
    if (!confirm(`Make ${person.nickname || person.name} forget everything from your chats? Their learned style stays.`)) return;
    try { await api.clearMemory(person.id); setNotes([]); onChanged(); } catch (e) { onError(e.message); }
  }
  return (
    <div className="style-panel">
      <p className="small"><strong>What they remember from your chats</strong></p>
      {!notes ? <p className="muted">Loading…</p> : notes.length ? <ul>{notes.map((n, i) => <li key={i}>{n}</li>)}</ul> : (
        <p className="muted small">Nothing yet. After a few messages imy notes down the things worth remembering, so they can bring them up later.</p>
      )}
      <div className="actions">
        {notes?.length > 0 && <button className="btn small ghost danger" onClick={forget}>Forget everything</button>}
        <button className="btn small" onClick={onClose}>Done</button>
      </div>
    </div>
  );
}

/* Tap a photo to see it full size. */
export function PhotoViewer({ media, onClose }) {
  useEffect(() => {
    const esc = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  if (!media) return null;
  return (
    <div className="photo-viewer" role="dialog" aria-label="Photo" onClick={onClose}>
      <button className="ic-btn close" aria-label="Close"><Icon name="close" /></button>
      <img src={media.url} alt={media.description || media.filename} onClick={(e) => e.stopPropagation()} />
      {media.description && <p className="fine">{media.description}</p>}
    </div>
  );
}
