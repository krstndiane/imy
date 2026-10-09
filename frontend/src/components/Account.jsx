import { useEffect, useState } from "react";
import { api } from "../api";

/* The account button at the bottom of the sidebar: who you're signed in as, signing out,
   changing your password, and deleting everything.

   "Delete everything" asks for the password and the typed word DELETE, because it really does
   remove every person, message and photo, and there is no undo.

   The menu also shows how many AI replies you've used today. Each account gets a fair share of the
   site's daily AI allowance (IMY_AI_DAILY_PER_ACCOUNT); past it, replies still come, written by imy
   from their messages instead. */
export default function Account({ user, onSignedOut }) {
  const [open, setOpen] = useState(null);   // null | "menu" | "password" | "delete"
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [ai, setAi] = useState(null);       // {used, limit} for today, fetched when the menu opens

  useEffect(() => {
    if (open !== "menu") return;
    let live = true;
    api.me().then((r) => live && setAi(r.user?.ai_today || null)).catch(() => {});
    return () => { live = false; };
  }, [open]);

  const close = () => { setOpen(null); setError(""); setNote(""); setOldPw(""); setNewPw(""); setPw(""); setConfirm(""); };

  async function run(fn, done) {
    setBusy(true); setError("");
    try { await fn(); done?.(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  const signOut = () => run(() => api.signOut(), onSignedOut);
  const changePassword = (e) => {
    e.preventDefault();
    run(() => api.changePassword(oldPw, newPw), () => { close(); setNote(""); });
  };
  const deleteEverything = (e) => {
    e.preventDefault();
    run(() => api.deleteAccount(pw, confirm), onSignedOut);
  };

  return (
    <div className="account">
      {open === "password" && (
        <form className="style-panel" onSubmit={changePassword}>
          <p className="small"><strong>Change password</strong></p>
          <label className="field">
            <span className="small muted">Current password</span>
            <input type="password" value={oldPw} autoComplete="current-password" required
              onChange={(e) => setOldPw(e.target.value)} />
          </label>
          <label className="field">
            <span className="small muted">New password</span>
            <input type="password" value={newPw} autoComplete="new-password" required minLength={8}
              placeholder="at least 8 characters" onChange={(e) => setNewPw(e.target.value)} />
          </label>
          <p className="fine">This signs you out everywhere else.</p>
          {error && <p className="banner warn" role="alert">{error}</p>}
          <div className="actions">
            <button type="button" className="btn small" onClick={close}>Cancel</button>
            <button className="btn small primary" disabled={busy}>{busy ? "Saving…" : "Change it"}</button>
          </div>
        </form>
      )}

      {open === "delete" && (
        <form className="style-panel" onSubmit={deleteEverything}>
          <p className="small"><strong>Delete everything</strong></p>
          <p className="small muted">
            This removes your account and every person you've set up — their messages, screenshots and
            photos. It can't be undone.
          </p>
          <label className="field">
            <span className="small muted">Your password</span>
            <input type="password" value={pw} autoComplete="current-password" required
              onChange={(e) => setPw(e.target.value)} />
          </label>
          <label className="field">
            <span className="small muted">Type DELETE to confirm</span>
            <input value={confirm} required placeholder="DELETE" onChange={(e) => setConfirm(e.target.value)} />
          </label>
          {error && <p className="banner warn" role="alert">{error}</p>}
          <div className="actions">
            <button type="button" className="btn small" onClick={close}>Keep my account</button>
            <button className="btn small danger" disabled={busy || confirm.trim().toUpperCase() !== "DELETE"}>
              {busy ? "Deleting…" : "Delete everything"}
            </button>
          </div>
        </form>
      )}

      {open === "menu" && (
        <div className="style-panel">
          {error && <p className="banner warn" role="alert">{error}</p>}
          {note && <p className="small">{note}</p>}
          {ai && ai.limit > 0 && (
            <div className="ai-today" aria-label={`AI replies today: ${ai.used} of ${ai.limit}`}>
              <span className="small">AI replies today <strong>{Math.min(ai.used, ai.limit)} of {ai.limit}</strong></span>
              <div className="bar"><div style={{ width: Math.min(100, (100 * ai.used) / ai.limit) + "%" }} /></div>
              <span className="fine">
                {ai.used >= ai.limit
                  ? "Used up for today. Replies still come, written by imy from their messages. Back at midnight."
                  : "Resets at midnight. After that, replies come from imy itself."}
              </span>
            </div>
          )}
          <div className="actions column">
            <button className="btn small" onClick={() => { setOpen("password"); setError(""); }}>Change password</button>
            <a className="btn small" href="/privacy.html" target="_blank" rel="noreferrer">What imy stores</a>
            <a className="btn small" href="/terms.html" target="_blank" rel="noreferrer">Terms</a>
            <button className="btn small" onClick={signOut} disabled={busy}>Sign out</button>
            <button className="btn small ghost danger" onClick={() => { setOpen("delete"); setError(""); }}>
              Delete everything
            </button>
            <button className="btn small" onClick={close}>Close</button>
          </div>
        </div>
      )}

      <button className="account-btn" onClick={() => (open ? close() : setOpen("menu"))}
        aria-expanded={open === "menu"} title={user?.email || ""}>
        <span className="account-dot" aria-hidden="true">{(user?.email || "?")[0].toUpperCase()}</span>
        <span className="account-text">
          <strong>{user?.email || "Account"}</strong>
          <small>Account & privacy</small>
        </span>
      </button>
    </div>
  );
}
