import { useEffect, useState } from "react";
import { api } from "../api";
import { Upload, Deck, Review, allQuestions, knowPercent, filled } from "./Trainer";

/* TrainPanel: Train mode inside a chat. Everything that teaches imy about this person, without leaving them:
     Add chats  -> upload a conversation file or as many screenshots as you like
     Questions  -> the one-question-per-screen interview, starting where you left off
     Review     -> check who-said-what (also lists what you taught with 👍 / Fix in the chat)
     More       -> edit the basics, merge a duplicate profile into this one */
const TABS = [["chats", "Add chats"], ["questions", "Questions"], ["review", "Review"], ["more", "More"]];

export default function TrainPanel({ person, persons, onChanged, onEditProfile, onBackToChat }) {
  const pid = person.id;
  const [tab, setTab] = useState(person.person_message_count ? "questions" : "chats");
  const [questions, setQuestions] = useState(null);
  const [answers, setAnswers] = useState(person.answers || {});
  const [shots, setShots] = useState([]);
  const [flagged, setFlagged] = useState(new Set());
  const [guesses, setGuesses] = useState(null);
  const [theirs, setTheirs] = useState(person.person_message_count);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [mergeId, setMergeId] = useState("");

  const name = (answers.q2 || answers.q1 || person.name || "them").split(" ")[0];
  const pct = knowPercent(questions, answers, theirs);
  const deep = allQuestions(questions).filter((q) => q.stage === "deep");
  const answered = deep.filter((q) => filled(answers, q.id)).length;
  const fixes = shots.filter((s) => s.platform === "correction").reduce((n, s) => n + s.messages.filter((m) => m.speaker === "user").length, 0);
  const screenshots = shots.filter((s) => s.url).length;
  const others = persons.filter((p) => p.id !== pid);

  useEffect(() => {
    api.questions().then(setQuestions);
    api.screenshots(pid).then(setShots);
  }, [pid]);
  useEffect(() => {
    if (tab === "questions" && guesses === null) api.guesses(pid).then(setGuesses).catch(() => setGuesses([]));
  }, [tab, pid, guesses]);

  async function refreshCounts() {
    const p = await api.person(pid);
    setTheirs(p.person_message_count);
    onChanged();
  }
  const reload = async () => { setShots(await api.screenshots(pid)); await refreshCounts(); };
  const addShots = (list) => {
    setShots((s) => [...s, ...list]);
    setFlagged((f) => new Set([...f, ...list.filter((x) => x.needs_review).map((x) => x.id)]));
    refreshCounts();
  };
  async function save(next = answers) {
    setBusy(true);
    try { const p = await api.updatePerson(pid, next); setTheirs(p.person_message_count); onChanged(); }
    finally { setBusy(false); }
  }
  async function merge() {
    const other = others.find((p) => String(p.id) === mergeId);
    if (!other || !confirm(`Move everything learned about “${other.nickname || other.name}” into ${name}, then remove that profile?`)) return;
    setBusy(true);
    try {
      const p = await api.merge(pid, other.id);
      setAnswers(p.answers); setMergeId(""); await reload();
      setNote(`Merged. ${name} now has ${p.person_message_count} of their messages.`);
    } catch (e) { setNote(e.message); }
    finally { setBusy(false); }
  }

  const cards = [
    ...(guesses || []).map((g) => ({ ...g, id: "guess_" + g.id, type: "guess", section: "imy noticed…" })),
    ...deep,
  ];

  return (
    <div className="train-panel">
      <div className="train-head">
        <div className="meter">
          <span>imy knows {name} <strong>{pct}%</strong></span>
          <div className="bar"><div style={{ width: pct + "%" }} /></div>
        </div>
        <p className="muted small train-stats">
          {theirs} of their messages · {screenshots} screenshot{screenshots === 1 ? "" : "s"} · {answered} of {deep.length} questions · {fixes} fix{fixes === 1 ? "" : "es"} from chat
        </p>
      </div>

      <div className="train-tabs" role="tablist">
        {TABS.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={"chip" + (tab === id ? " on" : "")} onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>
      {note && <div className="banner">{note}</div>}

      <div className="train-body">
        {tab === "chats" && (
          <Upload pid={pid} shots={shots} onAdded={addShots} onImported={reload} personName={name}
            footer={<button className="btn primary" onClick={() => setTab("review")}>Review what was read</button>} />
        )}
        {tab === "questions" && questions && guesses !== null && (
          <Deck key={"deck" + guesses.length} cards={cards} answers={answers} setAnswers={setAnswers} personName={name}
            onSave={save} busy={busy} finishLabel="Done" onBackFirst={() => setTab("chats")}
            onLater={async () => { await save(); onBackToChat(); }}
            onFinish={async (a) => { await save(a); setNote("All questions done. Thanks, that helps a lot."); setTab("chats"); }} />
        )}
        {tab === "review" && (
          <Review shots={shots} flagged={flagged} personName={name} reload={reload}
            footer={<button className="btn primary" onClick={onBackToChat}>Back to chat</button>} />
        )}
        {tab === "more" && (
          <section className="panel">
            <h3 className="sub-head">Name, nicknames and where you talk</h3>
            <p className="muted small">Change their name, what you call each other, or the app the chat looks like.</p>
            <button className="btn" onClick={onEditProfile}>Edit the basics</button>

            <h3 className="sub-head">Set up the same person twice?</h3>
            <p className="muted small">Merge the other profile into {name}: its screenshots, chats, answers and fixes move here, then it’s removed.</p>
            {others.length === 0 ? <p className="muted small">There’s no one else to merge.</p> : (
              <div className="merge-row">
                <select value={mergeId} onChange={(e) => setMergeId(e.target.value)} aria-label="Profile to merge in">
                  <option value="">Choose a profile…</option>
                  {others.map((p) => <option key={p.id} value={p.id}>{p.nickname || p.name} ({p.person_message_count} messages)</option>)}
                </select>
                <button className="btn" disabled={!mergeId || busy} onClick={merge}>Merge into {name}</button>
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
