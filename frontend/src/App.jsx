import { useCallback, useEffect, useRef, useState } from "react";
import { api, whenSignedOut } from "./api";
import { load, save } from "./storage";
import Sidebar from "./components/Sidebar";
import Trainer from "./components/Trainer";
import Chat from "./components/Chat";
import Auth from "./components/Auth";
import ResetPassword from "./components/ResetPassword";
import TermsGate from "./components/TermsGate";
import ErrorBoundary from "./components/ErrorBoundary";

// A "forgot password" email links to  /app#reset=<token>.  The part after # never reaches the server.
// The landing page's "Start Chatting" opens  /app?signup=1, which starts on "Make an account".
const startsOnSignUp = () => new URLSearchParams(window.location.search).has("signup");
const resetTokenInUrl = () => (window.location.hash.match(/^#reset=([A-Za-z0-9_-]{20,})$/) || [])[1] || null;
const clearUrlHash = () => window.history.replaceState(null, "", window.location.pathname + window.location.search);

/* App: works out who's signed in, owns the list of people, and decides which screen to show.
     a reset link    -> "Choose a new password" (whoever is signed in)
     nobody signed in -> the sign-in page
     mode "chat"      -> Chat Mode with the selected person
     mode "train"     -> Training Mode (trainId = null means "creating someone new")
   If the server can't be reached it keeps retrying quietly and shows "Reconnecting…" meanwhile. */
export default function App() {
  const [user, setUser] = useState(undefined);   // undefined = still asking, null = signed out
  const [persons, setPersons] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [mode, setMode] = useState("chat");
  const [trainId, setTrainId] = useState(null);
  const [offline, setOffline] = useState(false);
  const [resetToken, setResetToken] = useState(resetTokenInUrl);
  const [authStart, setAuthStart] = useState(null);   // { mode, email } to open the sign-in page on "forgot"
  const retry = useRef(null);
  useEffect(() => {            // a reset link opened in a tab that already has imy open
    const onHash = () => { const t = resetTokenInUrl(); if (t) setResetToken(t); };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  // Light or dark: remembered in this browser.
  const [theme, setTheme] = useState(() => load("imy.theme", "dark"));   // dark is the brand look (same as the landing page)
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    save("imy.theme", theme);
  }, [theme]);

  const refresh = useCallback(async () => {
    clearTimeout(retry.current);
    try {
      const list = await api.persons();
      setPersons(Array.isArray(list) ? list : []);
      setOffline(false);
    } catch (e) {
      if (/sign in/i.test(e.message)) return;      // the session ended; the sign-in page is coming
      setOffline(true);
      retry.current = setTimeout(refresh, 4000);   // try again by itself; no need to reload the page
    }
  }, []);

  // Any request coming back "not signed in" drops the whole app back to the sign-in page,
  // rather than leaving a half-working screen behind.
  const signedOut = useCallback(() => {
    clearTimeout(retry.current);
    setUser(null); setPersons([]); setActiveId(null); setMode("chat"); setOffline(false);
  }, []);

  useEffect(() => { whenSignedOut(signedOut); }, [signedOut]);

  // After signing in, ask again who we are: the sign-in reply is short, and /me also says whether this
  // account has agreed to the current Terms (terms_current) and which AI choice it made.
  const signedIn = useCallback((u) => {
    api.me().then((r) => setUser(r.user || u)).catch(() => setUser(u));
  }, []);

  useEffect(() => {
    let gone = false;
    api.me()
      .then((r) => !gone && setUser(r.user))
      .catch(() => !gone && setUser(null));        // can't reach imy: show sign-in rather than a blank page
    const quiet = (e) => console.warn("[imy] unhandled:", e.reason);   // a stray failed request must not break the page
    window.addEventListener("unhandledrejection", quiet);
    return () => { gone = true; window.removeEventListener("unhandledrejection", quiet); };
  }, []);

  useEffect(() => {
    if (!user) return;
    refresh();
    const back = () => refresh();
    window.addEventListener("online", back);
    return () => { window.removeEventListener("online", back); clearTimeout(retry.current); };
  }, [user, refresh]);

  const active = persons.find((p) => p.id === activeId) || null;

  const startTraining = (id = null) => { setTrainId(id); setMode("train"); };
  const finishTraining = async (id) => { await refresh(); setActiveId(id); setMode("chat"); };
  const remove = async (id) => {
    try { await api.deletePerson(id); } catch (e) { alert(e.message); return; }
    if (activeId === id) setActiveId(null);
    refresh();
  };

  if (resetToken) {
    return (
      <ResetPassword
        token={resetToken}
        onSignedIn={(u) => { clearUrlHash(); setResetToken(null); signedIn(u); }}
        onNewLink={(email) => { clearUrlHash(); setResetToken(null); setAuthStart({ mode: "forgot", email: email || "" }); }}
      />
    );
  }
  // Still asking the server who's signed in: nothing yet, so the page doesn't flash the wrong screen.
  if (user === undefined) return <div className="app booting" aria-busy="true" />;
  if (!user) {
    return <Auth key={authStart ? "forgot" : "start"} onSignedIn={(u) => { setAuthStart(null); signedIn(u); }}
      startMode={authStart?.mode || (startsOnSignUp() ? "up" : "in")} startEmail={authStart?.email} />;
  }

  // The Terms changed since this account agreed: read them and agree again before going on.
  if (user.terms_current === false) {
    return <TermsGate onAccepted={() => setUser({ ...user, terms_current: true })} onSignedOut={signedOut} />;
  }

  return (
    <div className="app">
      <Sidebar
        persons={persons}
        activeId={mode === "chat" ? activeId : null}
        onSelect={(id) => { setActiveId(id); setMode("chat"); }}
        onNew={() => startTraining(null)}
        theme={theme}
        onTheme={setTheme}
        user={user}
        onSignedOut={signedOut}
      />
      <main className="main">
        {offline && <div className="banner warn" role="status">Can’t reach imy right now. Reconnecting…</div>}
        <ErrorBoundary resetKey={`${mode}-${activeId}-${trainId}`}>
          {mode === "train" ? (
            <Trainer
              key={trainId ?? "new"}
              personId={trainId}
              theme={theme}
              onTheme={setTheme}
              onDone={finishTraining}
              onSaved={refresh}
              onCancel={() => { refresh(); setMode("chat"); }}
            />
          ) : active ? (
            <Chat key={active.id} person={active} persons={persons} theme={theme} onChanged={refresh}
              onBack={() => setActiveId(null)} onEdit={() => startTraining(active.id)} onDelete={() => remove(active.id)} />
          ) : (
            <Welcome hasPeople={persons.length > 0} onNew={() => startTraining(null)} />
          )}
        </ErrorBoundary>
      </main>
    </div>
  );
}

function Welcome({ hasPeople, onNew }) {
  return (
    <div className="welcome">
      <h1>{hasPeople ? "Who do you want to talk to?" : "Bring someone’s way of talking back"}</h1>
      <p>
        {hasPeople
          ? "Pick a person from the list, or set up someone new."
          : "Answer a few questions about them and upload your chats. imy learns how they write and lets you talk with that style again."}
      </p>
      <button className="btn primary" onClick={onNew}>Create new person</button>
      <p className="fine">imy copies writing patterns from the messages you give it. It isn’t them. When an AI helps with a reply, only the text needed for that reply is sent to it.</p>
    </div>
  );
}
