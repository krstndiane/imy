// api.js - one tiny wrapper around fetch so components stay readable.
async function handle(res) {
  if (!res.ok) {
    let message = res.statusText;
    try { message = (await res.json()).detail || message; } catch { /* not JSON */ }
    throw new Error(message);
  }
  return res.json();
}
const send = (method, url, body) =>
  fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  }).then(handle);

export const api = {
  questions: () => send("GET", "/api/questions"),
  persons: () => send("GET", "/api/persons"),
  person: (id) => send("GET", `/api/persons/${id}`),
  createPerson: (answers, platform) => send("POST", "/api/persons", { answers, platform }),
  updatePerson: (id, answers, platform) => send("PUT", `/api/persons/${id}`, { answers, platform }),
  deletePerson: (id) => send("DELETE", `/api/persons/${id}`),
  setLlm: (id, use_llm) => send("PUT", `/api/persons/${id}/settings`, { use_llm }),

  screenshots: (id) => send("GET", `/api/persons/${id}/screenshots`),
  upload: (id, files) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return fetch(`/api/persons/${id}/screenshots`, { method: "POST", body: form }).then(handle);
  },
  importText: (id, text, person_name) => send("POST", `/api/persons/${id}/import-text`, { text, person_name }),
  flipScreenshot: (sid) => send("POST", `/api/screenshots/${sid}/flip`),
  deleteScreenshot: (sid) => send("DELETE", `/api/screenshots/${sid}`),

  editMessage: (mid, patch) => send("PUT", `/api/messages/${mid}`, patch),
  deleteMessage: (mid) => send("DELETE", `/api/messages/${mid}`),
  mergeUp: (mid) => send("POST", `/api/messages/${mid}/merge-up`),

  chat: (id) => send("GET", `/api/persons/${id}/chat`),
  say: (id, message) => send("POST", `/api/persons/${id}/chat`, { message }),
  clearChat: (id) => send("DELETE", `/api/persons/${id}/chat`),
  style: (id) => send("GET", `/api/persons/${id}/style`),
};
