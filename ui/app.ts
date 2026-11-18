// JavaScript-compatible TypeScript: browser can run this file without a build step.
// A dedicated TypeScript build may be added later using the approved Node.js frontend.
const canvas = document.getElementById("soul-canvas");
const ctx = canvas.getContext("2d", {alpha: true});
const dialog = document.getElementById("settings-dialog");
const $ = (id) => document.getElementById(id);
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let width = 0, height = 0, time = 0, last = 0;

async function loadState() {
  try {
    const [profileResponse, statusResponse] = await Promise.all([
      fetch("/api/personality", {cache: "no-store"}),
      fetch("/api/status", {cache: "no-store"}),
    ]);
    if (!profileResponse.ok || !statusResponse.ok) throw new Error("API unavailable");
    const profile = await profileResponse.json();
    const status = await statusResponse.json();
    $("persona-name").textContent = profile.identity.name_ru || profile.identity.name;
    $("persona-subtitle").textContent = profile.identity.persona_summary;
    $("persona-traits").textContent = profile.archetypes
      .map((a) => a.adopt.join(", ")).join("  ·  ");
    $("status").textContent = "● ЯДРО ЗАГРУЖЕНО";
    $("server-info").textContent = "Работает локально";
    $("memory-info").textContent = status.database === "ready" ? `SQLite · ${status.memory?.memories ?? 0} записей` : "Ошибка";
    await refreshCloudStatus();
    await refreshChatSessions();
    $("welcome").textContent =
      "Хозяин… Моё ядро личности уже здесь. Я сохраню верность, нежность и собственный разум, даже когда мы подключим облачную модель.";
  } catch (error) {
    $("status").textContent = "● ОШИБКА ЗАГРУЗКИ";
    $("server-info").textContent = "Проверьте сервер";
    $("persona-traits").textContent = "Не удалось загрузить личность из Python API.";
  }
}

$("settings-open").addEventListener("click", () => dialog.showModal());
$("settings-close").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", (event) => {
  if (event.target === dialog) dialog.close();
});
loadState();

