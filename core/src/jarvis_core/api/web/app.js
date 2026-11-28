// JARVIS Web-Client: dünner Client für /v1/chat (Server-Sent Events über fetch).
// Alles wird mit textContent eingefügt, nie als HTML: Antworten stammen vom Modell.
"use strict";

const log = document.getElementById("log");
const form = document.getElementById("ask");
const input = document.getElementById("message");
const send = document.getElementById("send");
const stop = document.getElementById("stop");
let running = null;  // AbortController der laufenden Anfrage
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

// Schlankes Markdown (Absätze, Listen, Überschriften, **fett**, *kursiv*, `code`), nur als
// DOM-Knoten mit textContent gebaut: Modelltext wird nie als HTML ausgewertet.
const INLINE = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\s][^*\n]*\*)/g;

function inline(parent, text) {
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    if (match.index > last) parent.appendChild(document.createTextNode(text.slice(last, match.index)));
    const token = match[0];
    const tag = token.startsWith("**") ? "strong" : token.startsWith("`") ? "code" : "em";
    const cut = tag === "strong" ? 2 : 1;
    parent.appendChild(Object.assign(document.createElement(tag), { textContent: token.slice(cut, -cut) }));
    last = match.index + token.length;
  }
  if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
}

function renderMarkdown(container, text) {
  container.replaceChildren();
  let list = null;
  for (const line of text.split("\n")) {
    const item = line.match(/^\s*(?:([-*•])|(\d+)[.)])\s+(.*)$/);
    if (item) {
      const tag = item[1] ? "ul" : "ol";
      if (!list || list.tagName.toLowerCase() !== tag) list = container.appendChild(document.createElement(tag));
      inline(list.appendChild(document.createElement("li")), item[3]);
      continue;
    }
    list = null;
    if (!line.trim()) continue;
    const heading = line.match(/^#{1,4}\s+(.*)$/);
    const paragraph = container.appendChild(document.createElement("p"));
    if (heading) inline(paragraph.appendChild(document.createElement("strong")), heading[1]);
    else inline(paragraph, line);
  }
}

async function call(method, path, body, signal) {
  let response = await fetch(path, { method, signal, headers: headers(body !== undefined), body: body === undefined ? undefined : JSON.stringify(body) });
  if (response.status === 401) {
    const token = window.prompt("Zugangstoken für JARVIS (jarvis secrets generate api_token):");
    if (!token) return response;
    try { localStorage.setItem(TOKEN_KEY, token.trim()); } catch (_) { /* nur für diese Anfrage */ }
    response = await fetch(path, { method, signal, headers: { ...headers(body !== undefined), Authorization: "Bearer " + token.trim() }, body: body === undefined ? undefined : JSON.stringify(body) });
  }
  return response;
}

const openCards = new Map();  // Rückfrage-ID -> Karte (Anfrage-Strom und /v1/events zeigen dieselbe nur einmal)

function confirmCard(data) {
  if (openCards.has(data.id)) return;
  const card = element("div", "confirm");
  openCards.set(data.id, card);
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

function closeCard(data) {
  const card = openCards.get(data.id);
  openCards.delete(data.id);
  if (card && !card.querySelector("button:disabled")) {  // noch unbeantwortet: erledigt anderswo
    for (const b of card.querySelectorAll("button")) b.disabled = true;
    const note = document.createElement("p");
    note.textContent = data.approved === null ? "(Zeit abgelaufen)" : "(anderswo beantwortet)";
    card.appendChild(note);
  }
}

function handle(event, data, state) {
  if (state.waiting) {  // erstes Ereignis: "denkt nach" entfernen
    state.waiting.remove();
    state.waiting = null;
  }
  switch (event) {
    case "delta":
      if (!state.answer) {
        state.answer = element("div", "msg jarvis");
        state.text = "";
      }
      state.text += data.text;
      renderMarkdown(state.answer, state.text);
      log.scrollTop = log.scrollHeight;
      break;
    case "tool":
      element("div", "info").appendChild(Object.assign(document.createElement("span"), { className: "tool", textContent: "Werkzeug: " + data.name }));
      state.answer = null;  // Text nach dem Werkzeug als eigene Blase
      break;
    case "notice":
      element("div", "info", data.message);
      break;
    case "progress":
      element("div", "info", data.kind + ": " + data.text);
      state.answer = null;
      break;
    case "confirm":
      confirmCard(data);
      state.answer = null;
      break;
    case "error":
      element("div", "info error", "Fehler: " + data.message);
      break;
    case "done":
      if (sessionId && data.neu) {
        element("div", "info", "Hinweis: JARVIS kannte das bisherige Gespräch nicht mehr (z. B. nach einem Neustart). Diese Antwort beruht nur auf der letzten Nachricht.");
      }
      sessionId = data.session_id;
      break;
  }
}

async function ask(message) {
  element("div", "msg me", message);
  const body = { message };
  if (sessionId) body.session_id = sessionId;
  const response = await call("POST", "/v1/chat", body, running.signal);
  if (!response.ok || !response.body) {
    for (const node of log.querySelectorAll(".thinking")) node.remove();
    element("div", "info error", "Fehler: Anfrage abgelehnt (HTTP " + response.status + ").");
    return;
  }
  const state = { answer: null, text: "", waiting: element("div", "info thinking", "JARVIS denkt nach …") };
  await readEvents(response, (event, data) => handle(event, data, state));
}

// Liest Server-Sent Events aus einer fetch-Antwort und ruft onEvent(name, daten) je Ereignis.
async function readEvents(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
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
      if (data) onEvent(event, JSON.parse(data));
    }
  }
}

