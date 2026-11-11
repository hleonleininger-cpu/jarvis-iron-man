// JARVIS Web-Client: dünner Client für /v1/chat (Server-Sent Events über fetch).
// Alles wird mit textContent eingefügt, nie als HTML: Antworten stammen vom Modell.
"use strict";

const log = document.getElementById("log");
const form = document.getElementById("ask");
const input = document.getElementById("message");
const send = document.getElementById("send");
const TOKEN_KEY = "jarvis-token";
let sessionId = null;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  log.appendChild(node);
  log.scrollTop = log.scrollHeight;
  return node;
}

function headers(json) {
  const result = json ? { "Content-Type": "application/json" } : {};
  let token = null;
  try { token = localStorage.getItem(TOKEN_KEY); } catch (_) { /* privater Modus */ }
  if (token) result.Authorization = "Bearer " + token;
  return result;
}

async function call(method, path, body) {
  let response = await fetch(path, { method, headers: headers(body !== undefined), body: body === undefined ? undefined : JSON.stringify(body) });
  if (response.status === 401) {
    const token = window.prompt("Zugangstoken für JARVIS (jarvis secrets generate api_token):");
    if (!token) return response;
    try { localStorage.setItem(TOKEN_KEY, token.trim()); } catch (_) { /* nur für diese Anfrage */ }
    response = await fetch(path, { method, headers: { ...headers(body !== undefined), Authorization: "Bearer " + token.trim() }, body: body === undefined ? undefined : JSON.stringify(body) });
  }
  return response;
}

function confirmCard(data) {
  const card = element("div", "confirm");
  const text = document.createElement("p");
  text.textContent = data.preview + "\n\n" + data.question;
  const actions = document.createElement("div");
  actions.className = "actions";
  for (const [label, approve] of [["Ja", true], ["Nein", false]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    if (approve) button.className = "yes";
    button.addEventListener("click", async () => {
      for (const b of actions.querySelectorAll("button")) b.disabled = true;
      const response = await call("POST", "/v1/confirm/" + encodeURIComponent(data.id), { approve });
      button.textContent = response.ok ? label + " ✓" : label + " (zu spät)";
    });
    actions.appendChild(button);
  }
  card.append(text, actions);
}

function handle(event, data, state) {
  switch (event) {
    case "delta":
      if (!state.answer) state.answer = element("div", "msg jarvis", "");
      state.answer.textContent += data.text;
      log.scrollTop = log.scrollHeight;
      break;
    case "tool":
      element("div", "info").appendChild(Object.assign(document.createElement("span"), { className: "tool", textContent: "Werkzeug: " + data.name }));
      state.answer = null;  // Text nach dem Werkzeug als eigene Blase
      break;
    case "notice":
      element("div", "info", data.message);
      break;
    case "confirm":
      confirmCard(data);
      state.answer = null;
      break;
    case "error":
      element("div", "info error", "Fehler: " + data.message);
      break;
    case "done":
      sessionId = data.session_id;
      break;
  }
}

async function ask(message) {
  element("div", "msg me", message);
  const body = { message };
  if (sessionId) body.session_id = sessionId;
  const response = await call("POST", "/v1/chat", body);
  if (!response.ok || !response.body) {
    element("div", "info error", "Fehler: Anfrage abgelehnt (HTTP " + response.status + ").");
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const state = { answer: null };
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = "message", data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
        // Zeilen mit ":" am Anfang sind Lebenszeichen (Ping) und werden übergangen.
      }
      if (data) handle(event, JSON.parse(data), state);
    }
  }
}

form.addEventListener("submit", async (submitEvent) => {
  submitEvent.preventDefault();
  const message = input.value.trim();
  if (!message || send.disabled) return;
  input.value = "";
  send.disabled = true;
  try {
    await ask(message);
  } catch (error) {
    element("div", "info error", "Fehler: Verbindung zu JARVIS unterbrochen.");
  } finally {
    send.disabled = false;
    input.focus();
  }
});

input.addEventListener("keydown", (keyEvent) => {
  if (keyEvent.key === "Enter" && !keyEvent.shiftKey && !keyEvent.isComposing) {
    keyEvent.preventDefault();
    form.requestSubmit();
  }
});

document.getElementById("reset").addEventListener("click", () => {
  const previous = sessionId;
  sessionId = null;
  log.replaceChildren();
  element("div", "info", "Neues Gespräch.");
  input.focus();
  // Verlauf auf dem Server im Hintergrund vergessen; die Anzeige wartet nicht darauf.
  if (previous) call("DELETE", "/v1/sessions/" + encodeURIComponent(previous)).catch(() => {});
});

fetch("/health").then((r) => r.json()).then((h) => {
  document.getElementById("model").textContent = "Modell " + h.model;
}).catch(() => {});
input.focus();
