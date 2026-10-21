// api.js - one small wrapper around fetch so components stay readable.
// Every call has a time limit, and every failure becomes a short, human message (never a raw
// stack trace or an HTML error page), so the screens can show it calmly instead of breaking.
const FRIENDLY = {
  413: "That file is too big.",
  429: "Slow down a little, then try again.",
  500: "Something went wrong on imy's side. Please try again.",
  502: "imy is restarting. Try again in a few seconds.",
  503: "imy is busy or restarting. Try again in a few seconds.",
  504: "imy took too long to answer. Try again.",
};

async function handle(res) {
  let data = null;
  try { data = await res.json(); } catch { /* not JSON, e.g. a hosting error page */ }
  if (!res.ok) {
    const detail = data && typeof data.detail === "string" ? data.detail : null;
    // 401 means the session ended (signed out elsewhere, or it simply expired after 30 days).
    // Tell the app so it can show the sign-in page instead of a wall of errors.
    if (res.status === 401 && !res.url.includes("/api/auth/sign")) onSignedOut();
    throw new Error(detail || FRIENDLY[res.status] || `Request failed (${res.status}).`);
  }
  if (data === null) throw new Error("imy sent back something unexpected. Try again.");
  return data;
}

// When a request comes back "not signed in", the whole app needs to know, not just the screen that
// asked. App.jsx registers here and shows the sign-in page.
let onSignedOut = () => {};
export const whenSignedOut = (fn) => { onSignedOut = fn; };

async function request(url, options = {}, timeoutMs = 30000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    // credentials: the sign-in cookie must go with every request, including from the dev server,
    // which runs on a different port and so counts as a different origin.
    return await handle(await fetch(url, { ...options, credentials: "include", signal: ctrl.signal }));
  } catch (err) {
    if (err.name === "AbortError") throw new Error("That took too long. Check your connection and try again.");
    if (err instanceof TypeError) throw new Error("Can't reach imy right now. Check your connection.");
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

const send = (method, url, body, timeoutMs) =>
  request(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  }, timeoutMs);

const form = (url, fields, timeoutMs) => {
  const data = new FormData();
  Object.entries(fields).forEach(([k, v]) => (Array.isArray(v) ? v : [v]).forEach((x) => data.append(k, x)));
  return request(url, { method: "POST", body: data }, timeoutMs);
};

function timeZone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { return ""; }
}

