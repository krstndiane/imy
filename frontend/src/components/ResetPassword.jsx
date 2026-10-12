import { useEffect, useState } from "react";
import PdynMark from "./PdynMark";
import { api } from "../api";

/* "Choose a new password" - what the link in a "forgot password" email opens.

   The link looks like  https://imy.site/app#reset=<token>. App.jsx reads the token and shows this
   page instead of everything else. It asks the server first whether the link still works, so an
   old or used link says so straight away, before anyone types a new password into it.

   Saving signs out every other device, signs in this one, and the server emails a short notice. */
export default function ResetPassword({ token, onSignedIn, onNewLink }) {
  const [state, setState] = useState("checking");   // checking | ready | dead
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let gone = false;
    api.checkReset(token)
      .then((r) => { if (!gone) { setEmail(r.email); setState("ready"); } })
      .catch((e) => { if (!gone) { setError(e.message); setState("dead"); } });
    return () => { gone = true; };
  }, [token]);

  async function submit(e) {
    e.preventDefault();
    setError("");
    if (password !== again) { setError("The two passwords don’t match."); return; }
    setBusy(true);
    try {
      const out = await api.resetPassword(token, password);
      onSignedIn(out.user);
    } catch (err) {
      setError(err.message);
      setBusy(false);
      if (/expired|already used/i.test(err.message)) setState("dead");
    }
  }

  return (
    <div className="auth">
      <div className="auth-card">
        <span className="logo-pill big">i miss you</span>

        {state === "checking" && (
          <>
            <h1>One moment…</h1>
            <p className="muted">Checking your link.</p>
          </>
        )}

        {state === "dead" && (
          <>
            <h1>This link doesn’t work any more</h1>
            <p className="muted">{error || "It has expired or was already used."} Links work once, for 30 minutes.</p>
            <button type="button" className="btn primary block" onClick={() => onNewLink(email)}>Email me a new link</button>
          </>
        )}

        {state === "ready" && (
          <>
            <h1>Choose a new password</h1>
            <p className="muted">For <b>{email}</b>. Every other device will be signed out.</p>
            <form onSubmit={submit}>
              {/* Lets password managers save the new password under the right account. */}
              <input type="email" value={email} autoComplete="username" readOnly hidden />
              <label className="field">
                <span className="small muted">New password</span>
                <span className="pw">
                  <input type={show ? "text" : "password"} value={password} required minLength={8} autoFocus
                    autoComplete="new-password" placeholder="at least 8 characters"
                    onChange={(e) => setPassword(e.target.value)} />
                  <button type="button" className="btn small ghost" aria-label={show ? "Hide password" : "Show password"} onClick={() => setShow(!show)}>
                    {show ? "Hide" : "Show"}
                  </button>
                </span>
              </label>
              <label className="field">
                <span className="small muted">Type it again</span>
                <input type={show ? "text" : "password"} value={again} required minLength={8}
                  autoComplete="new-password" onChange={(e) => setAgain(e.target.value)} />
              </label>
              {error && <p className="banner warn" role="alert">{error}</p>}
              <button className="btn primary block" disabled={busy}>
                {busy ? "One moment…" : "Save and sign in"}
              </button>
            </form>
          </>
        )}

        <p className="small muted swap">
          <a href="/app" className="linky">Back to sign in</a>
        </p>
      </div>
      <PdynMark height={26} />
    </div>
  );
}
