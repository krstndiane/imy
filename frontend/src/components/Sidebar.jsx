export function initials(name = "") {
  return name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() || "").join("") || "?";
}

export default function Sidebar({ persons, activeId, onSelect, onNew }) {
  return (
    <aside className="sidebar">
      <div className="brand">imy</div>
      <button className="btn primary block" onClick={onNew}>Create new person</button>
      <nav className="people" aria-label="People">
        {persons.length === 0 && <p className="muted small">No one yet. Create a person to get started.</p>}
        {persons.map((p) => (
          <button key={p.id} className={"person-btn" + (p.id === activeId ? " active" : "")} onClick={() => onSelect(p.id)}>
            <span className="avatar">{initials(p.nickname || p.name)}</span>
            <span className="person-text">
              <strong>{p.nickname || p.name}</strong>
              <small>{p.person_message_count} of their messages learned</small>
            </span>
          </button>
        ))}
      </nav>
    </aside>
  );
}