export const api = {
  health: () => send("GET", "/api/health", null, 8000),
  status: () => send("GET", "/api/status"),

  // Accounts. The server replies with a cookie; the browser sends it back by itself after that.
  me: () => send("GET", "/api/auth/me"),
  // One tick box on the sign-up form covers both: 18 or over, and agreeing to the Terms.
  signUp: (email, password, agreed) => send("POST", "/api/auth/signup", { email, password, is_adult: agreed, agrees: agreed }),
  signIn: (email, password) => send("POST", "/api/auth/signin", { email, password }),
  signOut: () => send("POST", "/api/auth/signout"),
  changePassword: (old_password, new_password) => send("PUT", "/api/auth/password", { old_password, new_password }),
  // Forgot password: email a link, check the link still works, then set the new password with it.
  forgotPassword: (email) => send("POST", "/api/auth/forgot", { email }),
  checkReset: (token) => send("POST", "/api/auth/reset/check", { token }),
  resetPassword: (token, password) => send("POST", "/api/auth/reset", { token, password }),
  deleteAccount: (password, confirm) => send("DELETE", "/api/auth/account", { password, confirm }),
  // The account's own choices. "Download my data" is a plain link to /api/auth/export (a zip), not a call here.
  setCloudAi: (cloud_ai) => send("PUT", "/api/auth/ai", { cloud_ai }),     // let an outside AI help write replies?
  acceptTerms: () => send("POST", "/api/auth/accept-terms"),               // agree to the Terms as they stand now
  // A note for the people running imy (shown on the admin page). { kind, rating, message, can_reply, page }
  sendFeedback: (note) => send("POST", "/api/feedback", note),
  // "Someone opened the app today" for the admin page's counts. No cookies; see backend/insights.py.
  // The time zone (e.g. Asia/Manila) is roughly where you are; it's a dot on the admin page's map.
  visit: (page) => send("POST", "/api/visit", { page, tz: timeZone() }, 5000),

  questions: () => send("GET", "/api/questions"),
  persons: () => send("GET", "/api/persons"),
  person: (id) => send("GET", `/api/persons/${id}`),
  createPerson: (answers, platform, consent) => send("POST", "/api/persons", { answers, platform, consent: !!consent }),
  updatePerson: (id, answers, platform) => send("PUT", `/api/persons/${id}`, { answers, platform }),
  deletePerson: (id) => send("DELETE", `/api/persons/${id}`),
  setLook: (id, look) => send("PUT", `/api/persons/${id}/look`, look),   // { platform?, mode?, colors? }
  // Profile picture, nicknames and chat colour: what you'd change by tapping a name in Messenger.
  setProfile: (id, patch) => send("PUT", `/api/persons/${id}/profile`, patch),   // { nick_them?, nick_me?, color?, emoji? }
  setAvatar: (id, file, who = "person") => form(`/api/persons/${id}/avatar?who=${who}`, { file }, 60000),
  clearAvatar: (id, who = "person") => send("DELETE", `/api/persons/${id}/avatar?who=${who}`),

  screenshots: (id) => send("GET", `/api/persons/${id}/screenshots`),
  upload: (id, files) => form(`/api/persons/${id}/screenshots`, { files }, 180000),
  importText: (id, text, person_name) => send("POST", `/api/persons/${id}/import-text`, { text, person_name }),
  // Chat export files: step 1 reads the file and lists the chats in it, step 2 saves the one you pick.
  importFile: (id, file) => form(`/api/persons/${id}/import-file`, { file }, 300000),
  // Between the two: the talks in the picked chat, so you choose which parts imy learns from (nothing saved).
  importPreview: (id, body) => send("POST", `/api/persons/${id}/import-preview`, body, 120000),
  importCommit: (id, body) => send("POST", `/api/persons/${id}/import-commit`, body, 120000),   // body.talks = the parts you ticked
  flipScreenshot: (sid) => send("POST", `/api/screenshots/${sid}/flip`),
  reread: (sid) => send("POST", `/api/screenshots/${sid}/reread`, null, 180000),   // read a screenshot again with the latest reader
  deleteScreenshot: (sid) => send("DELETE", `/api/screenshots/${sid}`),

  editMessage: (mid, patch) => send("PUT", `/api/messages/${mid}`, patch),
  deleteMessage: (mid) => send("DELETE", `/api/messages/${mid}`),
  mergeUp: (mid) => send("POST", `/api/messages/${mid}/merge-up`),

  // The conversation is kept on the server, newest page first; `before` loads the older messages.
  chat: (id, before) => send("GET", `/api/persons/${id}/chat` + (before ? `?before=${before}` : "")),
  say: (id, message, extra = {}) => send("POST", `/api/persons/${id}/chat`, { message, ...extra }, 45000),
  sayMany: (id, messages, extra = {}) => send("POST", `/api/persons/${id}/chat`, { messages, ...extra }, 60000),
  clearChat: (id, media = false) => send("DELETE", `/api/persons/${id}/chat` + (media ? "?media=1" : "")),
  // Photos and files shared in the chat (uploaded first, so you can see them before you send).
  uploadMedia: (id, files) => form(`/api/persons/${id}/media`, { files }, 180000),
  mediaList: (id) => send("GET", `/api/persons/${id}/media`),
  react: (mid, emoji, who = "user") => send("POST", `/api/chat-messages/${mid}/react`, { emoji, who }),
  unsend: (mid) => send("DELETE", `/api/chat-messages/${mid}`),
  memory: (id) => send("GET", `/api/persons/${id}/memory`),       // what they remember from your chats
  clearMemory: (id) => send("DELETE", `/api/persons/${id}/memory`),
  style: (id) => send("GET", `/api/persons/${id}/style`),
  guesses: (id) => send("GET", `/api/persons/${id}/guesses`),   // "imy noticed…" cards for the interview
  teach: (id, prompt, reply) => send("POST", `/api/persons/${id}/teach`, { prompt, reply }),   // 👍 / Fix from the chat
  merge: (id, fromId) => send("POST", `/api/persons/${id}/merge`, { from_id: fromId }),
};
