import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { load, save } from "./storage";
import Sidebar from "./components/Sidebar";
import Trainer from "./components/Trainer";
import Chat from "./components/Chat";
import ErrorBoundary from "./components/ErrorBoundary";

/* App: owns the list of people and decides which screen to show.
     mode "chat"  -> Chat Mode with the selected person
     mode "train" -> Training Mode (trainId = null means "creating someone new")
   If the server can't be reached it keeps retrying quietly and shows "Reconnecting…" meanwhile. */
export default function App() {
  const [persons, setPersons] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [mode, setMode] = useState("chat");
  const [trainId, setTrainId] = useState(null);
  const [offline, setOffline] = useState(false);
  const retry = useRef(null);
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
    } catch {
      setOffline(true);
      retry.current = setTimeout(refresh, 4000);   // try again by itself; no need to reload the page
    }
  }, []);

  useEffect(() => {
    refresh();
    const back = () => refresh();
    window.addEventListener("online", back);
    const quiet = (e) => console.warn("[imy] unhandled:", e.reason);   // a stray failed request must not break the page
    window.addEventListener("unhandledrejection", quiet);
    return () => { window.removeEventListener("online", back); window.removeEventListener("unhandledrejection", quiet); clearTimeout(retry.current); };
  }, [refresh]);

  const active = persons.find((p) => p.id === activeId) || null;

  const startTraining = (id = null) => { setTrainId(id); setMode("train"); };
  const finishTraining = async (id) => { await refresh(); setActiveId(id); setMode("chat"); };
  const remove = async (id) => {
    try { await api.deletePerson(id); } catch (e) { alert(e.message); return; }
    if (activeId === id) setActiveId(null);
    refresh();
  };

  return (
    <div className="app">
      <Sidebar
        persons={persons}
        activeId={mode === "chat" ? activeId : null}
        onSelect={(id) => { setActiveId(id); setMode("chat"); }}
        onNew={() => startTraining(null)}
        theme={theme}
        onTheme={setTheme}
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