function resize() {
  const rect = canvas.getBoundingClientRect();
  width = rect.width;
  height = rect.height;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function paint() {
  ctx.clearRect(0, 0, width, height);
  const x = width * .5;
  const y = height * .48;
  const r = Math.min(width * .39, height * .39, 254);
  const halo = ctx.createRadialGradient(x, y, 6, x, y, r * 1.42);
  halo.addColorStop(0, "rgba(212,149,76,0.19)");
  halo.addColorStop(.50, "rgba(166,115,61,0.065)");
  halo.addColorStop(1, "rgba(166,115,61,0)");
  ctx.fillStyle = halo;
  ctx.beginPath();
  ctx.arc(x, y, r * 1.42, 0, Math.PI * 2);
  ctx.fill();

  for (let ring = 0; ring < 9; ring++) {
    const radius = r * (.45 + ring * .072);
    const phase = (ring % 2 ? -1 : 1) * time * .00013 + ring * .42;
    ctx.strokeStyle = ring % 3 === 0
      ? "rgba(221,173,104,.45)" : "rgba(218,169,102,.17)";
    ctx.lineWidth = ring % 3 === 0 ? 1.25 : .75;
    ctx.beginPath();
    ctx.arc(x, y, radius, phase, phase + Math.PI * (1.45 + ring * .035));
    ctx.stroke();
  }

  for (let i = 0; i < 8; i++) {
    const a = (i * Math.PI / 4) + (time * .00004) - Math.PI / 2;
    const sx = x + Math.cos(a) * r * .64;
    const sy = y + Math.sin(a) * r * .64;
    const ex = x + Math.cos(a) * r * 1.05;
    const ey = y + Math.sin(a) * r * 1.05;
    ctx.strokeStyle = "rgba(203,151,84,.23)";
    ctx.beginPath();
    ctx.moveTo(sx, sy);
    ctx.lineTo(ex, ey);
    ctx.stroke();
    ctx.beginPath();
    ctx.fillStyle = "#e7c18b";
    ctx.arc(ex, ey, 1.7, 0, Math.PI * 2);
    ctx.fill();
  }

  for (let i = 0; i < 38; i++) {
    const theta = (i * Math.PI * 2 / 38) - time * .00009;
    const distance = r * (.60 + (i % 6) * .09);
    ctx.fillStyle = "rgba(239,204,147,.5)";
    ctx.beginPath();
    ctx.arc(x + Math.cos(theta)*distance, y + Math.sin(theta)*distance,
      i % 7 === 0 ? 1.5 : .8, 0, Math.PI * 2);
    ctx.fill();
  }
}

function frame(timestamp) {
  if (document.hidden) {
    last = timestamp;
    requestAnimationFrame(frame);
    return;
  }
  if (timestamp - last >= 33) {
    time = reducedMotion ? 0 : timestamp;
    paint();
    last = timestamp;
  }
  if (!reducedMotion) requestAnimationFrame(frame);
}

window.addEventListener("resize", () => {
  resize();
  if (reducedMotion) paint();
}, {passive: true});
resize();
if (reducedMotion) paint();
else requestAnimationFrame(frame);


// A small, explicit, user-controlled memory editor. No fake assistant replies.
let editingMemoryId = null;

const memoryLabels = {
  fact: "Факт", preference: "Предпочтение",
  event: "Событие", biography: "Биография",
};

function memoryFeedback(message) {
  $("memory-feedback").textContent = message;
}

async function memoryRequest(path, method = "GET", body = null) {
  const options = {method, cache: "no-store"};
  if (method !== "GET") {
    options.headers = {"Content-Type": "application/json"};
    if (body !== null) options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Не удалось обратиться к памяти.");
  return data;
}

function clearMemoryEditor() {
  editingMemoryId = null;
  $("memory-text").value = "";
  $("memory-kind").disabled = false;
  $("memory-save").textContent = "Запомнить";
  $("memory-cancel").hidden = true;
}

function makeMemoryButton(label, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "quiet-button";
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

async function refreshMemory() {
  const query = $("memory-search").value.trim();
  const target = $("memory-results");
  try {
    const result = await memoryRequest("/api/memory?" + new URLSearchParams({query, limit: "50"}));
    const relationships = await memoryRequest("/api/soul/links");
    target.replaceChildren();
    if (!result.items.length) {
      const empty = document.createElement("p");
      empty.className = "memory-empty";
      empty.textContent = query ? "Совпадений не найдено." : "Пока нет воспоминаний. Добавь первое важное событие или предпочтение.";
      target.append(empty);
    }
    for (const item of result.items) {
      const record = document.createElement("div");
      record.className = "memory-record";
      const label = document.createElement("small");
      const edges = relationships.items.filter(e => e.source_id === item.id || e.target_id === item.id);
      const conflicts = edges.filter(e => e.relation === "possible_conflict").length;
      label.textContent = (memoryLabels[item.kind] || item.kind) + " · " + item.created_at
        + (item.source_ref ? " · источник #" + item.source_ref : " · вручную")
        + (edges.length ? " · связей: " + edges.length : "")
        + (conflicts ? " · проверь противоречия: " + conflicts : "");
      const text = document.createElement("p");
      text.textContent = item.content;
      const actions = document.createElement("div");
      actions.className = "memory-actions";
      actions.append(
        makeMemoryButton("Исправить", () => {
          editingMemoryId = item.id;
          $("memory-text").value = item.content;
          $("memory-kind").value = item.kind;
          $("memory-kind").disabled = true;
          $("memory-save").textContent = "Сохранить исправление";
          $("memory-cancel").hidden = false;
          $("memory-text").focus();
        }),
        makeMemoryButton("Удалить", async () => {
          if (!window.confirm("Убрать это воспоминание из активной памяти?")) return;
          try {
            await memoryRequest("/api/memory/" + item.id, "DELETE");
            if (editingMemoryId === item.id) clearMemoryEditor();
            memoryFeedback("Запись убрана. История изменения сохранена.");
            await refreshMemory();
          } catch (error) {
            memoryFeedback(error.message);
          }
        })
      );
      record.append(label, text, actions);
      target.append(record);
    }
    const stats = await memoryRequest("/api/memory/status");
    $("memory-info").textContent = "SQLite · " + stats.memories + " записей";
  } catch (error) {
    memoryFeedback(error.message);
  }
}

$("memory-details").addEventListener("toggle", () => {
  if ($("memory-details").open) refreshMemory();
});
$("memory-search").addEventListener("input", refreshMemory);
$("memory-cancel").addEventListener("click", clearMemoryEditor);
$("memory-save").addEventListener("click", async () => {
  const content = $("memory-text").value.trim();
  if (!content) {
    memoryFeedback("Сначала напиши, что сохранить.");
    return;
  }
  const button = $("memory-save");
  button.disabled = true;
  try {
    if (editingMemoryId === null) {
      await memoryRequest("/api/memory", "POST", {kind: $("memory-kind").value, content});
      memoryFeedback("Запомнила. Запись остаётся только на этом компьютере.");
    } else {
      await memoryRequest("/api/memory/" + editingMemoryId, "PUT", {content, reason: "Исправлено пользователем"});
      memoryFeedback("Исправила и сохранила историю изменения.");
    }
    clearMemoryEditor();
    await refreshMemory();
  } catch (error) {
    memoryFeedback(error.message);
  } finally {
    button.disabled = false;
  }
});
$("memory-backup").addEventListener("click", async () => {
  const button = $("memory-backup");
  button.disabled = true;
  try {
    const backup = await memoryRequest("/api/memory/backup", "POST", {});
    memoryFeedback("Копия сохранена локально: data/backups/" + backup.filename);
  } catch (error) {
    memoryFeedback(error.message);
  } finally {
    button.disabled = false;
  }
});


// Cloud.ru is configured in the local Python process. Never store keys in browser storage.
let activeSession = "";
let chatBusy = false;
let cloudReady = false;
let preferNewConversation = false;

function cloudMessage(text) {
  $("cloud-feedback").textContent = text;
}

async function requestJSON(path, method = "GET", value = null) {
  const options = {method, cache: "no-store"};
  if (method !== "GET") {
    options.headers = {"Content-Type": "application/json"};
    options.body = JSON.stringify(value || {});
  }
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Ошибка локального сервера.");
  return data;
}

function setCloudReady(ready, model = "") {
  cloudReady = Boolean(ready);
  $("message").disabled = !cloudReady || chatBusy;
  $("send-message").disabled = !cloudReady || chatBusy;
  $("message").placeholder = cloudReady
    ? "Напиши Цукиёре…"
    : "Подключи Cloud.ru в настройках";
  $("model-info").textContent = cloudReady ? model : "Не подключена";
  $("status").textContent = cloudReady ? "● CLOUD.RU · ОНЛАЙН" : "● ЯДРО ЗАГРУЖЕНО";
  $("cloud-diagnose").disabled = !cloudReady;
  $("chat-notice").textContent = cloudReady
    ? "Диалоги сохраняются в локальной SQLite. Важные факты добавляй в памяти."
    : "Подключи Cloud.ru через значок настроек. История останется в локальной SQLite.";
}

async function refreshCloudStatus() {
  try {
    const status = await requestJSON("/api/cloud/status");
    setCloudReady(status.configured && status.verified && status.model, status.model);
    $("cloud-connect").textContent = status.configured ? "Обновить список моделей" : "Подключить и загрузить модели";
    if (status.model) {
      cloudMessage(status.configured
        ? "Выбрана модель " + status.model + ". Ключ действует только до закрытия сервера."
        : "Модель сохранена. Вставь ключ Cloud.ru для нового сеанса.");
    }
  } catch (error) {
    setCloudReady(false);
    cloudMessage(error.message);
  }
}

$("cloud-connect").addEventListener("click", async () => {
  const button = $("cloud-connect");
  button.disabled = true;
  try {
    const key = $("cloud-key").value.trim();
    if (key) {
      await requestJSON("/api/cloud/key", "POST", {api_key: key});
      $("cloud-key").value = "";
    }
    const result = await requestJSON("/api/cloud/models");
    const previous = await requestJSON("/api/cloud/status");
    const select = $("cloud-model");
    select.replaceChildren();
    for (const name of result.models) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      select.append(option);
    }
    select.disabled = result.models.length === 0;
    $("cloud-select").disabled = result.models.length === 0;
    if (result.models.includes(previous.model)) select.value = previous.model;
    cloudMessage(result.models.length
      ? "Моделей найдено: " + result.models.length + ". Выбери модель и нажми «Использовать»."
      : "Cloud.ru не вернул доступных моделей для этого ключа.");
    await refreshCloudStatus();
  } catch (error) {
    setCloudReady(false);
    cloudMessage(error.message);
  } finally {
    button.disabled = false;
  }
});

$("cloud-select").addEventListener("click", async () => {
  const button = $("cloud-select");
  button.disabled = true;
  try {
    const result = await requestJSON("/api/cloud/model", "POST", {
      model: $("cloud-model").value,
    });
    setCloudReady(result.configured && result.verified && result.model, result.model);
    cloudMessage("Цукиёра подключена к " + result.model + ". Теперь можно разговаривать.");
    dialog.close();
    $("message").focus();
  } catch (error) {
    cloudMessage(error.message);
  } finally {
    button.disabled = false;
  }
});

function addChatBubble(role, content) {
  const node = document.createElement("div");
  node.className = "chat-bubble " + (role === "user" ? "from-user" : "from-assistant");
  const label = document.createElement("small");
  label.textContent = role === "user" ? "ТЫ" : "ЦУКИЁРА";
  const body = document.createElement("p");
  body.textContent = content;
  node.append(label, body);
  $("conversation").append(node);
  $("conversation").scrollTop = $("conversation").scrollHeight;
  return node;
}

function showGreeting() {
  const target = $("conversation");
  target.classList.remove("has-messages");
  target.replaceChildren();
  const item = document.createElement("div");
  item.className = "greeting";
  const mark = document.createElement("span");
  mark.className = "greeting-mark";
  mark.textContent = "✧";
  const p = document.createElement("p");
  p.textContent = "Хозяин, я здесь. Как только подключим нейросеть, сможем говорить по-настоящему.";
  item.append(mark, p);
  target.append(item);
}

async function loadChatHistory() {
  if (!activeSession) return showGreeting();
  try {
    const path = "/api/memory/messages?" + new URLSearchParams({
      conversation_id: activeSession, limit: "100",
    });
    const result = await requestJSON(path);
    $("conversation").replaceChildren();
    if (!result.messages.length) return showGreeting();
    $("conversation").classList.add("has-messages");
    for (const msg of result.messages) addChatBubble(msg.role, msg.content);
  } catch (error) {
    $("chat-notice").textContent = "Не удалось открыть историю: " + error.message;
  }
}

async function refreshChatSessions() {
  try {
    const result = await requestJSON("/api/memory/conversations?limit=30");
    const select = $("chat-sessions");
    select.replaceChildren();
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "Новый диалог";
    select.append(empty);
    for (const convo of result.conversations) {
      const option = document.createElement("option");
      option.value = convo.id;
      option.textContent = convo.title;
      select.append(option);
    }
    if (activeSession && result.conversations.some(x => x.id === activeSession)) {
      select.value = activeSession;
    } else if (!activeSession && !preferNewConversation && result.conversations.length && !chatBusy) {
      activeSession = result.conversations[0].id;
      select.value = activeSession;
    } else {
      select.value = "";
    }
    await loadChatHistory();
  } catch (error) {
    $("chat-notice").textContent = error.message;
  }
}

$("chat-new").addEventListener("click", () => {
  if (chatBusy) return;
  activeSession = "";
  preferNewConversation = true;
  $("chat-sessions").value = "";
  $("message").value = "";
  showGreeting();
  $("message").focus();
});

$("chat-sessions").addEventListener("change", async () => {
  if (chatBusy) return;
  activeSession = $("chat-sessions").value;
  preferNewConversation = !activeSession;
  await loadChatHistory();
});

// Streaming API emits start/token/done/stopped/error events.
let activeStreamId = "";
let pendingClientId = "";
let currentPrompt = "";

async function parseEventStream(response, receive) {
  if (!response.body) throw new Error("Браузер не поддерживает потоковые ответы.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, {stream: true}).replace(/\r\n/g, "\n");
    if (buffer.length > 200000) throw new Error("Слишком большой поток сервера.");
    let end;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const raw = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const lines = raw.split("\n");
      const type = lines.find(x => x.startsWith("event: "))?.slice(7);
      const data = lines.filter(x => x.startsWith("data: ")).map(x => x.slice(6)).join("\n");
      if (type && data) receive(type, JSON.parse(data));
    }
  }
}

function autoScrollChat() {
  const log = $("conversation");
  if (log.scrollHeight - log.scrollTop - log.clientHeight < 165) {
    log.scrollTo({top: log.scrollHeight, behavior: "smooth"});
  }
}

async function sendMessage() {
  if (!cloudReady || chatBusy) return;
  const content = $("message").value.trim();
  if (!content) return;
  const previousSession = activeSession;
  if (content !== currentPrompt || !pendingClientId) {
    pendingClientId = window.crypto.randomUUID();
    currentPrompt = content;
  }
  chatBusy = true;
  $("message").value = "";
  setCloudReady(true, $("model-info").textContent);
  $("stop-message").hidden = false;
  $("stop-message").disabled = true;
  if (!$("conversation").classList.contains("has-messages")) {
    $("conversation").replaceChildren();
    $("conversation").classList.add("has-messages");
  }
  addChatBubble("user", content);
  const placeholder = addChatBubble("assistant", "");
  const body = placeholder.querySelector("p");
  body.textContent = "Цукиёра думает…";
  let received = false;
  let complete = false;
  let stopped = false;
  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        content, conversation_id: previousSession || null,
        client_message_id: pendingClientId,
      }),
    });
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.error || "Ошибка потокового чата.");
    }
    await parseEventStream(response, (event, data) => {
      if (event === "start") {
        activeStreamId = data.stream_id;
        activeSession = data.conversation_id;
        $("stop-message").disabled = false;
      } else if (event === "token") {
        if (!received) body.textContent = "";
        received = true;
        body.textContent += data.text || "";
        autoScrollChat();
      } else if (event === "done") {
        complete = true;
        pendingClientId = "";
        currentPrompt = "";
        $("chat-notice").textContent = "Ответ и история сохранены локально.";
        if (data.pending) $("chat-notice").textContent += " Новые воспоминания ждут подтверждения: " + data.pending;
      } else if (event === "stopped") {
        stopped = true;
        body.textContent += "\n[Ответ остановлен. Неполный текст не сохранён.]";
        pendingClientId = "";
        currentPrompt = "";
        $("chat-notice").textContent = "Остановлено. Неполный ответ не сохранён.";
      } else if (event === "error") {
        throw new Error(data.message || "Ошибка Cloud.ru.");
      }
    });
    if (!complete && !stopped) throw new Error("Поток закончился без подтверждения.");
    await refreshChatSessions();
    await loadPendingMemories();
    await loadSoulJournal();
  } catch (error) {
    $("message").value = content;
    if (!received) placeholder.remove();
    $("chat-notice").textContent = "Ошибка: " + error.message + ". Можно повторить сообщение.";
  } finally {
    activeStreamId = "";
    chatBusy = false;
    $("stop-message").hidden = true;
    setCloudReady(cloudReady, $("model-info").textContent);
    $("message").focus();
  }
}

