// platforms.js - the apps a chat can be dressed up as.
// Colours for each (light + dark) live in styles.css under .skin-<id>; this file says which
// buttons sit in the header and around the message box. Icon names refer to components/Icon.jsx.
//   headIcons  decorative buttons in the header     menuIcon  the one that opens imy's own options
//   leftIcons  left of the message box              inField   inside the message box, on the right
//   idleIcon   where Send sits while the box is empty         sendIcon  once you've typed something
export const PLATFORMS = [
  { id: "messenger", label: "Messenger", status: "Active now", placeholder: "Aa", dot: true,
    headIcons: ["phone", "video"], menuIcon: "info",
    leftIcons: ["plus", "camera", "image", "mic"], inField: "smile", idleIcon: "like", sendIcon: "send" },
  { id: "imessage", label: "iMessage", status: "", placeholder: "iMessage",
    headIcons: [], menuIcon: "video",
    leftIcons: ["plus"], inField: "mic", idleIcon: "up", sendIcon: "up" },
  { id: "instagram", label: "Instagram", status: "Active now", placeholder: "Message...",
    headIcons: ["phone", "video"], menuIcon: "info",
    leftIcons: ["camera"], inField: "image", idleIcon: "mic", sendIcon: "send" },
  { id: "whatsapp", label: "WhatsApp", status: "online", placeholder: "Message",
    headIcons: ["video", "phone"], menuIcon: "more",
    leftIcons: ["plus"], inField: "camera", idleIcon: "mic", sendIcon: "send" },
  { id: "telegram", label: "Telegram", status: "online", placeholder: "Message",
    headIcons: ["phone"], menuIcon: "more",
    leftIcons: ["clip"], inField: "smile", idleIcon: "mic", sendIcon: "send" },
  { id: "", label: "Somewhere else", status: "", placeholder: "" },   // the plain imy look
];

export const platformOf = (id) => PLATFORMS.find((p) => p.id === id) || PLATFORMS[PLATFORMS.length - 1];
