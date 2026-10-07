/* ThemeSwitch: the Light / Dark choice. Used in the sidebar and in setup ("Where you talk"). */
export default function ThemeSwitch({ theme, onChange }) {
  return (
    <div className="theme-switch" role="radiogroup" aria-label="Colour mode">
      {["light", "dark"].map((t) => (
        <button key={t} type="button" role="radio" aria-checked={theme === t}
          className={theme === t ? "on" : ""} onClick={() => onChange(t)}>
          {t === "light" ? "☀ Light" : "☾ Dark"}
        </button>
      ))}
    </div>
  );
}
