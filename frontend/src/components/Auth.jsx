import { useState } from "react";
import { api } from "../api";

/* The first screen when nobody is signed in.

   One form that does both jobs - make an account or sign in - because two separate pages for four
   fields is more work for the person than it's worth. Making an account also asks you to confirm
   you're 18 or over (the AI services imy uses set that limit in their own terms) and to agree to
   the Terms; the server records which version and when.

   "Forgot password?" swaps the form for a single email field. imy emails a link that lets you choose
   a new password (ResetPassword.jsx); nobody is ever emailed a password. The answer is the same
   whether or not that email has an account, so this page can't be used to find out who uses imy. */
export default function Auth({ onSignedIn, startMode = "in", startEmail = "" }) {
  const [mode, setMode] = useState(startMode);   // "in" = sign in, "up" = make an account, "forgot"
  const [email, setEmail] = useState(startEmail);
  const [password, setPassword] = useState("");
  const [adult, setAdult] = useState(false);
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sent, setSent] = useState(null);       // after "email me a link": { email, minutes, printed }
  const making = mode === "up";
  const forgot = mode === "forgot";

  async function submit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (forgot) {
        const out = await api.forgotPassword(email.trim());
        setSent({ email: email.trim(), minutes: out.minutes || 30, printed: !!out.printed });
        setBusy(false);
        return;
      }
      const out = making
        ? await api.signUp(email.trim(), password, adult)
        : await api.signIn(email.trim(), password);
      onSignedIn(out.user);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  const go = (next) => { setMode(next); setError(""); setSent(null); setBusy(false); };

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

        {sent ? (
          <div className="sent" role="status">
            <h1>Check your email</h1>
            {sent.printed ? (
              <p className="muted">
                Email isn’t set up on this computer yet, so instead of emailing it, imy printed the link in the
                Terminal window where it’s running. Open that link to choose a new password.
              </p>
            ) : (
              <p className="muted">
                If there’s an imy account for <b>{sent.email}</b>, a link to choose a new password is on its way.
                It works once, for {sent.minutes} minutes.
              </p>
            )}
            {!sent.printed && <p className="small muted">Nothing after a few minutes? Check spam, or make sure that’s the email you signed up with.</p>}
            <button type="button" className="btn primary block" onClick={() => go("in")}>Back to sign in</button>
            <p className="small muted swap">
              <button type="button" className="linky" onClick={() => setSent(null)}>Send it again</button>
            </p>
          </div>
        ) : (
          <>
            <h1>{forgot ? "Forgot your password?" : making ? "Make an account" : "Welcome back"}</h1>
            <p className="muted">
              {forgot
                ? "Type the email you signed up with. imy will email you a link to choose a new password."
                : making
                  ? "Your people and your chats are kept to this account. Nobody else can see them."
                  : "Sign in to get back to your people."}
            </p>

            <form onSubmit={submit}>
              <label className="field">
                <span className="small muted">Email</span>
                <input type="email" value={email} autoComplete="email" required autoFocus
                  placeholder="you@example.com" onChange={(e) => setEmail(e.target.value)} />
              </label>

              {!forgot && (
                <div className="field">
                  <span className="pw-head">
                    <label className="small muted" htmlFor="auth-pw">Password</label>
                    {!making && (
                      <button type="button" className="linky small" onClick={() => go("forgot")}>Forgot password?</button>
                    )}
                  </span>
                  <span className="pw">
                    <input id="auth-pw" type={show ? "text" : "password"} value={password} required minLength={making ? 8 : undefined}
                      autoComplete={making ? "new-password" : "current-password"}
                      placeholder={making ? "at least 8 characters" : ""}
                      onChange={(e) => setPassword(e.target.value)} />
                    <button type="button" className="btn small ghost" onClick={() => setShow(!show)}>
                      {show ? "Hide" : "Show"}
                    </button>
                  </span>
                </div>
              )}

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
                {busy ? "One moment…" : forgot ? "Email me a link" : making ? "Create account" : "Sign in"}
              </button>
            </form>

            <p className="small muted swap">
              {forgot ? (
                <>Remembered it?{" "}<button type="button" className="linky" onClick={() => go("in")}>Sign in</button></>
              ) : (
                <>
                  {making ? "Already have an account?" : "New here?"}{" "}
                  <button type="button" className="linky" onClick={() => go(making ? "in" : "up")}>
                    {making ? "Sign in" : "Make one"}
                  </button>
                </>
              )}
            </p>
          </>
        )}
      </div>

      <p className="fine auth-fine">
        imy copies writing patterns from the messages you give it. Replies are written by AI. It isn't them.{" "}
        <a href="/privacy.html" target="_blank" rel="noreferrer">What imy stores</a>{" · "}
        <a href="/terms.html" target="_blank" rel="noreferrer">Terms</a>
      </p>
    </div>
  );
}
