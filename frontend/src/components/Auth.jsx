import { useState } from "react";
import { api } from "../api";

/* The first screen when nobody is signed in.

   One form that does both jobs - make an account or sign in - because two separate pages for four
   fields is more work for the person than it's worth. Making an account also asks you to confirm
   you're 18 or over (the AI services imy uses set that limit in their own terms) and to agree to
   the Terms; the server records which version and when. */
export default function Auth({ onSignedIn }) {
  const [mode, setMode] = useState("in");      // "in" = sign in, "up" = make an account
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [adult, setAdult] = useState(false);
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const making = mode === "up";

  async function submit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const out = making
        ? await api.signUp(email.trim(), password, adult)
        : await api.signIn(email.trim(), password);
      onSignedIn(out.user);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  const swap = () => { setMode(making ? "in" : "up"); setError(""); };

  return (
    <div className="auth">
      <div className="auth-card">
        <span className="logo-pill big">
          i miss you
          <i aria-hidden="true">
            <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
              <circle cx="10.5" cy="13" r="7.5" /><path d="M7.7 14.7a3.6 3.6 0 0 0 5.600 0M8.200 11v.1M12.800 11v.1M19 2v6M16 5h6" />
            </svg>
          </i>
        </span>
        <h1>{making ? "Make an account" : "Welcome back"}</h1>
        <p className="muted">
          {making
            ? "Your people and your chats are kept to this account. Nobody else can see them."
            : "Sign in to get back to your people."}
        </p>

        <form onSubmit={submit}>
          <label className="field">
            <span className="small muted">Email</span>
            <input type="email" value={email} autoComplete="email" required autoFocus
              placeholder="you@example.com" onChange={(e) => setEmail(e.target.value)} />
          </label>

          <label className="field">
            <span className="small muted">Password</span>
            <span className="pw">
              <input type={show ? "text" : "password"} value={password} required minLength={making ? 8 : undefined}
                autoComplete={making ? "new-password" : "current-password"}
                placeholder={making ? "at least 8 characters" : ""}
                onChange={(e) => setPassword(e.target.value)} />
              <button type="button" className="btn small ghost" onClick={() => setShow(!show)}>
                {show ? "Hide" : "Show"}
              </button>
            </span>
          </label>

          {making && (
            <label className="check">
              <input type="checkbox" checked={adult} onChange={(e) => setAdult(e.target.checked)} />
              <span>
                I'm 18 or over, and I agree to the{" "}
                <a href="/terms.html" target="_blank" rel="noreferrer">Terms</a> and the{" "}
                <a href="/privacy.html" target="_blank" rel="noreferrer">privacy page</a>.
              </span>
            </label>
          )}

          {error && <p className="banner warn" role="alert">{error}</p>}

          <button className="btn primary block" disabled={busy || (making && !adult)}>
            {busy ? "One moment…" : making ? "Create account" : "Sign in"}
          </button>
        </form>

        <p className="small muted swap">
          {making ? "Already have an account?" : "New here?"}{" "}
          <button type="button" className="linky" onClick={swap}>
            {making ? "Sign in" : "Make one"}
          </button>
        </p>
      </div>

      <p className="fine auth-fine">
        imy copies writing patterns from the messages you give it. Replies are written by AI. It isn't them.{" "}
        <a href="/privacy.html" target="_blank" rel="noreferrer">What imy stores</a>{" · "}
        <a href="/terms.html" target="_blank" rel="noreferrer">Terms</a>
      </p>
    </div>
  );
}