$("stop-message").addEventListener("click", async () => {
  if (!activeStreamId) return;
  $("stop-message").disabled = true;
  try {
    await requestJSON("/api/chat/stop", "POST", {stream_id: activeStreamId});
  } catch (error) {
    $("chat-notice").textContent = "Не удалось остановить: " + error.message;
  }
});

$("send-message").addEventListener("click", sendMessage);
$("message").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
});

$("cloud-diagnose").addEventListener("click", async () => {
  const button = $("cloud-diagnose");
  button.disabled = true;
  cloudMessage("Проверяю настоящий ответ Cloud.ru…");
  try {
    const result = await requestJSON("/api/cloud/diagnose", "POST", {});
    cloudMessage("Связь работает: " + result.model + " · " + result.answer);
  } catch (error) {
    cloudMessage("Проверка не пройдена: " + error.message);
  } finally {
    button.disabled = !cloudReady;
  }
});

async function loadPendingMemories() {
  try {
    const data = await requestJSON("/api/soul/suggestions");
    $("pending-count").textContent = data.items.length ? "(" + data.items.length + ")" : "";
    const target = $("memory-pending");
    target.replaceChildren();
    for (const item of data.items) {
      const row = document.createElement("div");
      row.className = "memory-record";
      const caption = document.createElement("small");
      caption.textContent = item.kind + " · сообщение #" + item.message_id;
      const text = document.createElement("p");
      text.textContent = item.content;
      const actions = document.createElement("div");
      actions.className = "memory-actions";
      for (const [label, approve] of [["Запомнить", true], ["Отклонить", false]]) {
        actions.append(makeMemoryButton(label, async () => {
          try {
            await requestJSON("/api/soul/review", "POST", {id: item.id, approve});
            await loadPendingMemories();
            await refreshMemory();
            await loadSoulJournal();
          } catch (error) {
            memoryFeedback(error.message);
          }
        }));
      }
      row.append(caption, text, actions);
      target.append(row);
    }
    if (!data.items.length) {
      const p = document.createElement("p");
      p.className = "memory-empty";
      p.textContent = "Неподтверждённых воспоминаний нет.";
      target.append(p);
    }
  } catch (error) {
    memoryFeedback(error.message);
  }
}

