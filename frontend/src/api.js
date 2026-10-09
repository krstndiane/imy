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
    throw new Error(detail || FRIENDLY[res.status] || `Request failed (${res.status}).`);
  }
  if (data === null) throw new Error("imy sent back something unexpected. Try again.");
  return data;
}

async function request(url, options = {}, timeoutMs = 30000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await handle(await fetch(url, { ...options, signal: ctrl.signal }));
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

export const api = {
  health: () => send("GET", "/api/health", null, 8000),
  status: () => send("GET", "/api/status"),
  questions: () => send("GET", "/api/questions"),
  persons: () => send("GET", "/api/persons"),
  person: (id) => send("GET", `/api/persons/${id}`),
  createPerson: (answers, platform) => send("POST", "/api/persons", { answers, platform }),
  updatePerson: (id, answers, platform) => send("PUT", `/api/persons/${id}`, { answers, platform }),
  deletePerson: (id) => send("DELETE", `/api/persons/${id}`),
  setLlm: (id, use_llm) => send("PUT", `/api/persons/${id}/settings`, { use_llm }),
  setLook: (id, look) => send("PUT", `/api/persons/${id}/look`, look),   // { platform?, mode?, colors? }

  screenshots: (id) => send("GET", `/api/persons/${id}/screenshots`),
  upload: (id, files) => form(`/api/persons/${id}/screenshots`, { files }, 180000),
  importText: (id, text, person_name) => send("POST", `/api/persons/${id}/import-text`, { text, person_name }),
  // Chat export files: step 1 reads the file and lists the chats in it, step 2 saves the one you pick.
  importFile: (id, file) => form(`/api/persons/${id}/import-file`, { file }, 300000),
  importCommit: (id, body) => send("POST", `/api/persons/${id}/import-commit`, body, 120000),
  flipScreenshot: (sid) => send("POST", `/api/screenshots/${sid}/flip`),
  reread: (sid) => send("POST", `/api/screenshots/${sid}/reread`, null, 180000),   // read a screenshot again with the latest reader
  deleteScreenshot: (sid) => send("DELETE", `/api/screenshots/${sid}`),

  editMessage: (mid, patch) => send("PUT", `/api/messages/${mid}`, patch),
  deleteMessage: (mid) => send("DELETE", `/api/messages/${mid}`),
  mergeUp: (mid) => send("POST", `/api/messages/${mid}/merge-up`),

  chat: (id) => send("GET", `/api/persons/${id}/chat`),
  say: (id, message) => send("POST", `/api/persons/${id}/chat`, { message }, 45000),
  sayMany: (id, messages) => send("POST", `/api/persons/${id}/chat`, { messages }, 45000),   // a burst, answered together
  clearChat: (id) => send("DELETE", `/api/persons/${id}/chat`),
  style: (id) => send("GET", `/api/persons/${id}/style`),
  guesses: (id) => send("GET", `/api/persons/${id}/guesses`),   // "imy noticed…" cards for the interview
  teach: (id, prompt, reply) => send("POST", `/api/persons/${id}/teach`, { prompt, reply }),   // 👍 / Fix from the chat
  merge: (id, fromId) => send("POST", `/api/persons/${id}/merge`, { from_id: fromId }),
};
