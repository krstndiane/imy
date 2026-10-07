import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import Sidebar from "./components/Sidebar";
import Trainer from "./components/Trainer";
import Chat from "./components/Chat";

/* App: owns the list of people and decides which screen to show.
     mode "chat"  -> Chat Mode with the selected person
     mode "train" -> Training Mode (trainId = null means "creating someone new") */
export default function App() {
  const [persons, setPersons] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [mode, setMode] = useState("chat");
  const [trainId, setTrainId] = useState(null);
  const [error, setError] = useState("");
  // Light or dark: remembered in this browser; the first visit follows the computer's setting.
  const [theme, setTheme] = useState(() => localStorage.getItem("imy.theme")
    || (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("imy.theme", theme);
  }, [theme]);

  const refresh = useCallback(async () => {
    try {
      setPersons(await api.persons());
    } catch (e) {
      setError("Can't reach the imy backend. Is it running on port 8000?");
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const active = persons.find((p) => p.id === activeId) || null;

  const startTraining = (id = null) => { setTrainId(id); setMode("train"); };
  const finishTraining = async (id) => { await refresh(); setActiveId(id); setMode("chat"); };
  const remove = async (id) => {
    await api.deletePerson(id);
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
        {error && <div className="banner error">{error}</div>}
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
          <Chat key={active.id} person={active} onChanged={refresh} onBack={() => setActiveId(null)} onEdit={() => startTraining(active.id)} onDelete={() => remove(active.id)} />
        ) : (
          <Welcome hasPeople={persons.length > 0} onNew={() => startTraining(null)} />
        )}
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
          : "Answer a few questions about them and upload screenshots of your chats. imy learns how they write and lets you talk with that style again."}
      </p>
      <button className="btn primary" onClick={onNew}>Create new person</button>
      <p className="fine">imy copies writing patterns from the messages you give it. It isn’t them, and your chats and screenshots stay on this computer unless you turn on Claude mode.</p>
    </div>
  );
}
