// platforms.js - the apps a chat can be dressed up as, and how each one behaves.
// Colours (light + dark) live in styles.css under .skin-<id>; colours measured from your own screenshots
// override them (see Chat.jsx). This file says what each app shows and where:
//   headIcons  decorative buttons in the header     menuIcon  the one that opens imy's own options
//   leftIcons  left of the message box              inField   inside the message box, on the right
//   idleIcon   where Send sits while the box is empty         sendIcon  once you've typed something
//   status     under the name ("Active now")        typingIn  "thread": a bubble with dots, "header": "typing…" under the name
//   receipt    how your messages show they were read: "avatar" (tiny photo), "text" ("Seen"), "ticks" (✓✓ in the bubble),
//              "delivered" ("Delivered" -> "Read 9:41 PM")
//   seps       time separators: "time" (a time when there was a pause) or "day" (a date chip when the day changes)
export const PLATFORMS = [
  { id: "messenger", label: "Messenger", status: "Active now", placeholder: "Message", dot: true,
    headIcons: ["phone", "video"], menuIcon: "info", typingIn: "thread", receipt: "avatar", seps: "time",
    leftIcons: ["plus", "camera", "image", "mic"], inField: "smile", idleIcon: "like", sendIcon: "send" },
  { id: "imessage", label: "iMessage", status: "", placeholder: "iMessage",
    headIcons: [], menuIcon: "video", typingIn: "thread", receipt: "delivered", seps: "time",
    leftIcons: ["plus"], inField: "mic", idleIcon: "up", sendIcon: "up" },
  { id: "instagram", label: "Instagram", status: "Active now", placeholder: "Message...",
    headIcons: ["phone", "video"], menuIcon: "info", typingIn: "thread", receipt: "text", seenText: "Seen", seps: "time",
    leftIcons: ["camera"], inField: "image", idleIcon: "mic", sendIcon: "send" },
  { id: "whatsapp", label: "WhatsApp", status: "online", placeholder: "Message",
    headIcons: ["video", "phone"], menuIcon: "more", typingIn: "header", typingText: "typing…", receipt: "ticks", seps: "day",
    leftIcons: ["plus"], inField: "camera", idleIcon: "mic", sendIcon: "send" },
  { id: "telegram", label: "Telegram", status: "online", placeholder: "Message",
    headIcons: ["phone"], menuIcon: "more", typingIn: "header", typingText: "typing…", receipt: "ticks", seps: "day",
    leftIcons: ["clip"], inField: "smile", idleIcon: "mic", sendIcon: "send" },
  { id: "viber", label: "Viber", status: "online", placeholder: "Type a message...",
    headIcons: ["phone", "video"], menuIcon: "more", typingIn: "thread", receipt: "text", seenText: "Seen", seps: "day",
    leftIcons: ["plus"], inField: "smile", idleIcon: "mic", sendIcon: "send" },
  { id: "", label: "Somewhere else", status: "", placeholder: "", typingIn: "thread", receipt: "text", seenText: "Seen", seps: "time" },
];

export const platformOf = (id) => PLATFORMS.find((p) => p.id === id) || PLATFORMS[PLATFORMS.length - 1];
