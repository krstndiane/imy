import { useState } from "react";
import PdynMark from "./PdynMark";
import { api } from "../api";

/* Shown once to anyone who agreed to an older version of the Terms (the server says so with
   `terms_current: false`, see auth.TERMS_VERSION). They read the new Terms and privacy page, agree,
   and carry on. They can also leave instead: sign out, or download their data from the account menu
   after agreeing (or ask to delete it). Nothing about their chats changes while they decide. */
export default function TermsGate({ onAccepted, onSignedOut }) {
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function accept(e) {
    e.preventDefault();
    setBusy(true); setError("");
    try { await api.acceptTerms(); onAccepted(); } catch (err) { setError(err.message); setBusy(false); }
  }
  async function leave() {
    setBusy(true);
    try { await api.signOut(); } catch { /* signing out of a page that can't reach the server is fine */ }
    onSignedOut();
  }

  return (
    <div className="auth">
      <div className="auth-card">
        <h1>We’ve updated our Terms and privacy page</h1>
        <p className="muted">
          What’s new: a section on cookies, your rights over your data, a button to download everything imy holds
          about you, and a switch to stop an outside AI from helping write replies. Please read them and agree to keep using imy.
        </p>
        <form onSubmit={accept}>
          <label className="check">
            <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
            <span>
              I agree to the <a href="/terms.html" target="_blank" rel="noreferrer">Terms</a> and the{" "}
              <a href="/privacy.html" target="_blank" rel="noreferrer">privacy page</a>.
            </span>
          </label>
          {error && <p className="banner warn" role="alert">{error}</p>}
          <button className="btn primary block" disabled={busy || !agreed}>{busy ? "One moment…" : "Agree and continue"}</button>
        </form>
        <p className="small muted swap">
          Not now? <button type="button" className="linky" onClick={leave} disabled={busy}>Sign out</button>
        </p>
      </div>
      <PdynMark height={26} />
    </div>
  );
}
