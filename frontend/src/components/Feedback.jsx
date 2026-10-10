import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api";

/* "Send feedback": a short note to the people running imy. It goes to the admin page's inbox
   (and their email). It never includes anyone's chats, and your email is only shown to them if you
   tick "you can email me about this". */
const KINDS = [
  { id: "idea", label: "💡 Idea" },
  { id: "problem", label: "🐞 Something’s wrong" },
  { id: "love", label: "💙 Something I like" },
  { id: "other", label: "💬 Other" },
];
const FACES = ["😞", "🙁", "😐", "🙂", "😍"];
const MAX = 2000;

export default function Feedback({ user, page = "app" }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState("idea");
  const [rating, setRating] = useState(null);
  const [message, setMessage] = useState("");
  const [canReply, setCanReply] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);
  const [contact, setContact] = useState("");   // the site's inbox (IMY_CONTACT_EMAIL), for "prefer email?"
  const box = useRef(null);
  const opener = useRef(null);

  const close = () => {
    setOpen(false);
    setError("");
    if (sent) { setSent(false); setMessage(""); setRating(null); setKind("idea"); }
    opener.current?.focus();
  };

  useEffect(() => {
    if (!open || contact) return;
    let live = true;
    api.status().then((s) => live && setContact(s.contact || "")).catch(() => {});
    return () => { live = false; };
  }, [open]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => box.current?.querySelector("textarea, button.primary")?.focus(), 30);
    const onKey = (e) => { if (e.key === "Escape") close(); };
    window.addEventListener("keydown", onKey);
    return () => { clearTimeout(t); window.removeEventListener("keydown", onKey); };
  }, [open, sent]);   // eslint-disable-line react-hooks/exhaustive-deps

  async function send(e) {
    e.preventDefault();
    if (!message.trim()) { setError("Write a few words first."); return; }
    setBusy(true); setError("");
    try {
      await api.sendFeedback({ kind, rating, message: message.trim(), can_reply: canReply, page });
      setSent(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button ref={opener} type="button" className="btn small ghost block feedback-open" onClick={() => setOpen(true)}>
        Send feedback
      </button>
      {open && createPortal(
        <div className="modal-back" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="fb-title" ref={box}>
            {sent ? (
              <div className="fb-thanks">
                <p className="fb-big" aria-hidden="true">💙</p>
                <h2 id="fb-title">Thank you</h2>
                <p className="muted">The people running imy read every note.{canReply ? " If they have a question, they’ll email you." : ""}</p>
                {contact && <p className="fine">Anything else? <a href={`mailto:${contact}`}>{contact}</a></p>}
                <div className="actions"><button type="button" className="btn primary" onClick={close}>Close</button></div>
              </div>
            ) : (
              <form onSubmit={send}>
                <h2 id="fb-title">Send feedback</h2>
                <p className="small muted">Tell us what to fix, what to add, or what you like. Only the people running imy see this, and it never includes your chats.</p>

                <p className="small fb-label">What’s it about?</p>
                <div className="fb-kinds" role="radiogroup" aria-label="What it's about">
                  {KINDS.map((k) => (
                    <button key={k.id} type="button" role="radio" aria-checked={kind === k.id}
                      className={"fb-chip" + (kind === k.id ? " on" : "")} onClick={() => setKind(k.id)}>{k.label}</button>
                  ))}
                </div>

                <p className="small fb-label">How’s imy so far? <span className="muted">(optional)</span></p>
                <div className="fb-faces" role="radiogroup" aria-label="Rating from 1 to 5">
                  {FACES.map((f, i) => (
                    <button key={i} type="button" role="radio" aria-checked={rating === i + 1} aria-label={`${i + 1} of 5`}
                      className={"fb-face" + (rating === i + 1 ? " on" : "")} onClick={() => setRating(rating === i + 1 ? null : i + 1)}>{f}</button>
                  ))}
                </div>

                <label className="field">
                  <span className="small fb-label">Your note</span>
                  <textarea rows={5} maxLength={MAX} value={message} onChange={(e) => setMessage(e.target.value)}
                    placeholder={kind === "problem" ? "What happened, and what did you expect?" : "Type here…"} />
                  <span className="fine fb-count muted">{message.length} / {MAX}</span>
                </label>

                <label className="check">
                  <input type="checkbox" checked={canReply} onChange={(e) => setCanReply(e.target.checked)} />
                  <span>You can email me about this{user?.email ? ` at ${user.email}` : ""}</span>
                </label>

                {error && <p className="banner warn" role="alert">{error}</p>}
                {contact && (
                  <p className="fine fb-contact">Prefer email? Write to us at <a href={`mailto:${contact}?subject=${encodeURIComponent("imy feedback")}`}>{contact}</a></p>
                )}
                <div className="actions">
                  <button type="button" className="btn" onClick={close}>Cancel</button>
                  <button className="btn primary" disabled={busy || !message.trim()}>{busy ? "Sending…" : "Send"}</button>
                </div>
              </form>
            )}
          </div>
        </div>,
        document.body,   // outside the sidebar, whose blur would otherwise trap the pop-up inside it
      )}
    </>
  );
}