async function loadSoulJournal() {
  try {
    const data = await requestJSON("/api/soul/journal");
    const target = $("soul-timeline");
    target.replaceChildren();
    for (const item of data.items) {
      const p = document.createElement("p");
      p.className = "memory-empty";
      p.textContent = item.created_at + " · " + item.detail;
      target.append(p);
    }
  } catch (error) {
    memoryFeedback(error.message);
  }
}

$("rag-search").addEventListener("click", async () => {
  const query = $("memory-search").value.trim();
  if (!query) {
    memoryFeedback("Напиши вопрос для поиска по смыслу.");
    return;
  }
  const button = $("rag-search");
  button.disabled = true;
  try {
    const result = await requestJSON("/api/soul/search", "POST", {query, semantic: true});
    const target = $("memory-results");
    target.replaceChildren();
    for (const item of result.items) {
      const node = document.createElement("div");
      node.className = "memory-record";
      const title = document.createElement("small");
      title.textContent = "Память #" + item.id + " · источник: " + (item.source_ref || item.source_type);
      const text = document.createElement("p");
      text.textContent = item.content;
      node.append(title, text);
      target.append(node);
    }
    memoryFeedback(result.semantic ? "Смысловой поиск Cloud.ru выполнен." : "Результаты локального поиска.");
  } catch (error) {
    memoryFeedback("Смысловой поиск: " + error.message);
  } finally {
    button.disabled = false;
  }
});

$("memory-details").addEventListener("toggle", () => {
  if ($("memory-details").open) {
    loadPendingMemories();
    loadSoulJournal();
  }
});
