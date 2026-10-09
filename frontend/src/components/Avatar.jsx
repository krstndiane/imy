/* Avatar: the little round profile picture.
   If a photo was uploaded it shows the photo; if not, it falls back to the person's initials, and
   if the photo file ever goes missing it quietly falls back too (never a broken-image icon). */
import { useEffect, useState } from "react";

export function initials(name = "") {
  return name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() || "").join("") || "?";
}

export default function Avatar({ src, name = "", className = "", dot = false, children }) {
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [src]);
  const show = src && !broken;
  return (
    <span className={"avatar" + (show ? " has-photo" : "") + (className ? " " + className : "")}
      aria-hidden={children ? undefined : "true"}>
      {show ? <img src={src} alt="" onError={() => setBroken(true)} /> : initials(name)}
      {dot && <i className="dot" />}
      {children}
    </span>
  );
}
