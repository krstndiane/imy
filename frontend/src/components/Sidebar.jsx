import ThemeSwitch from "./ThemeSwitch";
import Avatar, { initials } from "./Avatar";
import Account from "./Account";
import PdynMark from "./PdynMark";
import Feedback from "./Feedback";

export { initials };   // other screens still import it from here

export default function Sidebar({ persons, activeId, onSelect, onNew, theme, onTheme, user, onSignedOut }) {
  return (
    <aside className="sidebar">
      <a className="brand" href="/landing.html" title="About imy" aria-label="imy home" style={{ color: "inherit", textDecoration: "none" }}>
        <span className="logo-pill">
          i miss you
          <i aria-hidden="true">
            <svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
              <circle cx="10.5" cy="13" r="7.5" /><path d="M7.7 14.7a3.6 3.6 0 0 0 5.600 0M8.200 11v.1M12.800 11v.1M19 2v6M16 5h6" />
            </svg>
          </i>
        </span>
      </a>
      <button className="btn primary block" onClick={onNew}>Create new person</button>
      <nav className="people" aria-label="People">
        {persons.length === 0 && <p className="muted small">No one yet. Create a person to get started.</p>}
        {persons.map((p) => (
          <button key={p.id} className={"person-btn" + (p.id === activeId ? " active" : "")} onClick={() => onSelect(p.id)}>
            <Avatar src={p.avatar} name={p.nickname || p.name} />
            <span className="person-text">
              <strong>{p.nickname || p.name}</strong>
              <small>{p.person_message_count} of their messages learned</small>
            </span>
          </button>
        ))}
      </nav>
      <span className="grow" />
      <Feedback user={user} />
      <ThemeSwitch theme={theme} onChange={onTheme} />
      <Account user={user} onSignedOut={onSignedOut} />
      <div className="pdyn-side"><PdynMark height={22} /></div>
    </aside>
  );
}