// Erinnerungen (ADR-013): dauerhaft zuhören, bei Abbruch mit wachsendem Abstand neu verbinden.
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const TITLE = document.title;

function showReminder(data) {
  element("div", "reminder", "⏰ Erinnerung: " + data.text);
  if (document.hidden) document.title = "⏰ " + TITLE;
  try {
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification("JARVIS – Erinnerung", { body: data.text });
    }
  } catch (_) { /* z. B. iOS ohne installierte Web-App */ }
}

async function listenReminders() {
  let delay = 2000;
  for (;;) {
    try {
      const response = await fetch("/v1/events", { headers: headers(false) });
      if (response.status === 401) {
        await sleep(10000);  // Token kommt mit der ersten Chat-Anfrage
        continue;
      }
      if (!response.ok || !response.body) throw new Error("HTTP " + response.status);
      delay = 2000;
      await readEvents(response, (event, data) => {
        if (event === "reminder") showReminder(data);
        else if (event === "confirm") confirmCard(data);  // Rückfrage von einem anderen Gerät
        else if (event === "confirm_done") closeCard(data);
      });
    } catch (_) { /* Server weg oder neu gestartet */ }
    await sleep(delay);
    delay = Math.min(delay * 2, 60000);
  }
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) document.title = TITLE;
});

form.addEventListener("submit", async (submitEvent) => {
  submitEvent.preventDefault();
  const message = input.value.trim();
  if (!message || send.disabled) return;
  try {  // Erlaubnis für Systemmeldungen geht nur auf eine Nutzeraktion hin
    if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
  } catch (_) { /* nicht unterstützt */ }
  input.value = "";
  input.style.height = "auto";
  send.disabled = true;
  running = new AbortController();
  stop.hidden = false;
  try {
    await ask(message);
  } catch (error) {
    const aborted = error && error.name === "AbortError";
    element("div", aborted ? "info" : "info error", aborted ? "Abgebrochen." : "Fehler: Verbindung zu JARVIS unterbrochen.");
  } finally {
    for (const node of log.querySelectorAll(".thinking")) node.remove();
    running = null;
    stop.hidden = true;
    send.disabled = false;
    input.focus();
  }
});

// Not-Aus (ADR-018): stoppt serverseitig ALLES, auch Anfragen anderer Geräte und offene Rückfragen.
async function panic() {
  try {
    // Zeitlimit: hängt der Server, käme sonst nie eine Antwort und der Knopf wirkte tot.
    const response = await call("POST", "/v1/stop", undefined, AbortSignal.timeout(5000));
    const data = response.ok ? await response.json() : null;
    element("div", data ? "info" : "info error", data ? (data.abgebrochen ? "Not-Aus: alles gestoppt." : "Not-Aus: es lief nichts.") : "Not-Aus fehlgeschlagen (" + response.status + ").");
  } catch (_) {
    element("div", "info error", "Not-Aus: der Server antwortet nicht. In der Eingabeaufforderung hilft: jarvis stop");
  }
}
document.getElementById("panic").addEventListener("click", panic);
document.addEventListener("keydown", (keyEvent) => {
  if ((keyEvent.ctrlKey || keyEvent.metaKey) && keyEvent.key === ".") { keyEvent.preventDefault(); panic(); }
});

stop.addEventListener("click", () => {
  if (running) running.abort();  // der Server bricht die Anfrage dann ebenfalls ab
});

// Eingabefeld wächst mit dem Text (bis 40 % der Höhe, dann scrollt es).
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = input.scrollHeight + "px";
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
listenReminders();
