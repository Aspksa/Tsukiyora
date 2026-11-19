import {BrainAtlasView} from "./atlas_view.ts";
// JavaScript-compatible TypeScript: browser can run this file without a build step.
// A dedicated TypeScript build may be added later using the approved Node.js frontend.
const canvas = document.getElementById("soul-canvas");
const ctx = canvas.getContext("2d", {alpha: true});
const atlasView = new BrainAtlasView(ctx);
const dialog = document.getElementById("settings-dialog");
const $ = (id) => document.getElementById(id);
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let brainGraph = {nodes: [], edges: [], pending: 0};
let graphPositions = new Map();
let graphScale = 1;
let graphPanX = 0;
let graphPanY = 0;
let graphLoaded = false;
let graphFreshIds = new Set();
let graphPulseAt = 0;
let graphDrag = null;
let graphMoved = false;
let vectorAvailableForSearch = false;
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
    await refreshForgeChatUI();
    await Promise.all([refreshBrain(), refreshCognition()]);
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
  if (atlasView.active) {
    atlasView.draw(width,height,graphScale,graphPanX,graphPanY,reducedMotion);
    return;
  }
  ctx.clearRect(0, 0, width, height);
  const x = width * .5, y = height * .48;
  const r = Math.min(width * .39, height * .39, 254);
  const halo = ctx.createRadialGradient(x, y, 6, x, y, r * 1.3);
  halo.addColorStop(0, "rgba(212,149,76,.15)");
  halo.addColorStop(.55, "rgba(166,115,61,.04)");
  halo.addColorStop(1, "rgba(166,115,61,0)");
  ctx.fillStyle = halo;
  ctx.beginPath();
  ctx.arc(x, y, r * 1.3, 0, Math.PI * 2);
  ctx.fill();
  // Only records returned by SQLite are rendered as cells, no fake neurons.
  const counts = Array(8).fill(0);
  const kindCounts = {fact: 0, preference: 0, event: 0, biography: 0};
  const section = {fact: 0, preference: 2, event: 4, biography: 6};
  const radius = Math.max(2.1, Math.min(3.6, r / 68)) * Math.sqrt(graphScale);
  const unit = Math.max(2.1, Math.min(3.6, r / 68));
  const step = unit * 1.88;
  const wedge = .69;
  function capacity(level) {
    const dist = r * .22 + level * step;
    return Math.max(4, Math.floor(dist * wedge / (unit * 2.12)));
  }
  graphPositions = new Map();
  for (const node of brainGraph.nodes) {
    const kindIndex = kindCounts[node.kind] ?? 0;
    kindCounts[node.kind] = kindIndex + 1;
    // Large memory types expand into neighbouring sectors instead of
    // endlessly growing beyond the viewport.
    const sector = ((section[node.kind] ?? 0) +
       (kindIndex % 2) + Math.floor(kindIndex / 320) * 2) % 8;
    let slot = counts[sector]++;
    let level = 0;
    while (slot >= capacity(level) && level < 110) {
      slot -= capacity(level);
      level++;
    }
    const columns = capacity(level);
    const angle = -Math.PI / 2 + sector * Math.PI / 4 +
      ((slot + .5) / columns - .5) * wedge +
      (level % 2 ? .009 : 0);
    const distance = r * .22 + level * step;
    graphPositions.set(node.id, {
      id: node.id, kind: node.kind, preview: node.preview,
      x: x + graphPanX + Math.cos(angle) * distance * graphScale,
      y: y + graphPanY + Math.sin(angle) * distance * graphScale,
    });
  }
  for (let sector = 0; sector < 8; sector++) {
    const a = sector * Math.PI / 4 - Math.PI / 2;
    ctx.beginPath();
    ctx.moveTo(x + Math.cos(a) * r * .24, y + Math.sin(a) * r * .24);
    ctx.lineTo(x + Math.cos(a) * r * .92, y + Math.sin(a) * r * .92);
    ctx.strokeStyle = "rgba(203,151,84,.14)";
    ctx.lineWidth = .7;
    ctx.stroke();
  }
  const drawNodes = graphScale >= .85 || brainGraph.nodes.length < 300;
  if (!drawNodes) {
    // Level of detail: collapse >300 tiny cells into the eight real groups.
    for (let i = 0; i < counts.length; i++) {
      if (!counts[i]) continue;
      const a = i * Math.PI / 4 - Math.PI / 2;
      const px = x + graphPanX + Math.cos(a) * r * .66 * graphScale;
      const py = y + graphPanY + Math.sin(a) * r * .66 * graphScale;
      const size = 11 + Math.log2(counts[i] + 1) * 2;
      ctx.beginPath();
      for (let k = 0; k < 6; k++) {
        const phi = k * Math.PI / 3;
        const hx = px + Math.cos(phi) * size, hy = py + Math.sin(phi) * size;
        if (k === 0) ctx.moveTo(hx, hy); else ctx.lineTo(hx, hy);
      }
      ctx.closePath();
      ctx.fillStyle = "rgba(174,126,66,.20)";
      ctx.strokeStyle = "rgba(226,177,106,.73)";
      ctx.fill(); ctx.stroke();
      ctx.font = "11px system-ui,sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillStyle = "#ead0a9";
      ctx.fillText(String(counts[i]), px, py);
    }
    return;
  }
  // Viewport culling and bounded edge count avoid huge Canvas workloads.
  const visible = (n) => n.x >= -15 && n.x <= width + 15 &&
                           n.y >= -15 && n.y <= height + 15;
  const edgeCap = graphScale < 1 ? 400 : 1700;
  ctx.lineWidth = .75;
  for (const edge of brainGraph.edges.slice(0, edgeCap)) {
    const a = graphPositions.get(edge.source_id), b = graphPositions.get(edge.target_id);
    if (!a || !b || (!visible(a) && !visible(b))) continue;
    ctx.beginPath();
    ctx.strokeStyle = edge.relation === "possible_conflict"
      ? "rgba(232,125,93,.35)" : "rgba(221,171,110,.19)";
    ctx.moveTo(a.x, a.y);
    ctx.quadraticCurveTo(x + graphPanX, y + graphPanY, b.x, b.y);
    ctx.stroke();
  }
  ctx.beginPath();
  for (const node of graphPositions.values()) {
    if (!visible(node)) continue;
    for (let side = 0; side <= 6; side++) {
      const angle = side * Math.PI / 3;
      const px = node.x + radius * Math.cos(angle);
      const py = node.y + radius * Math.sin(angle);
      if (side === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }
  }
  ctx.strokeStyle = "rgba(238,190,123,.78)";
  ctx.lineWidth = .82;
  ctx.stroke();
  // A brief glow only when real records appeared since the previous refresh.
  if (!reducedMotion && performance.now() - graphPulseAt < 1700) {
    const strength = (1 - (performance.now() - graphPulseAt) / 1700) * .45;
    ctx.strokeStyle = "rgba(247,205,145," + Math.max(0, strength).toFixed(3) + ")";
    ctx.lineWidth = 1.2;
    for (const id of [...graphFreshIds].slice(0, 48)) {
      const node = graphPositions.get(id);
      if (!node || !visible(node)) continue;
      ctx.beginPath();
      ctx.arc(node.x, node.y, radius * 1.7, 0, 2 * Math.PI);
      ctx.stroke();
    }
  }
}

let renderCostMs = 0;
let frameIntervalMs = 33;
let fpsReportedAt = 0;
let observedFps = 0;
function frame(timestamp) {
  if (document.hidden) {
    last = timestamp;
    requestAnimationFrame(frame);
    return;
  }
  const crowded = brainGraph.nodes.length > 900 ||
    (atlasView.active && atlasView.level === "nodes");
  if (timestamp - last >= frameIntervalMs) {
    const elapsed = timestamp-last;
    if (last && elapsed>0) {
      const reading = Math.min(120,1000/elapsed);
      observedFps=observedFps ? observedFps*.85+reading*.15 : reading;
    }
    time = reducedMotion ? 0 : timestamp;
    const before = performance.now();
    paint();
    const cost = performance.now() - before;
    renderCostMs = renderCostMs ? renderCostMs * .86 + cost * .14 : cost;
    // Dynamic frame budget keeps the browser responsive. This is an
    // observed target, not a claim that the device sustains 60 FPS.
    frameIntervalMs = renderCostMs > 26 ? 85 :
      renderCostMs > 12 || crowded ? 50 : 33;
    if (timestamp - fpsReportedAt > 4500) {
      $("brain-fps").textContent = " · " + Math.round(observedFps) +
        " FPS (реально) · цель " +
        Math.round(1000 / frameIntervalMs);
      fpsReportedAt = timestamp;
    }
    last = timestamp;
  }
  if (!reducedMotion) requestAnimationFrame(frame);
}

async function refreshBrain() {
  try {
    const atlasResponse = await fetch("/api/soul/atlas?level=overview", {cache:"no-store"});
    if (!atlasResponse.ok) throw new Error("atlas unavailable");
    const overview = await atlasResponse.json();
    if (overview.total > 2400) {
      await atlasView.refresh(overview);
      brainGraph={nodes:[],edges:[],pending:0};
      $("soul-live-state").textContent = overview.total.toLocaleString("ru-RU") +
        " воспоминаний · " + overview.connections + " связей · " +
        (atlasView.level==="overview"?"8 секторов":
         atlasView.level==="regions"?"области памяти":
         atlasView.level==="clusters"?"кластеры памяти":"отдельные соты");
      if (reducedMotion) paint();
      return;
    }
    atlasView.active = false;
    const response = await fetch("/api/soul/graph", {cache: "no-store"});
    if (!response.ok) throw new Error("graph unavailable");
    const data = await response.json();
    if (graphLoaded) {
      const previous = new Set(brainGraph.nodes.map(node => node.id));
      graphFreshIds = new Set(data.nodes.filter(node => !previous.has(node.id)).map(node => node.id));
      if (graphFreshIds.size) graphPulseAt = performance.now();
    }
    graphLoaded = true;
    brainGraph = data;
    $("soul-live-state").textContent = data.total
      ? data.nodes.length + "/" + data.total + " воспоминаний · " +
        data.edges.length + " связей"
      : "Память ждёт первых воспоминаний";
    if (reducedMotion) paint();
  } catch (error) {
    $("soul-live-state").textContent = "Граф памяти временно недоступен";
  }
}

// Zoom, pan and click work on actual SQLite nodes. Dragging never creates records.
function nearestBrainNode(px, py) {
  if (atlasView.active) return atlasView.hit(px,py);
  if (graphScale < .85 && brainGraph.nodes.length >= 300) return null;
  let found = null;
  let distance = Math.max(64, 55 * graphScale);
  for (const node of graphPositions.values()) {
    const delta = (node.x - px) ** 2 + (node.y - py) ** 2;
    if (delta < distance) {
      distance = delta;
      found = node;
    }
  }
  return found;
}

canvas.addEventListener("wheel", (event) => {
  if (!brainGraph.nodes.length && !atlasView.active) return;
  event.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const centerX = rect.width * .5, centerY = rect.height * .48;
  const px = event.clientX - rect.left, py = event.clientY - rect.top;
  const old = graphScale;
  graphScale = Math.max(.65, Math.min(2.6, graphScale * (event.deltaY < 0 ? 1.12 : .89)));
  const ratio = graphScale / old;
  graphPanX = Math.max(-width * .5, Math.min(width * .5,
    (px - centerX) - ratio * (px - centerX - graphPanX)));
  graphPanY = Math.max(-height * .5, Math.min(height * .5,
    (py - centerY) - ratio * (py - centerY - graphPanY)));
  if (reducedMotion) paint();
}, {passive: false});

canvas.addEventListener("pointerdown", (event) => {
  if (event.button !== 0 || (!brainGraph.nodes.length && !atlasView.active)) return;
  graphDrag = {x: event.clientX, y: event.clientY};
  graphMoved = false;
  canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener("pointermove", (event) => {
  if (graphDrag) {
    const dx = event.clientX - graphDrag.x;
    const dy = event.clientY - graphDrag.y;
    if (Math.abs(dx) + Math.abs(dy) > 2) graphMoved = true;
    graphPanX = Math.max(-width * .5, Math.min(width * .5, graphPanX + dx));
    graphPanY = Math.max(-height * .5, Math.min(height * .5, graphPanY + dy));
    graphDrag = {x: event.clientX, y: event.clientY};
    if (reducedMotion) paint();
    return;
  }
  const rect = canvas.getBoundingClientRect();
  const node = nearestBrainNode(event.clientX - rect.left, event.clientY - rect.top);
  canvas.title = node ? (node.type ? node.preview : node.kind + ": " + node.preview)
    : "Колёсико — масштаб; перетаскивание — перемещение";
  canvas.style.cursor = node ? "pointer" : "grab";
});
canvas.addEventListener("pointerup", (event) => {
  graphDrag = null;
  if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
});
canvas.addEventListener("pointercancel", () => { graphDrag = null; });
canvas.addEventListener("pointerleave", () => { canvas.title = ""; });
canvas.addEventListener("dblclick", async () => {
  graphScale = 1; graphPanX = 0; graphPanY = 0;
  if (atlasView.active) {
    try {
      const overview=await atlasView.fetch("overview");
      atlasView.overview(overview);
    } catch (error) {
      $("soul-live-state").textContent = "Ошибка карты: "+error.message;
    }
  }
  if (reducedMotion) paint();
});
canvas.addEventListener("click", async (event) => {
  if (graphMoved) { graphMoved = false; return; }
  const rect = canvas.getBoundingClientRect();
  const node = nearestBrainNode(event.clientX - rect.left, event.clientY - rect.top);
  if (!node) return;
  if (atlasView.active && node.type && node.type !== "memory") {
    try {
      await atlasView.open(node);
      graphScale=1;graphPanX=0;graphPanY=0;
      $("soul-live-state").textContent =
        atlasView.level==="regions" ? "Области памяти · выбери область" :
        atlasView.level==="clusters" ? "Кластеры памяти · выбери соту" :
        atlasView.level==="nodes" ? "Воспоминания · выбери запись" :
        "Обзор памяти";
      if (reducedMotion) paint();
    } catch (error) {
      $("soul-live-state").textContent="Ошибка загрузки сот: "+error.message;
    }
    return;
  }
  $("memory-search").value = node.preview.slice(0, 60);
  $("memory-details").open = true;
  if (!dialog.open) dialog.showModal();
  await refreshMemory();
});

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
    $("embedding-auto").checked = Boolean(status.vector_chat_enabled);
    $("embedding-auto").disabled = !(status.configured && status.verified && status.embedding_model);
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
    const vectorSelect = $("embedding-model");
    vectorSelect.replaceChildren();
    for (const model of result.models.filter(x => /embed|bge-/i.test(x))) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      vectorSelect.append(option);
    }
    vectorSelect.disabled = vectorSelect.options.length === 0;
    $("embedding-select").disabled = vectorSelect.disabled;
    if (result.models.includes(previous.embedding_model)) {
      vectorSelect.value = previous.embedding_model;
      $("embedding-index").disabled = false;
    }
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

function attachFeedback(node, messageId) {
  if (!Number.isInteger(messageId) || node.querySelector(".feedback-actions")) return;
  const controls = document.createElement("div");
  controls.className = "feedback-actions";
  for (const [symbol, helpful, label] of [["♡", true, "Ответ помог"], ["✕", false, "Ответ не помог"]]) {
    const button = document.createElement("button");
    button.className = "feedback-button";
    button.type = "button";
    button.textContent = symbol;
    button.title = label;
    button.setAttribute("aria-label", label);
    button.addEventListener("click", async () => {
      try {
        const note = helpful ? "" :
          (window.prompt("Что улучшить? Например: точность, краткость, понятность, источники. Можно оставить пустым.", "") || "").slice(0, 400);
        await requestJSON("/api/soul/feedback", "POST", {
          message_id: messageId, helpful, note,
        });
        controls.querySelectorAll("button").forEach(item => item.disabled = true);
        $("chat-notice").textContent = "Оценку сохранила. Учту её в будущих ответах.";
        await refreshCognition();
        await refreshForgeChatUI();
      } catch (error) {
        $("chat-notice").textContent = error.message;
      }
    });
    controls.append(button);
  }
  node.append(controls);
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
    for (const msg of result.messages) {
      const node = addChatBubble(msg.role, msg.content);
      if (msg.role === "assistant") attachFeedback(node, msg.id);
    }
  } catch (error) {
    $("chat-notice").textContent = "Не удалось открыть историю: " + error.message;
  }
}

// A real saved conversation can be reopened without asking Cloud.ru anything.
let latestDialogue9=null;
async function refreshDialogue9Resume(){
  try{
    const old=await requestJSON("/api/soul/dialogue9/resume");
    latestDialogue9=old.found?old:null;
    $("dialogue9-resume").hidden=!latestDialogue9;
    const note=$("dialogue9-resume-note");
    if(latestDialogue9){
      const last=latestDialogue9.last_messages.at(-1);
      note.textContent="Сохранённый разговор: "+latestDialogue9.title+
        (last?" · [Сообщение #"+last.message_id+"] "+
         (last.role==="user"?"Ты: ":"Цукиёра: ")+last.excerpt:"");
      note.hidden=false;
    }else note.hidden=true;
  }catch(_error){
    latestDialogue9=null;
    $("dialogue9-resume").hidden=true;
    $("dialogue9-resume-note").hidden=true;
  }
}
$("dialogue9-resume").addEventListener("click",async()=>{
  if(chatBusy)return;
  try{
    const record=await requestJSON("/api/soul/dialogue9/resume");
    if(!record.found)return;
    activeSession=record.conversation_id;
    preferNewConversation=false;
    await refreshChatSessions();
    $("chat-notice").textContent="Открыла настоящий сохранённый разговор «"+
      record.title+"». История восстановлена локально, новых запросов Cloud.ru нет.";
    $("message").focus();
  }catch(error){
    $("chat-notice").textContent="Не удалось вернуться к диалогу: "+error.message;
  }
});

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
    await refreshDialogue9Resume();
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

// Entirely local history search. Never interpolates conversation text as HTML.
let lastDialogueQuery="";
let dialogueCursor=null;
async function searchDialogueHistory(nextPage=false) {
  const query=$("dialogue-query").value.trim();
  const output=$("dialogue-results");
  if (query.length<2) {
    output.textContent="Введите не меньше двух символов.";
    return;
  }
  const button=$("dialogue-search");
  button.disabled=true;
  try {
    if (!nextPage || query!==lastDialogueQuery) {
      dialogueCursor=null;output.replaceChildren();
    }
    const params=new URLSearchParams({query,limit:"20"});
    if (dialogueCursor!==null) params.set("before_id",String(dialogueCursor));
    const result=await requestJSON("/api/memory/history/search?"+params);
    lastDialogueQuery=query;dialogueCursor=result.next_before_id;
    for (const record of result.items) {
      const choice=document.createElement("button");
      choice.type="button";choice.className="dialogue-result";
      choice.textContent=record.title+" · "+record.role+" #"+record.id+
        " · "+record.excerpt;
      choice.addEventListener("click",async () => {
        if (chatBusy) return;
        activeSession=record.conversation_id;
        preferNewConversation=false;
        const sessions=$("chat-sessions");
        if (![...sessions.options].some(o=>o.value===activeSession)) {
          const opt=document.createElement("option");
          opt.value=activeSession;opt.textContent=record.title;
          sessions.append(opt);
        }
        sessions.value=activeSession;
        try {
          const nearby=await requestJSON("/api/memory/history/context?"+
            new URLSearchParams({message_id:String(record.id)}));
          const log=$("conversation");
          log.replaceChildren();log.classList.add("has-messages");
          let selected=null;
          for (const msg of nearby.messages) {
            const bubble=addChatBubble(msg.role,msg.content);
            if (msg.role==="assistant") attachFeedback(bubble,msg.id);
            if (msg.id===nearby.selected_message_id) {
              bubble.classList.add("dialogue-highlight");
              selected=bubble;
            }
          }
          if (selected) selected.scrollIntoView({block:"center"});
          $("chat-notice").textContent="Фрагмент истории около сообщения #"+
            record.id+". Для последних сообщений снова выбери диалог в списке.";
          $("dialogue-memory").open=false;
        } catch (error) {
          $("chat-notice").textContent="Ошибка открытия сообщения: "+error.message;
        }
      });
      output.append(choice);
    }
    if (!result.items.length && !nextPage) {
      output.textContent="Совпадений в сохранённой истории нет.";
    }
    const oldMore=$("dialogue-next-page");
    if (oldMore) oldMore.remove();
    if (result.has_more) {
      const more=document.createElement("button");
      more.type="button";more.id="dialogue-next-page";
      more.className="quiet-button";more.textContent="Ещё результаты";
      more.addEventListener("click",()=>searchDialogueHistory(true));
      output.append(more);
    }
  } catch (error) {
    output.textContent="Ошибка локального поиска: "+error.message;
  } finally {button.disabled=false;}
}
$("dialogue-search").addEventListener("click",()=>searchDialogueHistory());
$("dialogue-query").addEventListener("keydown",event => {
  if (event.key==="Enter") {
    event.preventDefault();searchDialogueHistory();
  }
});
$("dialogue-recap").addEventListener("click",async () => {
  const button=$("dialogue-recap");
  const node=$("dialogue-summary");
  if (!activeSession) {
    node.textContent="Сначала выбери сохранённый диалог.";
    return;
  }
  button.disabled=true;
  node.textContent="Читаю сохранённые сообщения локально…";
  try {
    const result=await requestJSON("/api/memory/history/recap?"+
      new URLSearchParams({conversation_id:activeSession}));
    node.textContent=result.ready
      ? result.summary
      : "Для сводки необходимо минимум "+result.required+
        " сообщений. Сейчас: "+result.message_count+".";
  } catch (error) {
    node.textContent="Ошибка сводки: "+error.message;
  } finally {button.disabled=false;}
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
        attachFeedback(placeholder, data.message_id);
        pendingClientId = "";
        currentPrompt = "";
        $("chat-notice").textContent = "Ответ и история сохранены локально." +
          (data.audit === "needs_review" ? " Проверь источники в разделе опыта." :
           data.audit === "source_checked" ? " Ссылки на память найдены; истинность не проверена." : "");
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
    await refreshBrain();
    await refreshCognition();
    await refreshForgeChatUI();
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
  if (!window.confirm("Сделать реальные запросы Cloud.ru для моделей, чата, потока и эмбеддингов (если модель выбрана)? Это может расходовать токены.")) return;
  const button = $("cloud-diagnose");
  button.disabled = true;
  cloudMessage("Проверяю настоящие ответы Cloud.ru…");
  try {
    const result = await requestJSON("/api/cloud/checkup", "POST", {
      include_stream: true, include_embeddings: true,
    });
    const labels = {models: "Каталог", chat: "Ответ модели", stream: "Поток",
                    embeddings: "Эмбеддинги"};
    const lines = Object.entries(result.checks).map(([name, check]) => {
      const state = check.state === "passed" ? "OK" :
        check.state === "skipped" ? "пропущено" : "ошибка";
      const extra = check.message || check.sample || (check.dimension ? "размерность " + check.dimension : "");
      return (labels[name] || name) + ": " + state + (extra ? " · " + extra : "");
    });
    cloudMessage((result.live ? "Проверка пройдена." : "Есть проблемы с подключением.") +
                 "\n" + lines.join("\n"));
  } catch (error) {
    cloudMessage("Живая проверка не выполнена: " + error.message);
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
            await refreshBrain();
            await refreshCognition();
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

$("precision-search").addEventListener("click", async () => {
  const query = $("memory-search").value.trim();
  if (!query) return memoryFeedback("Напиши запрос для локального поиска.");
  const button=$("precision-search");
  button.disabled=true;
  try {
    const result=await requestJSON("/api/soul/evidence","POST", {
      query,limit:20,
    });
    const target=$("memory-results");
    target.replaceChildren();
    for (const item of result.memories) {
      const row=document.createElement("div");
      row.className="memory-record";
      const label=document.createElement("small");
      label.textContent="Память #"+item.id+" · источник: "+
        (item.source_ref || item.source_type) +
        " · релевантность: "+Math.round(item.score*100)+"%" +
        (item.match==="related" ? " · связь с #"+item.related_to : "") +
        (item.possible_conflict ? " · возможное противоречие" : "")+
        (item.potentially_stale ? " · возможно устарело" : "")+
        (!item.source_exists ? " · источник не найден" : "")+
        " · "+item.citation;
      const body=document.createElement("p");
      body.textContent=item.content;
      row.append(label,body);
      target.append(row);
    }
    for (const doc of result.documents) {
      const row=document.createElement("div");
      row.className="memory-record";
      const label=document.createElement("small");
      label.textContent=doc.citation+" · "+doc.name+
        " · ревизия "+doc.revision+" · сохранён локально";
      const body=document.createElement("p");
      body.textContent=doc.content;
      row.append(label,body);
      target.append(row);
    }
    if (!result.memories.length && !result.documents.length) {
      const empty=document.createElement("p");
      empty.className="memory-empty";
      empty.textContent="По этому запросу записей не найдено.";
      target.append(empty);
    }
    memoryFeedback("RAG 5.0 · память "+result.memories.length+
      ", фрагментов документов "+result.documents.length+
      ", потенциальных противоречий "+result.flags.length+
      ". Источники не являются проверкой истинности; Cloud.ru не вызывается.");
  } catch (error) {
    memoryFeedback("Ошибка точного поиска: "+error.message);
  } finally {
    button.disabled=false;
  }
});

$("rag-search").addEventListener("click", async () => {
  const query = $("memory-search").value.trim();
  if (!query) {
    memoryFeedback("Напиши вопрос для поиска по смыслу.");
    return;
  }
  const button = $("rag-search");
  button.disabled = true;
  try {
    if (vectorAvailableForSearch &&
        !window.confirm("Отправить этот поисковый запрос embedding-модели Cloud.ru? Это может расходовать токены.")) return;
    const result = vectorAvailableForSearch
      ? await requestJSON("/api/soul/vectors/search", "POST", {
          query, confirm_send_to_cloud: true,
        })
      : await requestJSON("/api/soul/search", "POST", {query, semantic: true});
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
    memoryFeedback(result.mode === "vector_hybrid"
      ? "Векторный + текстовый поиск через Cloud.ru и SQLite."
      : result.semantic ? "Смысловой LLM-поиск выполнен." : "Результаты локального поиска.");
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


// Vector embeddings are opt-in, never silently sent in background maintenance.
$("embedding-select").addEventListener("click", async () => {
  const model = $("embedding-model").value;
  if (!model) return;
  try {
    const status = await requestJSON("/api/cloud/embedding/model", "POST", {model});
    $("embedding-index").disabled = false;
    $("embedding-auto").checked = false;
    $("embedding-auto").disabled = false;
    $("vector-feedback").textContent = "Выбрана " + status.embedding_model
      + ". Индексацию выполняем только после отдельного согласия.";
    await refreshCognition();
  } catch (error) {
    $("vector-feedback").textContent = error.message;
  }
});

$("embedding-auto").addEventListener("change", async () => {
  const checkbox = $("embedding-auto");
  const enabled = checkbox.checked;
  if (enabled && !window.confirm("Разрешить отправку будущих вопросов чата в embedding API Cloud.ru на время этого запуска? Запросы могут расходовать токены. По умолчанию функция выключена.")) {
    checkbox.checked = false;
    return;
  }
  checkbox.disabled = true;
  try {
    const state = await requestJSON("/api/cloud/embedding/auto", "POST", {
      allowed: enabled,
    });
    checkbox.checked = Boolean(state.vector_chat_enabled);
    $("vector-feedback").textContent = state.vector_chat_enabled
      ? "Смысловой поиск для диалогов включён на этот сеанс. Запросы отправляются в Cloud.ru."
      : "Автоматическая отправка в embedding API выключена.";
  } catch (error) {
    checkbox.checked = !enabled;
    $("vector-feedback").textContent = error.message;
  } finally {
    const cloud = await requestJSON("/api/cloud/status").catch(() => null);
    checkbox.disabled = !cloud?.configured || !cloud?.verified || !cloud?.embedding_model;
    if (cloud) checkbox.checked = Boolean(cloud.vector_chat_enabled);
  }
});

$("embedding-index").addEventListener("click", async () => {
  if (!window.confirm("Отправить до 40 подтверждённых воспоминаний в Cloud.ru для создания эмбеддингов? Запрос может расходовать токены.")) return;
  const button = $("embedding-index");
  button.disabled = true;
  $("vector-feedback").textContent = "Индексирую…";
  try {
    const info = await requestJSON("/api/soul/vectors/index", "POST", {
      confirm_send_to_cloud: true, limit: 40,
    });
    $("vector-feedback").textContent = "Готово: " + info.processed +
      " записей, новых облачных векторов: " + info.remote_vectors +
      ", из кэша: " + info.cache_hits + ".";
    await refreshCognition();
  } catch (error) {
    $("vector-feedback").textContent = "Не удалось создать индекс: " + error.message;
  } finally {
    button.disabled = false;
  }
});

async function refreshQuality() {
  const target = $("quality-results");
  target.replaceChildren();
  const loading = document.createElement("p");
  loading.className = "memory-empty";
  loading.textContent = "Проверяю утверждения и источники…";
  target.append(loading);
  try {
    const [result,groups]=await Promise.all([
      requestJSON("/api/soul/quality"),
      requestJSON("/api/soul/semantic/groups")
    ]);
    target.replaceChildren();
    for (const [field, label] of [["duplicates", "Похожая запись"],
                                   ["semantic_duplicates", "Возможно повторение по смыслу"],
                                   ["conflicts", "Возможное противоречие"]]) {
      for (const issue of result[field]) {
        const block = document.createElement("div");
        block.className = "memory-record";
        const name = document.createElement("small");
        name.textContent = label + " · сходство " + Math.round(issue.score * 100) + "%";
        const first = document.createElement("p");
        const second = document.createElement("p");
        first.textContent = "#" + issue.left.id + " (" +
          (issue.left.source_ref || issue.left.source_type) + "): " + issue.left.content;
        second.textContent = "#" + issue.right.id + " (" +
          (issue.right.source_ref || issue.right.source_type) + "): " + issue.right.content;
        block.append(name, first, second);
        if (field!=="conflicts") {
          const grouping=document.createElement("button");
          grouping.type="button";grouping.className="quiet-button";
          grouping.textContent="Связать похожие · сохранить оригиналы";
          grouping.addEventListener("click",async () => {
            if (!window.confirm("Обозначить эти записи похожими? "+
              "Обе исходные записи и все версии будут сохранены.")) return;
            grouping.disabled=true;
            try {
              await requestJSON("/api/soul/semantic/link","POST",{
                left_id:issue.left.id,right_id:issue.right.id,confirmed:true
              });
              memoryFeedback("Записи связаны без удаления исходных фактов.");
              await refreshQuality();
            } catch (error) {
              memoryFeedback("Связь не создана: "+error.message);
              grouping.disabled=false;
            }
          });
          block.append(grouping);
        }
        target.append(block);
      }
    }
    if (groups.items.length) {
      const heading=document.createElement("p");
      heading.className="memory-empty";
      heading.textContent="Подтверждённые вручную связи · "+groups.items.length+
        " (оригиналы сохранены):";
      target.append(heading);
      for (const group of groups.items.slice(0,20)) {
        const row=document.createElement("div");
        row.className="memory-record";
        const label=document.createElement("p");
        label.textContent="#"+group.left_id+" ↔ #"+group.right_id+
          (group.current ? " · действующая связь" :
           " · изменена запись — подтверди заново");
        const revoke=document.createElement("button");
        revoke.type="button";revoke.className="quiet-button";
        revoke.textContent="Отменить связь";
        revoke.addEventListener("click",async () => {
          if (!window.confirm("Убрать связь между воспоминаниями? "+
                              "Сами факты не удалятся.")) return;
          revoke.disabled=true;
          try {
            await requestJSON("/api/soul/semantic/unlink","POST",{
              left_id:group.left_id,right_id:group.right_id,confirmed:true
            });
            await refreshQuality();
          } catch (error) {
            memoryFeedback(error.message);revoke.disabled=false;
          }
        });
        row.append(label,revoke);target.append(row);
      }
    }
    if (!result.duplicates.length && !result.semantic_duplicates.length && !result.conflicts.length) {
      const empty = document.createElement("p");
      empty.className = "memory-empty";
      empty.textContent = "Подозрительных совпадений среди " +
        result.scanned + " последних записей не найдено.";
      target.append(empty);
    }
  } catch (error) {
    target.textContent = "Ошибка анализа: " + error.message;
  }
}
$("memory-quality").addEventListener("toggle", () => {
  if ($("memory-quality").open) refreshQuality();
});

let academyPracticeQuestions=[];
async function refreshCognition() {
  try {
    const [status, biography, cloud, character, academyMetrics, timeline, experiments,
      curriculum, personaArc, rollback, practice, personaLinks] = await Promise.all([
      requestJSON("/api/soul/cognition"),
      requestJSON("/api/soul/biography"),
      requestJSON("/api/cloud/status"),
      requestJSON("/api/soul/character"),
      requestJSON("/api/soul/academy/metrics"),
      requestJSON("/api/soul/character/timeline"),
      requestJSON("/api/soul/academy/experiments"),
      requestJSON("/api/soul/academy/curriculum"),
      requestJSON("/api/soul/persona/arc"),
      requestJSON("/api/soul/academy/rollback"),
      requestJSON("/api/soul/academy/practice"),
      requestJSON("/api/soul/persona/links"),
    ]);
    academyPracticeQuestions=practice.questions;
    const select=$("academy-practice-skill");
    const keep=select.value;
    select.replaceChildren();
    for (const question of academyPracticeQuestions) {
      const option=document.createElement("option");
      option.value=question.skill;option.textContent=question.skill;
      select.append(option);
    }
    if (academyPracticeQuestions.some(x=>x.skill===keep)) select.value=keep;
    const chosen=academyPracticeQuestions.find(x=>x.skill===select.value);
    $("academy-practice-question").textContent=chosen?.question||
      "Нет доступных контрольных вопросов.";
    $("persona-link-summary").textContent=
      "Связи подтверждённой биографии и событий: "+
      personaLinks.relation_count+
      ". Отображаются только существующие записи SQLite.";
    const attempts=practice.results.reduce((sum,x)=>sum+x.runs,0);
    if (!$("academy-practice-result").textContent) {
      $("academy-practice-result").textContent=
        "Выполнено локальных контрольных ответов: "+attempts+
        ". Эвристика не доказывает качество LLM.";
    }
    const academy=status.academy;
    $("academy-enabled").checked=Boolean(academy.enabled);
    const academyPane=$("academy-progress");
    academyPane.replaceChildren();
    for (const goal of academy.goals) {
      if (!goal.samples) continue;
      const item=document.createElement("p");
      item.className="memory-empty";
      item.textContent=goal.category+" · "+goal.state+
        " · полезных: "+goal.helpful+"/"+goal.samples+
        " · изменение оценки: "+(goal.trend>=0?"+":"")+
        Math.round(goal.trend*100)+" п.п.";
      academyPane.append(item);
    }
    if (!academyPane.children.length) academyPane.textContent=
      "Циклы начнутся после оценок реальных ответов. Программу можно отключить.";
    const metricsPane=$("academy-metrics");
    metricsPane.replaceChildren();
    for (const metric of academyMetrics.metrics) {
      const item=document.createElement("p");
      item.className="memory-empty";
      item.textContent=metric.skill+": "+metric.assessment+
        (metric.change_points===null ? " · данных пока недостаточно" :
         " · изменение полезности: "+metric.change_points+" п.п.")+
        " · оценок: "+metric.sample_count;
      metricsPane.append(item);
    }
    const experimentsPane=$("academy-experiments");
    experimentsPane.replaceChildren();
    const one=document.createElement("p");
    one.className="memory-empty";
    const concise=experiments.strategies.concise_first;
    const clear=experiments.strategies.clear_steps;
    one.textContent="Эксперименты манеры ответа: "+experiments.assigned_conversations+
      " диалогов · кратко "+concise.successes+"/"+concise.samples+
      " · по шагам "+clear.successes+"/"+clear.samples+
      (experiments.comparison_ready ?
        " · наблюдаемая разница "+Math.round(experiments.observed_difference*100)+" п.п." :
        " · пока недостаточно оценок");
    experimentsPane.append(one);
    const curriculumPane=$("academy-curriculum");
    curriculumPane.replaceChildren();
    for (const goal of curriculum.goals) {
      const line=document.createElement("p");
      line.className="memory-empty";
      line.textContent=goal.skill+" · "+goal.state+
        " · оценок "+goal.samples+
        (goal.review_required ? " · проверь регрессию!" : "");
      curriculumPane.append(line);
    }
    if (!curriculum.goals.length) curriculumPane.textContent=
      "Цели появятся после первого локального учебного цикла.";
    const rollbackPane=$("academy-rollback");
    rollbackPane.replaceChildren();
    for (const strategy of rollback.strategies) {
      const line=document.createElement("div");
      line.className="memory-actions";
      const status=document.createElement("span");
      status.className="memory-empty";
      status.textContent=strategy.strategy+" · "+strategy.state+
        " · оценок "+strategy.evidence_count;
      line.append(status);
      if (strategy.state==="suspended") {
        const resume=document.createElement("button");
        resume.type="button";resume.className="quiet-button";
        resume.textContent="Вернуть стиль";
        resume.addEventListener("click",async () => {
          if (!window.confirm("Вернуть этот стиль после отрицательных оценок?")) return;
          resume.disabled=true;
          try {
            await requestJSON("/api/soul/academy/rollback/resume","POST",
              {strategy:strategy.strategy,confirmed:true});
            await refreshCognition();
          } catch(error) {
            $("chat-notice").textContent="Ошибка возврата стиля: "+error.message;
            resume.disabled=false;
          }
        });
        line.append(resume);
      }
      rollbackPane.append(line);
    }
    const arcPane=$("persona-arc");
    arcPane.replaceChildren();
    const lastArc=personaArc.snapshots[0];
    const arcInfo=document.createElement("p");
    arcInfo.className="memory-empty";
    arcInfo.textContent=lastArc
      ? "История: биография "+lastArc.approved_facts+
        " · привычки "+lastArc.approved_habits+
        " · исправленные записи "+lastArc.corrected_items+
        ". Защищённый характер не меняется."
      : "История характера пока не сформирована.";
    arcPane.append(arcInfo);
    const charPane=$("character-progress");
    charPane.replaceChildren();
    const summary=document.createElement("p");
    summary.className="memory-empty";
    summary.textContent="Диалогов: "+character.conversations+
      " · подтверждённых привычек: "+character.stable_habits.length+
      " · событий: "+character.accepted_events+
      ". Состояние — программная модель, не эмоции.";
    charPane.append(summary);
    for (const habit of character.stable_habits.slice(0,8)) {
      const line=document.createElement("p");
      line.className="memory-empty";
      line.textContent="Память #"+habit.id+": "+habit.content;
      charPane.append(line);
    }
    const historyPane=$("character-timeline");
    historyPane.replaceChildren();
    for (const entry of timeline.items.slice(0,8)) {
      const line=document.createElement("p");
      line.className="memory-empty";
      line.textContent=entry.timestamp+" · "+entry.event+
        (entry.memory_id ? " · память #"+entry.memory_id : "")+
        " · источник: "+(entry.source_ref || entry.source_type);
      historyPane.append(line);
    }
    vectorAvailableForSearch = Boolean(
      cloud.configured && cloud.verified && cloud.embedding_model
      && status.vectors.indexed_memories > 0
    );
    const recent = status.last_cycle;
    $("cognitive-status").textContent = (recent
      ? "Локальная проверка: " + recent.status + " · " + recent.created_at
      : "Первый фоновый цикл ещё не завершён")
      + ". Воспоминания: " + status.vectors.active_memories
      + ", проиндексировано: " + status.vectors.indexed_memories
      + ", неоднозначных связей: " + status.possible_conflicts
      + ", оценок ответов: " + status.feedback.total + ".";
    $("vector-count").textContent = status.vectors.indexed_memories
      ? "(" + status.vectors.indexed_memories + ")" : "опционально";
    const state = status.experience;
    $("tone-status").textContent = "Тон общения (программная модель, не чувства): " +
      "теплота " + Math.round(state.warmth * 100) + "% · " +
      "внимательность " + Math.round(state.focus * 100) + "% · " +
      "любопытство " + Math.round(state.curiosity * 100) + "%. " +
      "Оценок: " + state.feedback_samples + ".";
    const goals = $("learning-goals");
    goals.replaceChildren();
    const names = {accuracy: "Точность", brevity: "Краткость",
                   clarity: "Ясность", sources: "Источники"};
    for (const goal of state.goals) {
      if (!goal.attempts) continue;
      const item = document.createElement("p");
      item.className = "memory-empty";
      item.textContent = (names[goal.category] || goal.category) +
        ": " + (goal.status === "practice" ? "нужно улучшить" :
        goal.status === "improving" ? "улучшается" : "наблюдение") +
        " · обратная связь: " + goal.attempts;
      goals.append(item);
    }
    const list = $("biography-notes");
    list.replaceChildren();
    for (const entry of biography.approved_history.slice(0, 12)) {
      const item = document.createElement("p");
      item.className = "memory-empty";
      item.textContent = "№" + entry.id + " · " + entry.kind + ": " + entry.content;
      list.append(item);
    }
    if (biography.learned_guidance) {
      const item = document.createElement("p");
      item.className = "memory-empty";
      item.textContent = "Опыт общения: " + biography.learned_guidance;
      list.append(item);
    }
    if (!biography.approved_history.length) {
      const item = document.createElement("p");
      item.className = "memory-empty";
      item.textContent = "Биография пополняется только подтверждёнными воспоминаниями.";
      list.append(item);
    }
  } catch (error) {
    $("cognitive-status").textContent = "Состояние памяти недоступно: " + error.message;
  }
}

// Cognitive Soul 5.0 is a single local dashboard for the existing modules.
// Older manual tools remain available under "Advanced" without losing data.
async function refreshUnifiedCognitive() {
  if(!$("cognitive-unified").open) return;
  try {
    const [state,reviews]=await Promise.all([
      requestJSON("/api/soul/unified"),
      requestJSON("/api/soul/unified/reviews")
    ]);
    const jobs=state.maintenance?.jobs||[];
    $("unified-state").textContent=(state.enabled
      ?"Локальная автоматика включена":"Локальная автоматика на паузе")+
      " · режим: только безопасные локальные действия"+
      " · очередь: "+(state.maintenance?.queue_depth??0)+
      " · предупреждений для проверки: "+state.pending_reviews+
      " · скрытых платных запросов: 0.";
    $("unified-toggle").textContent=state.enabled
      ?"Приостановить фоновую работу":"Возобновить локальную работу";
    $("unified-toggle").dataset.enabled=String(state.enabled);
    $("unified-tick").disabled=!state.enabled;
    const overview=$("unified-overview");
    overview.replaceChildren();
    const counts={ok:0,warning:0,pending:0,running:0,waiting:0};
    for(const job of jobs){
      if(job.last_status==="ok") counts.ok++;
      if(job.last_status==="warning") counts.warning++;
      if(job.state in counts) counts[job.state]++;
    }
    addCognitiveLine(overview,"Планировщик: "+jobs.length+" локальных заданий"+
      " · завершено успешно: "+counts.ok+
      " · с предупреждениями: "+counts.warning+
      " · ожидают: "+counts.pending+
      ". Чат имеет приоритет.");
    if(state.last_digest) {
      const data=state.last_digest.summary;
      addCognitiveLine(overview,"Сводка "+state.last_digest.day+
        ": отзывов "+data.feedback+
        ", память на проверке "+data.pending_memories+
        ", исследовательских вопросов "+data.pending_questions+
        ", кандидатов навыков "+data.skill_candidates+
        ", гипотез V3/V4 "+data.v3_hypotheses+"/"+data.v4_hypotheses);
    } else addCognitiveLine(overview,
      "Сводка появится после первого задания cog5_digest.");
    const reviewPane=$("unified-review-list");
    reviewPane.replaceChildren();
    for(const item of reviews.items.filter(x=>x.state==="pending").slice(0,12)) {
      const line=document.createElement("div");
      line.className="memory-record";
      addCognitiveLine(line,(item.kind==="possible_conflict"
        ?"Возможное противоречие":"Возможно устаревшая запись")+
        " · память #"+item.memory_id+
        (item.related_id?" и #"+item.related_id:""));
      const mark=document.createElement("button");
      mark.type="button";mark.className="quiet-button";
      mark.textContent="Отметить как проверенное";
      mark.addEventListener("click",async()=>{
        if(!window.confirm("Отметить предупреждение проверенным? Исходная память не изменится.")) return;
        try {
          await requestJSON("/api/soul/unified/review","POST",
                            {id:item.id,confirmed:true});
          await refreshUnifiedCognitive();
        } catch(error) {
          $("unified-state").textContent="Ошибка: "+error.message;
        }
      });
      line.append(mark);reviewPane.append(line);
    }
    if(!reviewPane.childElementCount) addCognitiveLine(reviewPane,
      "Пока нет новых предупреждений. Это не доказывает отсутствие ошибок в знаниях.");
    const activity=$("unified-activity");
    activity.replaceChildren();
    for(const event of state.recent_jobs.slice(0,10)){
      addCognitiveLine(activity,event.kind+" · "+
        (event.outcome==="ok"?"выполнено":"требует внимания")+
        " · "+event.duration_ms+" мс · "+event.created_at);
    }
    if(!state.recent_jobs.length) addCognitiveLine(activity,
      "Задачи ещё не выполнялись на этой базе.");
  } catch(error) {
    $("unified-state").textContent="Диагностика Cognitive Soul 5.0: "+error.message;
  }
}
$("cognitive-unified").addEventListener("toggle",refreshUnifiedCognitive);
$("unified-refresh").addEventListener("click",refreshUnifiedCognitive);
$("unified-toggle").addEventListener("click",async()=>{
  const enable=$("unified-toggle").dataset.enabled!=="true";
  if(!window.confirm(enable?"Возобновить безопасный локальный цикл?"
      :"Приостановить фоновую автоматику?")) return;
  try {
    await requestJSON("/api/soul/unified/control","POST",
      {enabled:enable,confirmed:true});
    await refreshUnifiedCognitive();
  } catch(error) {
    $("unified-state").textContent="Не удалось изменить режим: "+error.message;
  }
});
$("unified-tick").addEventListener("click",async()=>{
  if(!window.confirm("Выполнить один безопасный локальный шаг очереди? Cloud.ru не используется.")) return;
  try {
    const result=await requestJSON("/api/soul/unified/tick","POST",{confirmed:true});
    $("unified-state").textContent="Результат шага: "+result.status+
      (result.kind?" · "+result.kind:"");
    await refreshUnifiedCognitive();
  } catch(error) {
    $("unified-state").textContent="Локальная проверка: "+error.message;
  }
});

// Tsukiyora 13.0: one read-only view and explicit opt-in metadata actions.
async function refreshCoordinator13(){
  if(!$("coordinator13").open)return;
  try{
    const [state,signals,reviews]=await Promise.all([
      requestJSON("/api/soul/coordinator13"),
      requestJSON("/api/soul/coordinator13/issues"),
      requestJSON("/api/soul/coordinator13/reviews")
    ]);
    $("coordinator13-status").textContent=
      "Единая очередь: "+state.queue.registered_jobs+
      " задач · с ошибками: "+state.queue.jobs_with_failures+
      " · приблизительно проиндексированных векторов: "+state.cached_vector_index.existing_indexed_vectors+
      " / "+state.cached_vector_index.memory_embedding_links+
      " · аннотаций действительности: "+state.memory.validity_annotations+
      " · проверочных эталонов: "+state.user_attested_holdout_cases+
      " · новых платных фоновых обращений: "+state.new_background_model_calls+".";
    const overview=$("coordinator13-overview");overview.replaceChildren();
    addCognitiveLine(overview,"Выбрано 29 пунктов; №5 (адаптация нагрузки) не добавлен."+
      " Рабочие механизмы переиспользованы, часть направлений остаётся под дальнейшие тесты.");
    addCognitiveLine(overview,"Единая лента проверок: "+reviews.sampled_count+
      " записей, все требуют ручного решения. Не автоподтверждение.");
    addCognitiveLine(overview,"Отрицательных отзывов изучено: "+
      signals.negative_feedback_scanned+
      " · возможные причины: "+JSON.stringify(signals.possible_categories)+
      ". Только текстовые признаки, не доказанная причина.");
    addCognitiveLine(overview,"Очередь: фиксированные приоритеты и старение ожидающих задач."+
      " Индивидуальных сетевых агентов нет.");
    for(const item of reviews.items.slice(0,7))
      addCognitiveLine(overview,"Проверка "+item.kind+
        " · "+item.table+" #"+item.record_id+
        " · источник #"+item.source_id);
  }catch(error){
    $("coordinator13-status").textContent="Центр 13.0 недоступен: "+error.message;
  }
}
$("coordinator13").addEventListener("toggle",refreshCoordinator13);
$("coordinator13-refresh").addEventListener("click",refreshCoordinator13);
$("coordinator13-annotate").addEventListener("click",async()=>{
  const memory=Number($("coordinator13-memory").value);
  const start=$("coordinator13-from").value||null;
  const until=$("coordinator13-until").value||null;
  const rationale=$("coordinator13-reason").value.trim();
  if(!Number.isSafeInteger(memory)||memory<=0||(!start&&!until)||
     rationale.length<8){
    $("coordinator13-result").textContent=
      "Укажи ID, дату действия и объяснение не короче 8 символов.";
    return;
  }
  if(!window.confirm("Сохранить указанную тобой действительность факта #"+
       memory+"? Это не изменяет сам факт и не доказывает его истинность."))return;
  try{
    const result=await requestJSON("/api/soul/temporal13/annotate","POST",{
      memory_id:memory,effective_from:start,effective_until:until,
      entity_key:$("coordinator13-entity").value.trim()||null,
      rationale,confirmed:true
    });
    $("coordinator13-result").textContent=
      "Аннотация #"+result.annotation_id+
      (result.already_recorded?" уже сохранена.":" сохранена.")+
      " Текст и история источника не изменялись.";
    await refreshCoordinator13();
  }catch(error){
    $("coordinator13-result").textContent="Ошибка временной записи: "+error.message;
  }
});
$("coordinator13-timeline").addEventListener("click",async()=>{
  const id=Number($("coordinator13-memory").value);
  if(!Number.isSafeInteger(id)||id<=0)return;
  try{
    const history=await requestJSON(
      "/api/soul/temporal13/timeline?memory_id="+id);
    $("coordinator13-result").textContent=
      "Память #"+id+" · редакций: "+history.revisions.length+
      " · временных отметок: "+history.validity_annotations.length+
      " · текущих отметок: "+history.validity_annotations.filter(
        x=>x.applies_to_current_version).length+
      ". Все даты — утверждения пользователя, не исторические снимки.";
  }catch(error){
    $("coordinator13-result").textContent="Ошибка истории: "+error.message;
  }
});
$("coordinator13-add-case").addEventListener("click",async()=>{
  const question=$("coordinator13-question").value.trim();
  const id=Number($("coordinator13-expected").value);
  if(question.length<3||!Number.isSafeInteger(id)||id<=0){
    $("coordinator13-result").textContent=
      "Укажи вопрос и существующий ID ожидаемого воспоминания.";
    return;
  }
  if(!window.confirm("Подтвердить эталон поиска для памяти #"+id+
    "? Он будет сохранён только локально."))return;
  try{
    const result=await requestJSON("/api/soul/coordinator13/eval-case","POST",
      {question,expected_memory_id:id,confirmed:true});
    $("coordinator13-result").textContent=
      "Эталон #"+result.id+" сохранён. Это твой образец, а не ответ модели.";
    await refreshCoordinator13();
  }catch(error){
    $("coordinator13-result").textContent="Ошибка эталона: "+error.message;
  }
});
$("coordinator13-benchmark").addEventListener("click",async()=>{
  if(!window.confirm("Проверить поиск по сохранённым тобой эталонам? "+
    "Это локальные вычисления, не запрос к Cloud.ru."))return;
  try{
    const result=await requestJSON("/api/soul/coordinator13/benchmark","POST",
      {confirmed:true,limit:15});
    $("coordinator13-result").textContent=result.status==="deferred"
      ?"Проверка отложена: сейчас идёт чат."
      :"Эталонных вопросов: "+result.valid_cases+" · найдено: "+
       result.hits_at_k+" · Recall@5: "+
       (result.recall_at_k===null?"нет образцов":result.recall_at_k)+
       " · p50: "+result.p50_ms+" мс · p95: "+result.p95_ms+
       " мс. Это не проверка качества LLM.";
  }catch(error){
    $("coordinator13-result").textContent="Ошибка проверки: "+error.message;
  }
});

// Tsukiyora 12.0: inspectable, deterministic inner notes, not private model reasoning.
async function refreshInner12(){
  if(!$("inner12").open)return;
  try {
    const [state,notes]=await Promise.all([
      requestJSON("/api/soul/inner12"),
      requestJSON("/api/soul/inner12/notes?limit=25")
    ]);
    const c=state.state.active_goals_and_review_items;
    $("inner12-status").textContent=
      "Самостоятельность: "+state.state.autonomy_level+"/3"+
      " · локальная практика: "+(state.state.local_practice_enabled?"разрешена":"отключена")+
      " · открытых заметок: "+c.open_notes+
      " · нерешённых вопросов: "+c.pending_questions+
      " · предупреждений очереди: "+c.queue_warning_jobs+
      " · самостоятельных обращений Cloud.ru: "+state.runtime.new_paid_cloud_calls+".";
    const summary=$("inner12-overview");summary.replaceChildren();
    addCognitiveLine(summary,"Разрешённые пункты: "+state.selected_features.join(", ")+
      ". Пункт 4 не включён.");
    addCognitiveLine(summary,"Внутренний рабочий блокнот: ID источников вместо выдуманных мыслей."+
      " Отдельного фонового процесса нет.");
    addCognitiveLine(summary,"Личность: "+state.state.style_adjustments_approved+
      " одобренных манер речи · "+state.state.persona_arc_snapshots+
      " сохранённых снимков хронологии.");
    if(state.last_diary_entry) {
      addCognitiveLine(summary,"Дневник: "+state.last_diary_entry.utc_day+
        " · исходная память и навыки не изменялись.");
    } else {
      addCognitiveLine(summary,"Первый дневник 12.0 появится после локального фонового цикла.");
    }
    const list=$("inner12-notes");list.replaceChildren();
    if(!notes.items.length)
      addCognitiveLine(list,"Нет актуальных заметок с проверяемым источником."+
        " Если локальная практика разрешена, новая запись появится при следующем цикле.");
    for(const item of notes.items){
      const row=document.createElement("div");
      row.className="memory-result";
      const line=document.createElement("p");
      line.className="memory-empty";
      line.textContent="Заметка #"+item.id+" · "+item.label+
        " Вопрос: "+item.question+
        " · источник: "+item.source_type+" #"+item.source_id;
      row.append(line);
      const inspect=document.createElement("button");
      inspect.type="button";
      inspect.className="quiet-button";
      inspect.textContent="Проверить";
      inspect.addEventListener("click",async()=>{
        try{
          const result=await requestJSON("/api/soul/inner12/inspect","POST",
            {note_id:item.id});
          $("inner12-result").textContent=
            "Заметка #"+result.id+" · источник "+
            (result.source_current?"актуален":"уже не требует внимания")+
            " · данные проверки: "+JSON.stringify(result.checks)+
            ". Это локальная проверка, а не тест языковой модели.";
        }catch(error){
          $("inner12-result").textContent="Ошибка проверки: "+error.message;
        }
      });
      const close=document.createElement("button");
      close.type="button";
      close.className="quiet-button";
      close.textContent="Закрыть заметку";
      close.addEventListener("click",async()=>{
        if(!window.confirm("Закрыть только рабочую заметку #"+item.id+
          "? Исходные данные и решения останутся без изменений."))return;
        try{
          await requestJSON("/api/soul/inner12/dismiss","POST",
            {note_id:item.id,confirmed:true});
          $("inner12-result").textContent="Заметка #"+item.id+
            " закрыта. Исходные данные сохранены.";
          await refreshInner12();
        }catch(error){
          $("inner12-result").textContent="Ошибка: "+error.message;
        }
      });
      row.append(inspect,close);list.append(row);
    }
  } catch(error){
    $("inner12-status").textContent="Не удалось прочитать рабочий блокнот: "+error.message;
  }
}
$("inner12").addEventListener("toggle",refreshInner12);
$("inner12-refresh").addEventListener("click",refreshInner12);

// Tsukiyora 11.0: source-linked local memory retrieval, no extra provider calls.
async function refreshMemory11(){
  if(!$("memory11").open)return;
  try {
    const report=await requestJSON("/api/soul/memory11");
    const c=report.counters;
    $("memory11-state").textContent=
      "Активных воспоминаний: "+c.active_memory+
      " · версий: "+c.memory_revisions+
      " · графовых связей: "+c.graph_links+
      " · кэшированных embeddings: "+c.cached_embeddings+
      " · ожидают проверки: "+(c.unresolved_memories+c.possible_conflicts)+
      " · предупреждений очереди: "+c.queue_warning_jobs+
      " · новых Cloud.ru-запросов: "+report.additional_cloud_calls+".";
    const container=$("memory11-metrics");container.replaceChildren();
    addCognitiveLine(container,"Индексы: "+(report.stages.retrieval.fts_tables.join(", ")||"только ограниченный текстовый поиск"));
    addCognitiveLine(container,"Интеллект: лексические признаки и ссылки на источники, не независимая проверка истины.");
    addCognitiveLine(container,"Управление: 1 очередь, 0 дополнительных процессов, одобрение навыков и прав доступа по старым правилам.");
    const last=report.latest_local_audit;
    addCognitiveLine(container,last
      ?"Последний локальный аудит: "+last.day+
       " · спорных записей: "+last.counts.possible_conflicts
      :"Фоновый аудит 11.0 ещё не выполнялся на этой базе.");
  }catch(error){
    $("memory11-state").textContent="Не удалось проверить память: "+error.message;
  }
}
$("memory11").addEventListener("toggle",refreshMemory11);
$("memory11-refresh").addEventListener("click",refreshMemory11);
$("memory11-search").addEventListener("click",async()=>{
  const query=$("memory11-query").value.trim();
  if(!query){$("memory11-search-state").textContent="Напиши поисковый запрос.";return;}
  const items=$("memory11-results");items.replaceChildren();
  $("memory11-search-state").textContent="Поиск по локальной базе...";
  try{
    const body={query,limit:12};
    if($("memory11-date").value)body.as_of=$("memory11-date").value;
    const found=await requestJSON("/api/soul/memory11/search","POST",body);
    const ambiguous=found.plan.reference_ambiguous;
    $("memory11-search-state").textContent=
      "Режим: "+found.mode+" · найдено: "+found.items.length+
      " · время: "+(found.duration_ms===undefined?"не измерено":found.duration_ms+" мс")+
      (ambiguous?" · уточни, что именно нужно продолжить":"")+
      (found.cached_vector_used?" · использован старый векторный кэш":"")+".";
    for(const item of found.items){
      addCognitiveLine(items,item.citation+" · "+item.kind+
        " · "+item.excerpt+" · источник: "+item.source_type+
        (item.source_ref?" #"+item.source_ref:"")+
        " · изменений: "+item.revision_count+
        (item.possible_conflict?" · проверить возможное противоречие":"")+
        " · пользовательское утверждение, не доказанная истина");
    }
    if(!found.items.length)addCognitiveLine(items,
      "Совпадений среди активных записей памяти нет. Из истории нельзя автоматически придумывать факты.");
  }catch(error){
    $("memory11-search-state").textContent="Поиск недоступен: "+error.message;
  }
});
$("memory11-benchmark").addEventListener("click",async()=>{
  if(!window.confirm("Проверить 5 существующих воспоминаний локальным поиском? "+
     "Это тест самопоиска, не доказательство понимания. Cloud.ru не вызывается."))return;
  try{
    const b=await requestJSON("/api/soul/memory11/benchmark","POST",
      {confirmed:true,count:5});
    $("memory11-search-state").textContent=b.status==="deferred"
      ?"Проверка отложена: Цукиёра сейчас отвечает."
      :"Самопоиск: "+b.self_retrieval_hits+"/"+b.queries+
       " · recall@5: "+(b.self_retrieval_recall_at_k===null?"нет примеров":b.self_retrieval_recall_at_k)+
       " · p50: "+b.p50_ms+" мс · p95: "+b.p95_ms+
       " мс. Это НЕ независимая оценка качества ответов.";
  }catch(error){
    $("memory11-search-state").textContent="Проверка скорости не удалась: "+error.message;
  }
});

// Tsukiyora 10.0: ONE read-only status surface over existing modules and queue.
async function refreshLife10(){
  if(!$("life10").open)return;
  try{
    const report=await requestJSON("/api/soul/life10");
    const q=report.feedback,s=report.signals,p=report.systems;
    $("life10-state").textContent=
      "Единый локальный планировщик · автономность "+p.personality.autonomy_level+
      "/3 · память: "+p.memory.active_items+
      " · оценки ответов: "+q.positive+" полезных / "+q.negative+" отрицательных"+
      " · предупреждения очереди: "+s.queue_warning_jobs+
      " · дополнительных запросов Cloud.ru: "+report.additional_paid_requests+".";
    const systems=$("life10-systems");systems.replaceChildren();
    addCognitiveLine(systems,"Системы · один источник данных вместо копирования");
    addCognitiveLine(systems,"Общение: общий ограниченный контекст обычного и потокового чата, ссылки на сообщения");
    addCognitiveLine(systems,"Память: "+p.memory.active_items+
      " активных записей · "+p.memory.review_signals+
      " сигналов на проверку (не подтверждённые противоречия)");
    addCognitiveLine(systems,"Личность: "+p.personality.approved_styles+
      " одобренных манер общения · исходный характер защищён");
    addCognitiveLine(systems,"Обучение: "+p.learning.review_required+
      " учебных проверок · "+p.learning.skill_candidates+
      " кандидатов · контрольные пары V7 не считаются независимым тестом модели");
    addCognitiveLine(systems,"Управление: 1 SQLite-очередь, 1 рабочий поток; фоновая работа V10 не вызывает модель");
    const actions=$("life10-actions");actions.replaceChildren();
    addCognitiveLine(actions,"Что требует внимания (ничего не применяется автоматически)");
    if(!report.actions.length)addCognitiveLine(actions,"Пока нет новых локальных сигналов для просмотра.");
    for(const action of report.actions){
      const button=document.createElement("button");
      button.type="button";
      button.className="quiet-button";
      button.textContent=action.label+" · "+action.count+" · открыть";
      button.addEventListener("click",()=>{
        const target=$(action.section);
        if(!target)return;
        target.open=true;
        target.scrollIntoView({behavior:"smooth",block:"center"});
      });
      actions.append(button);
    }
    const evidence=$("life10-evidence");evidence.replaceChildren();
    const last=report.last_local_audit;
    addCognitiveLine(evidence,last
      ?"Последний локальный аудит: "+last.day+
        " · отрицательных оценок среди последних 30 оценённых ответов: "+
         last.counts.recent_negative+
        " · предупреждений очереди: "+last.counts.queue_warning_jobs
      :"Аудит V10 ещё не выполнялся: задача ожидает очереди. Качество модели не доказано.");
    addCognitiveLine(evidence,"Источники: существующие SQLite-таблицы V5/V7/V8/V9. "+
      "Платные проверки, автоизменение личности и автоматическое включение навыков запрещены.");
  }catch(error){
    $("life10-state").textContent="Не удалось прочитать единый цикл: "+error.message;
  }
}
$("life10").addEventListener("toggle",refreshLife10);
$("life10-refresh").addEventListener("click",refreshLife10);

// Unified read-only view of EXISTING quality evidence (no new Cloud.ru calls).
async function refreshGrowthQuality(){
  if(!$("growth-quality").open)return;
  try{
    const report=await requestJSON("/api/soul/growth-quality");
    const f=report.ratings,g=report.growth;
    $("growth-quality-state").textContent=
      "Диалогов: "+report.conversation.dialogues+
      " · сообщений: "+report.conversation.messages+
      " · подтверждённых и рабочих записей памяти: "+report.memory.active+
      " · оценки ответов: "+f.positive+" положительных, "+f.negative+" отрицательных"+
      " · локальных целей: "+g.goals+
      " · ждут решения: "+g.pending_review+
      " · ошибки очереди: "+g.queue_warnings+".";
    const target=$("growth-quality-skills");target.replaceChildren();
    addCognitiveLine(target,"Навыки: реальный пользовательский отзыв ≠ контрольное сравнение ≠ доказательство причинного улучшения");
    if(!report.skills.length){
      addCognitiveLine(target,"Навыки ещё не зарегистрированы.");
    }
    for(const skill of report.skills){
      const pct=skill.observed_positive_share===null?"оценок пока нет":
        Math.round(skill.observed_positive_share*100)+"% положительных";
      addCognitiveLine(target,"Навык #"+skill.skill_id+" · "+skill.domain+
        " · "+skill.state+" · реальных оценок: "+skill.real_answer_ratings+
        " ("+pct+") · контрольных пар: "+skill.attested_cases+
        " · прирост по ручным эталонам: "+skill.attested_gain+
        " · регрессии: "+skill.attested_regressions+
        (skill.real_answer_ratings<5?" · недостаточно отзывов для устойчивого сигнала":"")+
        " · независимое улучшение модели не доказано");
    }
  }catch(error){
    $("growth-quality-state").textContent="Не удалось получить результаты: "+error.message;
  }
}
$("growth-quality").addEventListener("toggle",refreshGrowthQuality);
$("growth-quality-refresh").addEventListener("click",refreshGrowthQuality);

// Living Personality 8.0: bounded local autonomy with explicit user choices.
let pendingLiving8=null;
let living8Busy=false;
async function refreshLiving8Chat(){
  try{
    const proposals=await requestJSON("/api/soul/living8/proposals");
    pendingLiving8=proposals.items[0]||null;
    $("living8-chat-proposal").hidden=!pendingLiving8;
    if(pendingLiving8){
      $("living8-chat-text").textContent=
        "Хозяин, "+pendingLiving8.label+" · "+pendingLiving8.effect;
    }
  }catch(_error){
    pendingLiving8=null;
    $("living8-chat-proposal").hidden=true;
  }
}
async function refreshLiving8(){
  if(!$("living8").open)return;
  try{
    const [state,agenda,diary,research]=await Promise.all([
      requestJSON("/api/soul/living8"),
      requestJSON("/api/soul/living8/agenda"),
      requestJSON("/api/soul/living8/journal"),
      requestJSON("/api/soul/living8/research")
    ]);
    $("living8-level").value=String(state.autonomy_level);
    const rights=state.local_permissions;
    $("living8-proactive").checked=Boolean(rights.proactive_chat);
    $("living8-practice").checked=Boolean(rights.local_practice);
    $("living8-documents").checked=Boolean(rights.indexed_documents);
    $("living8-status").textContent="Автономность: "+state.autonomy_level+
      " · целей: "+state.progress.total_goals+
      " · ждут просмотра: "+state.progress.waiting_review+
      " · согласованных манер: "+state.progress.approved_style_adjustments+
      " · новых платных вызовов V8: 0.";
    const goals=$("living8-goals");goals.replaceChildren();
    addCognitiveLine(goals,"Планы и контрольные точки");
    for(const item of agenda.items.slice(0,14)){
      addCognitiveLine(goals,"#"+item.id+" · "+item.label+
        " · "+item.state+" · шаги "+item.checkpoint_count+"/3"+
        " · источников метрик: "+item.evidence_count);
    }
    if(!agenda.items.length)addCognitiveLine(goals,
      "Пока нет целей: фоновый планировщик изучит существующие оценки, вопросы и предупреждения.");
    const refs=$("living8-research");refs.replaceChildren();
    addCognitiveLine(refs,"Источники по разрешённым локальным вопросам");
    for(const item of research.items.slice(0,10)){
      addCognitiveLine(refs,"Вопрос #"+item.question_id+" · "+
        item.name+" · "+item.citation+
        (item.source_updated_since_review?" · источник обновлён, требуется повторная проверка":""));
    }
    if(!research.items.length)addCognitiveLine(refs,
      "Пока нет ссылок. Нужны одобренные исследовательские вопросы и локальное разрешение на документы.");
    const log=$("living8-chronicle");log.replaceChildren();
    addCognitiveLine(log,"Дневник локального развития (без текста переписки)");
    for(const item of diary.items.slice(0,12)){
      addCognitiveLine(log,item.created_at+" · "+item.event+
        " · "+item.source_type+" · "+item.source_ref);
    }
    if(!diary.items.length)addCognitiveLine(log,"Записей пока нет.");
  }catch(error){
    $("living8-status").textContent="Состояние 8.0 недоступно: "+error.message;
  }
}
$("living8").addEventListener("toggle",refreshLiving8);
$("living8-refresh").addEventListener("click",refreshLiving8);
$("living8-save-level").addEventListener("click",async()=>{
  try{
    const level=Number($("living8-level").value);
    if(!Number.isInteger(level)||level<0||level>3)throw Error("Неверный уровень.");
    if(!window.confirm("Установить уровень автономности "+level+
      "? Для Cloud.ru, изменения личности и внешних действий по-прежнему требуется отдельное согласие."))return;
    await requestJSON("/api/soul/living8/control","POST",{level,confirmed:true});
    $("living8-message").textContent="Уровень сохранён: "+level+".";
    await refreshLiving8();await refreshLiving8Chat();
  }catch(error){$("living8-message").textContent=error.message;}
});
for(const [element,scope] of [["living8-proactive","proactive_chat"],
                               ["living8-practice","local_practice"],
                               ["living8-documents","indexed_documents"]]){
  $(element).addEventListener("change",async value=>{
    const enabled=Boolean(value.target.checked);
    try{
      if(!window.confirm("Изменить локальное разрешение "+scope+" на "+
        (enabled?"ВКЛ":"ВЫКЛ")+"? Платные и внешние действия это не разрешает.")){
        await refreshLiving8();return;
      }
      await requestJSON("/api/soul/living8/permission","POST",
        {scope,enabled,confirmed:true});
      $("living8-message").textContent="Локальное разрешение обновлено.";
      await refreshLiving8();await refreshLiving8Chat();
    }catch(error){
      $("living8-message").textContent=error.message;await refreshLiving8();
    }
  });
}
async function decideLiving8(approve){
  if(!pendingLiving8||living8Busy)return;
  const item=pendingLiving8;
  if(!window.confirm((approve?"Одобрить":"Отклонить")+
    " предложение #"+item.id+"? "+
    (item.kind==="style"
      ?"Только согласованный стиль ответов, исходная личность не изменится."
      :"Это отметит просмотр локального отчёта; не активирует навыки.")))return;
  living8Busy=true;
  try{
    await requestJSON("/api/soul/living8/decision","POST",
      {proposal_id:item.id,approve,confirmed:true});
    $("chat-notice").textContent=approve
      ?"Предложение саморазвития одобрено.":"Предложение отклонено.";
    await refreshLiving8Chat();
    await refreshLiving8();
  }catch(error){
    $("chat-notice").textContent="Решение не сохранено: "+error.message;
  }finally{living8Busy=false;}
}
$("living8-chat-approve").addEventListener("click",()=>decideLiving8(true));
$("living8-chat-reject").addEventListener("click",()=>decideLiving8(false));
$("living8-chat-details").addEventListener("click",async()=>{
  const dialog=$("settings-dialog");
  if(!dialog.open)dialog.showModal();
  $("living8").open=true;
  await refreshLiving8();
});

// Cognitive Soul 7.0 — shared V1–V6 registry and offline evidence review.
// No provider request or active model trial is launched by these controls.
async function refreshCognitive7(){
  if(!$("cognitive-v7").open)return;
  try{
    const [status,plan,health]=await Promise.all([
      requestJSON("/api/soul/cognitive7"),
      requestJSON("/api/soul/cognitive7/plan"),
      requestJSON("/api/soul/cognitive7/health")
    ]);
    $("v7-state").textContent="Общий реестр: "+status.unified_registry.count+
      " элементов · контрольных сравнений: "+
      status.unified_registry.user_attested_cases+
      " · SQLite: "+(health.healthy?"проверена":"нужна проверка")+
      " · внешних тестов Cloud.ru: 0.";
    const registry=$("v7-registry");registry.replaceChildren();
    for(const group of ["forge6","experience1","evolution4","academy"]){
      const list=status.unified_registry.items.filter(x=>x.source===group);
      addCognitiveLine(registry,group+": "+list.length+
        " элементов (выборка, без копирования исходных таблиц)");
    }
    const pane=$("v7-plan");pane.replaceChildren();
    for(const item of plan.tasks.slice(0,8)){
      const line=document.createElement("div");line.className="memory-record";
      addCognitiveLine(line,"Навык #"+item.skill_id+" · "+item.domain+
        " · парных проверок осталось: "+item.remaining_cases+
        " · регрессий: "+item.regressions+
        (item.ready?" · готов к ручному одобрению":" · нужна проверка"));
      const btn=document.createElement("button");btn.type="button";
      btn.className="quiet-button";btn.textContent="Открыть проверку";
      btn.addEventListener("click",()=>{
        $("v7-skill-id").value=String(item.skill_id);
        $("v7-test-panel").open=true;
      });
      line.append(btn);pane.append(line);
    }
    if(!plan.tasks.length)addCognitiveLine(pane,
      "Проверяемых черновиков пока нет. Новые навыки появятся из обратной связи и Skill Forge.");
  }catch(error){
    $("v7-state").textContent="Не удалось обновить Cognitive Soul 7.0: "+error.message;
  }
}
$("cognitive-v7").addEventListener("toggle",refreshCognitive7);
$("v7-refresh").addEventListener("click",refreshCognitive7);
function v7SelectedSkill(){
  const id=Number($("v7-skill-id").value);
  if(!Number.isSafeInteger(id)||id<=0)throw Error("Укажи положительный ID навыка.");
  return id;
}
$("v7-add-case").addEventListener("click",async()=>{
  try{
    const skill=v7SelectedSkill();
    if(!window.confirm("Ты действительно сравнил два ответа одной модели в сопоставимых условиях? "+
      "В SQLite запишется только хеш вопроса и результаты, без исходного текста. "+
      "Это пользовательские свидетельства, а не независимый тест LLM."))return;
    const result=await requestJSON("/api/soul/cognitive7/case","POST",{
      skill_id:skill,question:$("v7-question").value,
      expected:$("v7-expected").value,
      baseline_answer:$("v7-baseline").value,
      candidate_answer:$("v7-candidate").value,
      model:$("v7-model").value,confirmed:true,attested:true
    });
    $("v7-case-state").textContent="Контрольных пар: "+result.cases+
      "/"+result.min_cases+" · улучшение: "+result.net_gain+
      " · регрессии: "+result.regressions+
      (result.evidence_ready?" · готов к ручному одобрению":" · нужно продолжить проверку");
    for(const name of ["v7-question","v7-expected","v7-baseline","v7-candidate"])
      $(name).value="";
    await refreshCognitive7();await refreshForgeChatUI();
  }catch(error){$("v7-case-state").textContent=error.message;}
});
$("v7-verdict").addEventListener("click",async()=>{
  try{
    const report=await requestJSON("/api/soul/cognitive7/verdict?skill_id="+v7SelectedSkill());
    $("v7-case-state").textContent="Проверено "+report.cases+
      " пар · старый метод: "+report.baseline_correct+
      " · новый: "+report.candidate_correct+
      " · регрессий: "+report.regressions+
      " · подтверждение по данным пользователя: "+(report.evidence_ready?"да":"нет")+
      ". Автоматических облачных тестов не было.";
  }catch(error){$("v7-case-state").textContent=error.message;}
});
$("v7-approve").addEventListener("click",async()=>{
  try{
    const id=v7SelectedSkill();
    if(!window.confirm("Одобрить навык #"+id+
      " после проверки результатов на контрольных примерах? "+
      "Его можно отключить, если результаты ухудшатся."))return;
    const result=await requestJSON("/api/soul/cognitive7/approve","POST",
      {skill_id:id,confirmed:true});
    $("v7-case-state").textContent="Навык #"+id+" вручную одобрен. "+
      "Применение включено, история и исходники сохранены.";
    await refreshCognitive7();await refreshForgeChatUI();
  }catch(error){$("v7-case-state").textContent="Не одобрено: "+error.message;}
});

// Initiative is a REAL unfinished V8 local goal, never a fake assistant bubble.
let pendingDialogue9Goal=null;
async function refreshDialogue9Initiative(){
  try{
    // V8 already has an actionable proposal; avoid stacking duplicates.
    if(!$("living8-chat-proposal").hidden){
      $("dialogue9-initiative").hidden=true;
      pendingDialogue9Goal=null;
      return;
    }
    if(chatBusy||!activeSession){
      pendingDialogue9Goal=null;
      $("dialogue9-initiative").hidden=true;
      return;
    }
    const state=await requestJSON("/api/soul/dialogue9/initiative?"+
      new URLSearchParams({conversation_id:activeSession}));
    pendingDialogue9Goal=state.item||null;
    $("dialogue9-initiative").hidden=!pendingDialogue9Goal;
    if(pendingDialogue9Goal){
      $("dialogue9-initiative-text").textContent=pendingDialogue9Goal.label;
    }
  }catch(_error){
    pendingDialogue9Goal=null;
    $("dialogue9-initiative").hidden=true;
  }
}
$("dialogue9-initiative-open").addEventListener("click",async()=>{
  if(!pendingDialogue9Goal)return;
  const settings=$("settings-dialog");
  if(!settings.open)settings.showModal();
  $("living8").open=true;
  await refreshLiving8();
});
$("dialogue9-initiative-later").addEventListener("click",async()=>{
  if(!pendingDialogue9Goal)return;
  try{
    const p=pendingDialogue9Goal;
    await requestJSON("/api/soul/dialogue9/snooze","POST",{
      goal_id:p.goal_id,progress:p.progress,confirmed:true
    });
    $("chat-notice").textContent="Напоминание о локальной задаче скрыто на сутки.";
    await refreshDialogue9Initiative();
  }catch(error){
    $("chat-notice").textContent="Не удалось отложить предложение: "+error.message;
  }
});

// One visible switch; no complicated budget form in the normal workflow.
// ON is explicit permission for at most one potentially paid Cloud.ru call
// per UTC day. OFF prevents new auto calls, not requests already in flight.
let forgeAutoEnabled=false;
let pendingForgeProposal=null;
let forgeUIBusy=false;

async function refreshForgeChatUI(){
  try{
    const [auto,ready]=await Promise.all([
      requestJSON("/api/soul/forge/auto"),
      requestJSON("/api/soul/forge/ready")
    ]);
    forgeAutoEnabled=Boolean(auto.enabled);
    const quick=$("forge-chat-toggle");
    quick.textContent=forgeAutoEnabled?"Навыки · вкл":"Навыки · выкл";
    quick.setAttribute("aria-pressed",String(forgeAutoEnabled));
    quick.title=forgeAutoEnabled
      ?"Автосоздание черновиков Cloud.ru включено, максимум 1 платный вызов в сутки"
      :"Автосоздание черновиков Cloud.ru отключено";
    const full=$("forge-auto-toggle");
    full.textContent="Cloud.ru самообучение: "+(forgeAutoEnabled?"ВКЛ":"ВЫКЛ");
    full.setAttribute("aria-pressed",String(forgeAutoEnabled));
    const panel=$("forge-chat-proposal");
    pendingForgeProposal=ready.items[0]||null;
    panel.hidden=!pendingForgeProposal;
    if(pendingForgeProposal){
      const p=pendingForgeProposal;
      $("forge-chat-apply").textContent=p.can_approve
        ?"Применить навык":"Проверить навык";
      $("forge-chat-evidence-note").textContent=p.can_approve
        ?"Доступно ручное одобрение. Структурные тесты не доказывают качество модели."
        :"Для нового навыка 7.0 нужны минимум 8 подтверждённых сравнений со старым методом. Автоматических платных испытаний не будет.";
      const areas={logic:"Логика",coding:"Программирование",
                   legal:"Договоры",rag:"RAG и память",documents:"Документы"};
      $("forge-chat-proposal-text").textContent=
        "Хозяин, готов новый навык #"+p.id+" («"+
        (areas[p.domain]||p.domain)+"», версия "+p.version+"). "+
        "Шаги: "+p.steps.join(" → ")+
        ". Подтверждённых положительных оценок: "+p.source_count+".";
    }
  }catch(error){
    // The chat remains fully usable if Skill Forge is unavailable.
    $("forge-chat-proposal").hidden=true;
    $("forge-chat-toggle").title="Skill Forge: "+error.message;
  }
  await refreshLiving8Chat();
  await refreshDialogue9Initiative();
}
async function toggleForgeAuto(){
  if(forgeUIBusy)return;
  const next=!forgeAutoEnabled;
  if(next&&!window.confirm(
    "Включить автоматическое создание навыков через Cloud.ru?\n"+
    "Разрешается максимум ОДИН потенциально платный запрос в сутки. "+
    "Стоимость токенов неизвестна, денежный лимит это не гарантирует. "+
    "Навыки не будут применяться без твоего одобрения."))return;
  forgeUIBusy=true;
  $("forge-chat-toggle").disabled=true;
  $("forge-auto-toggle").disabled=true;
  try{
    await requestJSON("/api/soul/forge/auto","POST",{
      enabled:next,confirmed:true,acknowledge_paid:next
    });
    $("chat-notice").textContent=next
      ?"Автообучение Cloud.ru включено: максимум 1 вызов в сутки."
      :"Автообучение Cloud.ru выключено. Новых автоматических вызовов не будет.";
    await refreshForgeChatUI();
    if($("skill-forge").open) await refreshSkillForge();
  }catch(error){
    $("chat-notice").textContent="Не удалось изменить самообучение: "+error.message;
  }finally{
    forgeUIBusy=false;
    $("forge-chat-toggle").disabled=false;
    $("forge-auto-toggle").disabled=false;
  }
}
$("forge-chat-toggle").addEventListener("click",toggleForgeAuto);
$("forge-auto-toggle").addEventListener("click",toggleForgeAuto);
$("forge-chat-apply").addEventListener("click",async()=>{
  if(!pendingForgeProposal||forgeUIBusy)return;
  const p=pendingForgeProposal;
  if(!p.can_approve){
    const settings=$("settings-dialog");
    if(!settings.open)settings.showModal();
    $("cognitive-v7").open=true;
    $("v7-test-panel").open=true;
    $("v7-skill-id").value=String(p.id);
    await refreshCognitive7();
    return;
  }
  if(!window.confirm("Применить проверенный локально навык #"+p.id+
    " к будущим ответам? Реальная правильность ответов этим не доказана."))return;
  forgeUIBusy=true;
  try{
    await requestJSON("/api/soul/forge/decide","POST",{
      skill_id:p.id,approve:true,confirmed:true
    });
    $("chat-notice").textContent="Навык #"+p.id+" одобрен. Можно отключить его в настройках.";
    await refreshForgeChatUI();
  }catch(error){
    $("chat-notice").textContent="Навык не применён: "+error.message;
  }finally{forgeUIBusy=false;}
});
$("forge-chat-dismiss").addEventListener("click",async()=>{
  if(!pendingForgeProposal||forgeUIBusy)return;
  const p=pendingForgeProposal;
  forgeUIBusy=true;
  try{
    await requestJSON("/api/soul/forge/archive","POST",{
      skill_id:p.id,confirmed:true
    });
    $("chat-notice").textContent="Предложение #"+p.id+" отклонено. История сохранена.";
    await refreshForgeChatUI();
  }catch(error){
    $("chat-notice").textContent="Не удалось отклонить: "+error.message;
  }finally{forgeUIBusy=false;}
});
// Purely local, read-only polling: shows proposals created after a quiet
// background cycle even if the user has not sent a new message.
window.setInterval(()=>{
  if(!document.hidden&&!forgeUIBusy)refreshForgeChatUI();
},45000);

// Cognitive Soul 6.0: new skills are non-executable drafts until explicitly approved.
async function forgeAction(action, payload, notice) {
  try {
    const answer=await requestJSON("/api/soul/forge/"+action,"POST",payload);
    $("forge-result").textContent=notice+(answer.id?" · #"+answer.id:"")+
      (answer.state?" · "+answer.state:"");
    await refreshSkillForge();
    await refreshForgeChatUI();
  } catch(error) {
    $("forge-result").textContent="Не выполнено: "+error.message;
  }
}
function forgeButton(parent,label,handler) {
  const btn=document.createElement("button");
  btn.className="quiet-button";btn.type="button";
  btn.textContent=label;
  btn.addEventListener("click",handler);
  parent.append(btn);
}
async function refreshSkillForge(){
  if(!$("skill-forge").open) return;
  try{
    const domain=$("forge-domain").value;
    const [status,items,history,course,ranks]=await Promise.all([
      requestJSON("/api/soul/forge"),
      requestJSON("/api/soul/forge/skills"),
      requestJSON("/api/soul/forge/history"),
      requestJSON("/api/soul/forge/curriculum?domain="+encodeURIComponent(domain)),
      requestJSON("/api/soul/forge/compete?domain="+encodeURIComponent(domain))
    ]);
    $("forge-state").textContent="Навыков всего: "+status.skills+
      " · активных: "+(status.by_state.approved||0)+
      " · черновиков: "+(status.by_state.draft||0)+
      " · теневых: "+(status.by_state.shadow||0)+
      " · Cloud.ru в фоне: 0.";
    const budget=status.cloud_budget;
    $("forge-budget-status").textContent="Статус бюджета: "+budget.state+
      " · предложено "+budget.requested_calls+
      " · разрешено "+budget.approved_calls+
      " · использовано сегодня "+budget.today_reserved+
      ". Цена токенов не рассчитана.";
    const curriculum=$("forge-curriculum");
    curriculum.replaceChildren();
    addCognitiveLine(curriculum,"Программа "+domain+" · уровень: "+course.stage+
      " · структурных проверок: "+course.locally_tested_scenarios+
      " · одобренных навыков: "+course.approved+
      ". Проверка оценивает структуру, не качество модели.");
    const candidates=$("forge-candidates");
    candidates.replaceChildren();
    const relevant=items.items.filter(x=>x.domain===domain);
    for(const item of relevant.slice(0,40)){
      const panel=document.createElement("div");panel.className="memory-record";
      addCognitiveLine(panel,"Навык #"+item.id+" · v"+item.version+
        " · "+item.state+" · "+item.origin+
        " · источник: "+(item.parent_id??"нет")+
        " · положительных оценок: "+item.metrics.positive_sources);
      addCognitiveLine(panel,"Шаги: "+item.steps.join(" → "));
      addCognitiveLine(panel,"Теневой тест: "+item.metrics.structural_passes+
        "/"+item.metrics.scenario_count+
        " · реальные оценки: "+item.metrics.live_positive+
        "/"+item.metrics.live_ratings);
      if(item.state==="draft" || item.state==="shadow" || item.state==="approved"){
        forgeButton(panel,"Новая версия",()=>{
          if(window.confirm("Создать новую локальную версию навыка #"+item.id+" без активации?"))
            forgeAction("variant",{skill_id:item.id,confirmed:true},"Кандидат версии создан.");
        });
      }
      if(item.state==="shadow"){
        forgeButton(panel,"Одобрить навык",()=>{
          if(window.confirm("Включить навык #"+item.id+
            " для новых запросов области "+domain+"? Это изменение можно откатить."))
            forgeAction("decide",{skill_id:item.id,approve:true,confirmed:true},
                        "Навык включён вручную.");
        });
      }
      if(item.state==="approved"){
        forgeButton(panel,"Откатить",()=>{
          if(window.confirm("Отключить навык #"+item.id+" и сохранить историю?"))
            forgeAction("decide",{skill_id:item.id,approve:false,confirmed:true},
                        "Навык отключён.");
        });
      }
      if(["shadow","approved"].includes(item.state)){
        forgeButton(panel,"Перенести в другую область",()=>{
          const target=$("forge-domain").value;
          const different=["logic","coding","legal","rag","documents"].find(x=>x!==target);
          const chosen=window.prompt("Область переноса: logic, coding, legal, rag или documents",different);
          if(!chosen) return;
          if(window.confirm("Создать ТОЛЬКО черновик переноса в "+chosen+"?"))
            forgeAction("transfer",{skill_id:item.id,target:chosen,confirmed:true},
                        "Черновик переноса создан.");
        });
        forgeButton(panel,"Объединить с ID",()=>{
          const other=Number($("forge-combine-id").value);
          if(!Number.isSafeInteger(other)||other<=0){
            $("forge-result").textContent="Введи ID второго проверенного навыка.";
            return;
          }
          if(window.confirm("Объединить навыки #"+item.id+" и #"+other+
                           " в новый неактивный кандидат?"))
            forgeAction("compose",{left_id:item.id,right_id:other,
                                   confirmed:true},"Составной навык создан.");
        });
      }
      if(item.state!=="approved"&&item.state!=="archived"){
        forgeButton(panel,"Архивировать",()=>{
          if(window.confirm("Архивировать навык без удаления истории?"))
            forgeAction("archive",{skill_id:item.id,confirmed:true},
                        "Навык архивирован.");
        });
      }
      if(budget.state==="approved" && budget.approved_calls>budget.today_reserved
          && ["draft","shadow","approved"].includes(item.state)){
        forgeButton(panel,"Создать облачный черновик (1 вызов)",()=>{
          if(window.confirm("Отправить в Cloud.ru только названия разрешённых шагов "+
            "и область навыка? Это 1 потенциально платный вызов. "+
            "Никакие воспоминания или документы не отправятся."))
            forgeAction("cloud/draft",{skill_id:item.id,
                                      confirm_cloud_calls:true},
                        "Облачный ответ проверен. Кандидат остаётся черновиком.");
        });
      }
      candidates.append(panel);
    }
    if(!relevant.length) addCognitiveLine(candidates,
      "Пока нет навыков в этой области. Локальное обучение может создать черновик после положительных оценок.");
    const timeline=$("forge-history");timeline.replaceChildren();
    addCognitiveLine(timeline,"Сравнение процедур (только структурная эвристика): "+
      (ranks.ranked.slice(0,3).map(x=>"#"+x.id+": "+x.structural_passes+"/4").join(" · ")||"нет результатов"));
    for(const event of history.items.slice(0,8)){
      addCognitiveLine(timeline,"#"+event.skill_id+" · "+event.kind+
        " · "+event.created_at);
    }
  }catch(error){
    $("forge-state").textContent="Skill Forge недоступен: "+error.message;
  }
}
$("skill-forge").addEventListener("toggle",refreshSkillForge);
$("forge-domain").addEventListener("change",refreshSkillForge);
$("forge-refresh").addEventListener("click",refreshSkillForge);
$("forge-propose").addEventListener("click",()=>{
  if(!window.confirm("Создать безопасный неактивный шаблон навыка в "+$("forge-domain").value+"?"))return;
  forgeAction("propose",{domain:$("forge-domain").value,
                        confirmed:true},"Кандидат сохранён.");
});
$("forge-shadow").addEventListener("click",()=>{
  if(!window.confirm("Проверить максимум 8 черновиков локально без Cloud.ru?"))return;
  forgeAction("shadow",{limit:8,confirmed:true},
              "Теневая структурная проверка завершена.");
});
$("forge-budget-request").addEventListener("click",()=>{
  const calls=Number($("forge-budget-calls").value);
  if(!Number.isSafeInteger(calls)||calls<1||calls>20){
    $("forge-result").textContent="Выбери 1–20 вызовов в сутки.";return;
  }
  if(!window.confirm("Подготовить предложение на "+calls+
      " потенциально платных Cloud.ru-вызовов в сутки? Само по себе это НЕ разрешает расходы."))return;
  forgeAction("budget/request",{calls,confirmed:true},
              "Предложение бюджета подготовлено. Ожидается отдельное одобрение.");
});
$("forge-budget-approve").addEventListener("click",()=>{
  if(!window.confirm("Отдельное согласование: ты ознакомился со стоимостью "+
                     "Cloud.ru и согласен с ранее предложенным лимитом?"))return;
  if(!window.confirm("Подтверди ещё раз: точная денежная стоимость токенов "+
                     "программой не рассчитывается. Каждый вызов останется ручным."))return;
  forgeAction("budget/decide",{approve:true,confirmed:true,
                              ack_unpriced:true},
              "Лимит вызовов отдельно согласован. Автозапуск Cloud.ru запрещён.");
});
$("forge-budget-revoke").addEventListener("click",()=>{
  if(!window.confirm("Отозвать все разрешения на будущие Cloud.ru-вызовы Skill Forge?"))return;
  forgeAction("budget/decide",{approve:false,confirmed:true},
              "Облачные вызовы Skill Forge заблокированы.");
});

// Three opt-in cognitive extensions live in the existing settings panel.
function addCognitiveLine(parent, label) {
  const line=document.createElement("p");
  line.className="memory-empty";
  line.textContent=label;
  parent.append(line);
  return line;
}

async function cognitiveAction(path, data, message) {
  try {
    await requestJSON(path,"POST",data);
    $("cognitive-tools-feedback").textContent=message;
    await refreshCognitiveTools();
    await refreshCognition();
  } catch(error) {
    $("cognitive-tools-feedback").textContent="Не выполнено: "+error.message;
  }
}

async function refreshCognitiveTools() {
  if (!$("cognitive-tools").open) return;
  try {
    const [skills, evolution]=await Promise.all([
      requestJSON("/api/soul/skills"),
      requestJSON("/api/soul/evolution")
    ]);
    const skillPane=$("skill-candidates");
    skillPane.replaceChildren();
    for(const skill of skills.items) {
      const row=document.createElement("div");
      row.className="memory-record";
      const text=addCognitiveLine(row,"Навык #"+skill.id+" · "+skill.state+
        " · "+skill.guidance);
      text.title="Основание: ответ #"+skill.message_id;
      if(skill.state==="pending" || skill.state==="approved") {
        const accept=skill.state==="pending";
        const button=document.createElement("button");
        button.type="button";button.className="quiet-button";
        button.textContent=accept?"Одобрить":"Отозвать";
        button.addEventListener("click",()=>{
          if(!window.confirm(accept
             ?"Применять эту рекомендацию в будущих ответах?"
             :"Отключить этот навык?")) return;
          cognitiveAction("/api/soul/skills/decide",
            {candidate_id:skill.id,approve:accept,confirmed:true},
            accept?"Навык одобрен.":"Навык отключён.");
        });
        row.append(button);
      }
      skillPane.append(row);
    }
    if(!skills.items.length) addCognitiveLine(skillPane,
      "Пока нет кандидатов. Поставь положительную оценку ответу и поясни почему.");
    const trialPane=$("evolution-trials");
    trialPane.replaceChildren();
    for(const trial of evolution.trials) {
      const row=document.createElement("div");
      row.className="memory-record";
      const base=trial.arms.baseline, cand=trial.arms.candidate;
      addCognitiveLine(row,trial.strategy+" · "+trial.state+
        " · обычная "+base.helpful+"/"+base.samples+
        " · кандидат "+cand.helpful+"/"+cand.samples+
        (trial.gain===null?" · данных недостаточно":
         " · разница "+Math.round(trial.gain*100)+" п.п."));
      let action=null, label="", data=null;
      if(trial.state==="draft") {
        action="/api/soul/evolution/start";label="Начать тест";
        data={trial_id:trial.id,confirmed:true};
      } else if(trial.state==="testing" && trial.ready_for_review) {
        action="/api/soul/evolution/decide";label="Применить";
        data={trial_id:trial.id,activate:true,confirmed:true};
      }
      if(action) {
        const button=document.createElement("button");
        button.type="button";button.className="quiet-button";
        button.textContent=label;
        button.addEventListener("click",()=>{
          if(window.confirm("Подтвердить: "+label+" для "+trial.strategy+"?")) {
            cognitiveAction(action,data,"Решение подтверждено.");
          }
        });
        row.append(button);
      }
      if(trial.state==="active" || trial.state==="testing") {
        const stop=document.createElement("button");
        stop.type="button";stop.className="quiet-button";
        stop.textContent="Отключить";
        stop.addEventListener("click",()=>{
          if(window.confirm("Прекратить применение этой стратегии?")) {
            cognitiveAction("/api/soul/evolution/decide",
              {trial_id:trial.id,activate:false,confirmed:true},"Стратегия отключена.");
          }
        });
        row.append(stop);
      }
      trialPane.append(row);
    }
    if(!evolution.trials.length) addCognitiveLine(trialPane,
      "Эксперименты ещё не создавались. Все стратегии выключены.");
  } catch(error) {
    $("cognitive-tools-feedback").textContent=
      "Не удалось загрузить Cognitive Soul: "+error.message;
  }
}

$("cognitive-tools").addEventListener("toggle",refreshCognitiveTools);
$("evolution-propose").addEventListener("click",async()=>{
  const strategy=$("evolution-strategy").value;
  await cognitiveAction("/api/soul/evolution/propose",{strategy},
    "Кандидат создан. Он ещё не активирован.");
});
$("reflection-run").addEventListener("click",async()=>{
  const query=$("reflection-query").value.trim();
  const target=$("reflection-result");
  target.replaceChildren();
  if(!query) {
    addCognitiveLine(target,"Введите вопрос об истории решения.");
    return;
  }
  try {
    const data=await requestJSON("/api/soul/reflect","POST",{query,limit:8});
    for(const entry of data.items) {
      const correction=entry.revisions.some(x=>x.operation==="correct")
        ?" · история исправлений: "+entry.revisions.length:"";
      addCognitiveLine(target,"Память #"+entry.id+" · "+entry.content+
        correction+(entry.source_exists?" · источник сохранён":" · источник требует проверки"));
    }
    for(const warning of data.uncertainties.slice(0,8)) {
      addCognitiveLine(target,"Проверь: "+warning);
    }
    if(!data.items.length) addCognitiveLine(target,"Совпадений не найдено.");
  } catch(error) {
    addCognitiveLine(target,"Ошибка поиска: "+error.message);
  }
});

// Cognitive Soul 2.0 uses opt-in, same-origin local endpoints only.
function showNext(result) {
  const target=$("next-result");
  target.replaceChildren();
  const text=document.createElement("pre");
  text.className="next-output";
  text.textContent=typeof result==="string"?result:
    JSON.stringify(result,null,2).slice(0,7000);
  target.append(text);
}
async function nextPost(endpoint,body,done) {
  try {
    const result=await requestJSON("/api/soul/next/"+endpoint,"POST",body);
    showNext(result);
    if(done) await refreshNext();
  } catch(error) {
    showNext("Ошибка: "+error.message);
  }
}
async function refreshNext() {
  if(!$("cognitive-next").open) return;
  try {
    const [state,understanding,skills,personality,questions,simulations]=
      await Promise.all([
        requestJSON("/api/soul/next/state"),
        requestJSON("/api/soul/next/understanding"),
        requestJSON("/api/soul/next/skills"),
        requestJSON("/api/soul/next/personality"),
        requestJSON("/api/soul/next/questions"),
        requestJSON("/api/soul/next/simulations")
      ]);
    $("next-state").textContent="Активных целей: "+state.active_goals+
      " · ожидают подтверждения воспоминания: "+state.pending_memory_reviews+
      " · вопросы: "+state.pending_research_questions+
      " · оценено ответов: "+state.rated_answers+
      " · устойчивых привычек: "+personality.approved_habits.length+
      " · повторяющихся успешных навыков: "+skills.repeatable_candidates.length+
      " · испытаний: "+simulations.items.length+
      ". Установленная модель состояния не означает реальных чувств.";
    const goalPane=$("next-goals");
    goalPane.replaceChildren();
    for(const goal of understanding.user_defined_goals) {
      const line=document.createElement("div");
      line.className="memory-record";
      addCognitiveLine(line,"Цель #"+goal.id+" · "+goal.state+" · "+goal.title);
      const button=document.createElement("button");
      button.type="button"; button.className="quiet-button";
      button.textContent=goal.state==="active"?"Завершить":"Активировать";
      const nextState=goal.state==="active"?"done":"active";
      button.addEventListener("click",()=>{
        if(window.confirm("Изменить состояние цели #"+goal.id+"?")) {
          nextPost("goal/state",{id:goal.id,state:nextState,confirmed:true},true);
        }
      });
      line.append(button);
      goalPane.append(line);
    }
    const questionsPane=$("next-research");
    questionsPane.replaceChildren();
    for(const question of questions.items.filter(x=>x.state==="pending").slice(0,10)) {
      const line=document.createElement("div");
      line.className="memory-record";
      addCognitiveLine(line,"Вопрос #"+question.id+" · "+question.question);
      for(const state of ["approved","dismissed"]) {
        const b=document.createElement("button");
        b.className="quiet-button";b.type="button";
        b.textContent=state==="approved"?"Сохранить для исследования":"Отклонить";
        b.addEventListener("click",()=>{
          if(window.confirm("Подтвердить решение по вопросу #"+question.id+"?")) {
            nextPost("question/decide",{id:question.id,state,confirmed:true},true);
          }
        });
        line.append(b);
      }
      questionsPane.append(line);
    }
  } catch(error) {
    $("next-state").textContent="Когнитивная диагностика недоступна: "+error.message;
  }
}
$("cognitive-next").addEventListener("toggle",refreshNext);
$("next-causal").addEventListener("click",()=>nextPost("causal",
  {query:$("next-query").value,limit:8},false));
$("next-gap").addEventListener("click",()=>nextPost("gap",
  {query:$("next-query").value},false));
$("next-save-question").addEventListener("click",()=>{
  const question=$("next-query").value.trim();
  if(!window.confirm("Сохранить этот вопрос в локальную очередь исследований?")) return;
  nextPost("question/save",{question,confirmed:true},true);
});
$("next-selfcheck").addEventListener("click",()=>nextPost("selfcheck",
  {message_id:Number($("next-message-id").value)},false));
$("next-review").addEventListener("click",()=>nextPost("memory/review",
  {limit:100},false));
$("next-summary").addEventListener("click",async()=>{
  try {
    const [understanding, personality, skills]=await Promise.all([
      requestJSON("/api/soul/next/understanding"),
      requestJSON("/api/soul/next/personality"),
      requestJSON("/api/soul/next/skills")
    ]);
    showNext({understanding,personality,skills});
  } catch(error) { showNext("Ошибка: "+error.message); }
});
$("next-add-goal").addEventListener("click",()=>{
  const title=$("next-goal").value.trim();
  if(!window.confirm("Сохранить эту цель в локальную SQLite?")) return;
  nextPost("goal/save",{title,confirmed:true},true);
});
$("next-scenario").addEventListener("click",()=>nextPost("scenario",{
  decision:$("next-query").value,
  option_a:$("next-scenario-a").value,
  option_b:$("next-scenario-b").value
},false));
$("next-simulate").addEventListener("click",()=>{
  if(!window.confirm("Выполнить локальный контрольный тест и сохранить только оценку/хеш?")) return;
  nextPost("simulate",{
    strategy:$("next-sim-strategy").value,
    skill:$("next-sim-skill").value,
    answer:$("next-sim-answer").value,
    confirmed:true
  },true);
});

// Cognitive Soul 3.0: no paid work without a click and explicit consent.
async function improvementAction(path,body,message) {
  try {
    const result=await requestJSON("/api/soul/improvement/"+path,"POST",body);
    $("improve-feedback").textContent=message+
      (result.run ? " · эталонов "+result.run.cases+
       " · выигрыш "+Math.round((result.run.gain??0)*100)+" п.п." : "");
    await refreshImprovement();
  } catch(error) {
    $("improve-feedback").textContent="Не выполнено: "+error.message;
  }
}
async function refreshImprovement() {
  if(!$("self-improvement").open) return;
  try {
    const [report,cases,diagnostics]=await Promise.all([
      requestJSON("/api/soul/improvement"),
      requestJSON("/api/soul/improvement/cases"),
      requestJSON("/api/soul/improvement/diagnostics")
    ]);
    const diagnosticPane=$("improvement-diagnostics");
    diagnosticPane.replaceChildren();
    const negatives=diagnostics.negative_feedback_by_category
      .map(x=>x.category+": "+x.n).join(", ");
    addCognitiveLine(diagnosticPane,
      "Проблемы по отзывам: "+(negatives||"пока нет данных")+
      " · контрольных заданий: "+(report.case_counts.holdout||0)+
      "/4 · активный вариант: "+(report.active_id??"нет"));
    const casePane=$("improve-cases");
    casePane.replaceChildren();
    for(const testCase of cases.items) {
      const row=document.createElement("div");
      row.className="memory-record";
      addCognitiveLine(row,"#"+testCase.id+" ["+testCase.split+"] "+
        testCase.question+" · эталон: "+testCase.expected);
      const remove=document.createElement("button");
      remove.type="button";remove.className="quiet-button";
      remove.textContent="Удалить эталон";
      remove.addEventListener("click",async()=>{
        if(!window.confirm("Удалить эталон? Использованные в тестах задания удалять нельзя.")) return;
        await improvementAction("case/delete",{id:testCase.id,confirmed:true},
          "Эталон удалён.");
      });
      row.append(remove);casePane.append(row);
    }
    if(!cases.items.length) addCognitiveLine(casePane,
      "Добавь четыре независимых проверенных эталона для реального сравнения.");
    const pane=$("improve-proposals");
    pane.replaceChildren();
    for(const proposal of report.proposals) {
      const block=document.createElement("div");
      block.className="memory-record";
      const run=proposal.last_run;
      addCognitiveLine(block,"Гипотеза #"+proposal.id+" · "+proposal.strategy+
        " · "+proposal.state+" · "+proposal.rationale);
      if(run) {
        addCognitiveLine(block,"Последний тест #"+run.id+" · "+run.status+
          " · вызовов: "+run.provider_calls+
          " · базовая стратегия: "+run.baseline_correct+"/"+run.cases+
          " · новая: "+run.candidate_correct+"/"+run.cases+
          " · регрессий: "+run.regressions+
          " · выигрыш: "+(run.gain===null?"нет данных":Math.round(run.gain*100)+" п.п.")+
          " · среднее время: "+run.baseline_mean_ms+"/"+run.candidate_mean_ms+" мс"+
          (run.ready_for_review?" · готова к подтверждению":""));
      }
      const controls=[];
      if(proposal.state==="proposed" && !run?.ready_for_review) {
        controls.push(["Проверить Cloud.ru",async()=>{
          if((report.case_counts.holdout||0)<4) {
            $("improve-feedback").textContent="Сначала добавь четыре эталонных задания holdout.";
            return;
          }
          if(!window.confirm("Отправить 4 вопроса в Cloud.ru: максимум 8 платных запросов? Эталонные ответы в облако не отправляются.")) return;
          await improvementAction("run",{proposal_id:proposal.id,max_pairs:4,
            confirm_cloud_calls:true},"Парное сравнение завершено");
        }]);
      } else if(proposal.state==="proposed" && run?.ready_for_review) {
        controls.push(["Повторить тест",async()=>{
          if(!window.confirm("Снова выполнить 8 платных запросов Cloud.ru?")) return;
          await improvementAction("run",{proposal_id:proposal.id,max_pairs:4,
            confirm_cloud_calls:true},"Повторная проверка");
        }]);
        controls.push(["Одобрить улучшение",async()=>{
          if(!window.confirm("Применять эту стратегию в дальнейших диалогах? Можно откатить.")) return;
          await improvementAction("decide",{proposal_id:proposal.id,approve:true,
            confirmed:true},"Улучшение одобрено.");
        }]);
      }
      if(run?.status==="running") {
        controls.push(["Закрыть прерванный тест",async()=>{
          if(!window.confirm("Убедись, что тест не выполняется. Пометить его неудачным?")) return;
          await improvementAction("recover",{run_id:run.id,confirmed:true},
            "Прерванный тест закрыт без активации.");
        }]);
      }
      if(report.active_id===proposal.id) {
        controls.push(["Откатить",async()=>{
          if(!window.confirm("Откатить активную стратегию и вернуть предыдущую?")) return;
          await improvementAction("decide",{proposal_id:proposal.id,approve:false,
            confirmed:true},"Стратегия откатилась.");
        }]);
      }
      for(const [name,act] of controls) {
        const button=document.createElement("button");
        button.type="button";button.className="quiet-button";
        button.textContent=name;
        button.addEventListener("click",act);
        block.append(button);
      }
      pane.append(block);
    }
    if(!report.proposals.length) addCognitiveLine(pane,
      "Гипотез нет. Они могут появляться после повторных ошибок и отрицательных оценок.");
  } catch(error) {
    $("improve-feedback").textContent=
      "Не удалось открыть самоулучшение: "+error.message;
  }
}
$("self-improvement").addEventListener("toggle",refreshImprovement);
$("improve-add-case").addEventListener("click",async()=>{
  const question=$("improve-question").value.trim();
  const expected=$("improve-expected").value.trim();
  const split=$("improve-split").value;
  if(!window.confirm("Сохранить вопрос и эталон только в локальной SQLite?")) return;
  await improvementAction("case",{question,expected,split,confirmed:true},
    "Эталон сохранён локально.");
});
$("improve-propose").addEventListener("click",async()=>{
  if(!window.confirm("Создать новую гипотезу? Без облачных вызовов и без активации.")) return;
  await improvementAction("propose",{strategy:$("improve-strategy").value,
    confirmed:true},"Гипотеза добавлена.");
});

// Cognitive Soul 4.0: all generated strategies are drafts until approved.
async function evol4Action(endpoint,payload,success) {
  try {
    const result=await requestJSON("/api/soul/evolution4/"+endpoint,"POST",payload);
    $("evol4-diagnostics").textContent=success+" "+
      JSON.stringify(result,null,2).slice(0,2200);
    await refreshEvolution4();
  } catch(error) {
    $("evol4-diagnostics").textContent="Не выполнено: "+error.message;
  }
}
async function refreshEvolution4() {
  if(!$("evolution4-details").open) return;
  try {
    const report=await requestJSON("/api/soul/evolution4");
    const domain=$("evol4-domain").value;
    const active=report.active.find(x=>x.domain===domain);
    const tests=report.cases.find(x=>x.domain===domain && x.split==="holdout");
    $("evol4-overview").textContent="Область: "+domain+
      " · тесты: "+(tests?.n||0)+" / 30"+
      " · активная стратегия: "+(active?.candidate_id??"отсутствует")+
      " · запросов на эксперименты сегодня: "+report.daily_calls_reserved+
      " / "+report.daily_call_limit+
      ". Генерация и учебная программа — без облака.";
    const pane=$("evol4-candidates");
    pane.replaceChildren();
    for(const candidate of report.candidates.filter(x=>x.domain===domain).slice(0,35)) {
      const card=document.createElement("div");
      card.className="memory-record";
      addCognitiveLine(card,"Кандидат #"+candidate.id+" · поколение "+
        candidate.generation+" · "+candidate.state+
        " · методы: "+candidate.recipe.join(", ")+
        " · предок: "+(candidate.parent_id??"нет"));
      for(const score of candidate.own_results) {
        addCognitiveLine(card,"Проверено "+score.cases+" /30 · модель "+
          score.model+" · базовый "+score.baseline_correct+"/"+score.cases+
          " · новый "+score.candidate_correct+"/"+score.cases+
          " · регрессий "+score.regressions+
          " · улучшение "+Math.round(score.gain*100)+" п.п."+
          " · средняя задержка "+score.mean_baseline_ms+
          "/"+score.mean_candidate_ms+" мс");
      }
      for(const score of candidate.transfer_results) {
        addCognitiveLine(card,"Проверка переноса: "+score.domain+
          ", "+score.cases+" задач, разница "+Math.round(score.gain*100)+" п.п.");
      }
      const controls=[];
      if(candidate.state==="proposed" && !candidate.running) {
        controls.push(["Тестировать парно (12 вызовов)",async()=>{
          const evalDomain=$("evol4-test-domain").value;
          if(!window.confirm("Разрешить до 12 платных запросов Cloud.ru для 6 новых holdout-вопросов области "+evalDomain+"? Эталоны остаются локально.")) return;
          await evol4Action("evaluate",{candidate_id:candidate.id,
            evaluation_domain:evalDomain,batch_size:6,
            confirm_cloud_calls:true},"Парное испытание завершено.");
        }]);
        if(candidate.eligible) {
          controls.push(["Применить после испытаний",async()=>{
            if(!window.confirm("Применить стратегию #"+candidate.id+
              " для области "+domain+"? Будет доступен ручной откат.")) return;
            await evol4Action("decision",{candidate_id:candidate.id,
              approve:true,confirmed:true},"Стратегия активирована.");
          }]);
        }
      }
      if(candidate.last_status==="running") {
        controls.push(["Закрыть прерванный тест",async()=>{
          if(!window.confirm("Убедись, что тест больше не выполняется. Пометить его неудачным?")) return;
          await evol4Action("recover",{run_id:candidate.last_run_id,
            confirmed:true},"Прерванный тест закрыт.");
        }]);
      }
      if(candidate.state==="active") {
        controls.push(["Откатить",async()=>{
          if(!window.confirm("Отключить активную стратегию #"+candidate.id+"?")) return;
          await evol4Action("decision",{candidate_id:candidate.id,
            approve:false,confirmed:true},"Стратегия отключена.");
        }]);
      }
      controls.push(["Многоуровневая проверка",async()=>{
        await evol4Action("peer-review",{candidate_id:candidate.id},
          "Аудит стратегии.");
      }]);
      for(const [label,fn] of controls) {
        const button=document.createElement("button");
        button.type="button";
        button.className="quiet-button";
        button.textContent=label;
        button.addEventListener("click",fn);
        card.append(button);
      }
      pane.append(card);
    }
    if(!pane.childElementCount) addCognitiveLine(pane,
      "Нет гипотез. Создай несколько локальных вариантов, затем добавь эталоны.");
  } catch(error) {
    $("evol4-overview").textContent="Ошибка Cognitive Soul 4.0: "+error.message;
  }
}
$("evolution4-details").addEventListener("toggle",refreshEvolution4);
$("evol4-domain").addEventListener("change",()=>{
  $("evol4-test-domain").value=$("evol4-domain").value;
  refreshEvolution4();
});
$("evol4-generate").addEventListener("click",()=>{
  if(!window.confirm("Создать четыре новых безопасных варианта локально? Они не изменят поведение до подтверждения.")) return;
  evol4Action("generate",{domain:$("evol4-domain").value,count:4,
    confirmed:true},"Созданы гипотезы.");
});
$("evol4-add-case").addEventListener("click",()=>{
  const domain=$("evol4-domain").value;
  const question=$("evol4-question").value.trim();
  const expected=$("evol4-gold").value.trim();
  const split=$("evol4-split").value;
  if(!window.confirm("Сохранить контрольный вопрос и правильный ответ в локальную SQLite?")) return;
  evol4Action("case",{domain,question,expected,split,confirmed:true},
    "Эталон сохранён.");
});
$("evol4-curriculum").addEventListener("click",async()=>{
  try {
    const domain=$("evol4-domain").value;
    const data=await requestJSON("/api/soul/evolution4/curriculum?domain="+
      encodeURIComponent(domain));
    $("evol4-diagnostics").textContent=JSON.stringify(data,null,2);
  } catch(error) {
    $("evol4-diagnostics").textContent="Учебная программа: "+error.message;
  }
});

$("academy-enabled").addEventListener("change",async () => {
  const control=$("academy-enabled");
  const value=control.checked;
  control.disabled=true;
  try {
    await requestJSON("/api/soul/academy/enabled","POST",{enabled:value});
    await refreshCognition();
  } catch (error) {
    control.checked=!value;
    $("chat-notice").textContent="Настройка SOUL ACADEMY: "+error.message;
  } finally {
    control.disabled=false;
  }
});

$("cognitive-details").addEventListener("toggle", () => {
  if ($("cognitive-details").open) refreshCognition();
});
$("vector-details").addEventListener("toggle", () => {
  if ($("vector-details").open) refreshCognition();
});
$("settings-open").addEventListener("click", refreshCognition);

// Avoid constant UI/API work when browser is hidden or user is offline.
window.setInterval(() => {
  if (!document.hidden) refreshBrain();
}, 30000);


// v0.7 diagnostics are local by default. The Cloud.ru live check remains
// an explicit separate button and NEVER runs automatically on page open.
async function refreshDiagnostics() {
  const target = $("diagnostics-results");
  target.replaceChildren();
  try {
    const [report, workload, backups, selftest, migration, budget, evidence,
      atlasHealth, wal] = await Promise.all([
      requestJSON("/api/system/diagnostics"),
      requestJSON("/api/system/workload"),
      requestJSON("/api/system/backups"),
      requestJSON("/api/system/selftest"),
      requestJSON("/api/system/migrations"),
      requestJSON("/api/system/cloud-budget"),
      requestJSON("/api/soul/evidence/audits"),
      requestJSON("/api/soul/atlas/cache"),
      requestJSON("/api/system/wal"),
    ]);
    $("atlas-health").textContent=
      "Карта SQLite: "+(atlasHealth.verified?"✓":"требует восстановления индекса")+
      " · записей "+atlasHealth.source_count+
      " · агрегатов "+atlasHealth.cached_count+
      " · WAL "+wal.journal_mode+
      " · внешний ключ: "+(wal.foreign_key_errors?"ошибка":"OK")+
      ". Реальный браузер и Cloud.ru проверяются отдельно.";
    const evidencePane=$("evidence-audit-summary");
    evidencePane.replaceChildren();
    const entry=document.createElement("p");
    entry.className="memory-empty";
    const flagged=evidence.items.filter(x=>x.label==="review_required").length;
    entry.textContent="Аудит ответов: "+evidence.items.length+
      " проверено · требуют внимания: "+flagged+
      " · проверяется наличие цитат, не фактическая истинность.";
    evidencePane.append(entry);
    $("budget-chat").value=budget.limits.chat_per_day;
    $("budget-embeddings").value=budget.limits.embeddings_per_day;
    $("budget-minute").value=budget.limits.per_minute;
    $("budget-status").textContent=
      "Сегодня: диалогов "+(budget.today.chat||0)+"/"+budget.limits.chat_per_day+
      ", embeddings "+(budget.today.embeddings||0)+"/"+
      budget.limits.embeddings_per_day+
      ". Это число попыток запросов, не счёт Cloud.ru.";
    const eventsPane=$("budget-events");
    eventsPane.replaceChildren();
    for (const event of budget.recent_events.slice(0,6)) {
      const entry=document.createElement("p");
      entry.className="memory-empty";
      entry.textContent=event.created_at+" · "+event.event+" · "+event.reason;
      eventsPane.append(entry);
    }
    const snapshotSelect=$("recovery-snapshot");
    const previousName=snapshotSelect.value;
    snapshotSelect.replaceChildren();
    for (const entry of backups.items) {
      const option=document.createElement("option");
      option.value=entry.filename;option.textContent=entry.filename;
      snapshotSelect.append(option);
    }
    if (backups.items.some(x=>x.filename===previousName)) {
      snapshotSelect.value=previousName;
    }
    const required=["cloud-connect","cloud-model","cloud-select","cloud-diagnose",
      "message","send-message","stop-message","memory-save","memory-search",
      "precision-search","rag-search","memory-backup","run-system-check",
      "academy-enabled","layer-selector","soul-canvas"];
    const missing=required.filter(id=>!$(id));
    const hasCanvas=Boolean(canvas.getContext("2d"));
    $("browser-selftest").textContent=
      (!missing.length && hasCanvas ? "✓" : "!")+
      " Интерфейс: "+(required.length-missing.length)+"/"+required.length+
      " элементов · Canvas: "+(hasCanvas?"OK":"ошибка")+
      (missing.length ? " · отсутствуют: "+missing.join(", ") : "")+
      " · это проверка DOM, а не нажатий всех кнопок."+
      " Резервные копии: "+selftest.backups.healthy+
      " исправных из "+selftest.backups.checked+
      ", повреждённых: "+selftest.backups.invalid+
      ". Реальный Cloud.ru — отдельной кнопкой.";
    for (const [name, info] of Object.entries(report.checks)) {
      const item = document.createElement("p");
      item.className = "memory-empty";
      item.textContent = (info.status === "passed" ? "✓ " :
        info.status === "pending" ? "· " : "! ") + name +
        ": " + info.status + (info.integrity ? " · " + info.integrity : "");
      target.append(item);
    }
    const q = workload.usage;
    $("workload-status").textContent =
      "Очередь обслуживания: " + workload.queue_depth +
      " · оценка токенов (не данные провайдера): запросы " + q.approx_prompt_tokens +
      ", ответы " + q.approx_answer_tokens +
      " · резервных копий " + backups.items.length +
      " · схема SQLite v"+migration.version+
      (migration.fts_missing_rows ? " · неполный FTS индекс: "+migration.fts_missing_rows : "")+
      ". Общение имеет приоритет; фоновые задачи не вызывают Cloud.ru.";
  } catch (error) {
    const item = document.createElement("p");
    item.className = "memory-empty";
    item.textContent = "Ошибка диагностики: " + error.message;
    target.append(item);
  }
}
$("run-system-check").addEventListener("click", refreshDiagnostics);
$("system-diagnostics").addEventListener("toggle", () => {
  if ($("system-diagnostics").open) refreshDiagnostics();
});
$("diagnostics-backup").addEventListener("click", async () => {
  const button = $("diagnostics-backup");
  button.disabled = true;
  try {
    const result = await requestJSON("/api/system/backup", "POST", {});
    $("workload-status").textContent = "Проверенная резервная копия: " + result.name +
      ". Восстановление только после остановки сервера.";
    await refreshDiagnostics();
  } catch (error) {
    $("workload-status").textContent = "Ошибка резервного копирования: " + error.message;
  } finally {
    button.disabled = false;
  }
});

async function refreshMemoryLayers() {
  const target = $("layer-results");
  const layer = $("layer-selector").value;
  try {
    const params = new URLSearchParams({layer, limit: "35"});
    if (layer === "short_term" && activeSession) {
      params.set("conversation_id", activeSession);
    }
    const response = await requestJSON("/api/soul/layers?" + params);
    target.replaceChildren();
    if (response.links?.length) {
      const summary = document.createElement("p");
      summary.className = "memory-empty";
      const cross = response.links.filter(link => link.source_layer !== link.target_layer).length;
      summary.textContent = "Связей между воспоминаниями: " + response.links.length +
        " · между разными уровнями: " + cross +
        ". Связи — сигналы, а не доказанные причинно-следственные отношения.";
      target.append(summary);
    }
    if (!response.items.length) {
      const empty = document.createElement("p");
      empty.className = "memory-empty";
      empty.textContent = "В этом уровне пока нет сохранённых записей.";
      target.append(empty);
    }
    for (const memory of response.items) {
      const node = document.createElement("div");
      node.className = "memory-record";
      const label = document.createElement("small");
      label.textContent = memory.layer + " · источник: " +
        (memory.source_ref || memory.source_type) +
        " · приоритет: " + memory.priority +
        (memory.revision_id ? " · версия: " + memory.revision_id : "");
      const body = document.createElement("p");
      body.textContent = memory.content;
      node.append(label, body);
      target.append(node);
    }
  } catch (error) {
    target.textContent = "Ошибка чтения уровней памяти: " + error.message;
  }
}
$("layer-selector").addEventListener("change", refreshMemoryLayers);
$("memory-layers").addEventListener("toggle", () => {
  if ($("memory-layers").open) refreshMemoryLayers();
});

async function refreshAudits() {
  try {
    const report = await requestJSON("/api/soul/audits");
    const unresolved = report.items.filter(x => x.state === "needs_review").length;
    $("audit-status").textContent = "Проверка ссылок на память: " +
      report.items.length + " завершённых аудитов · требуют просмотра: " +
      unresolved + ". Это не автоматическая проверка истинности ответов.";
  } catch (error) {
    $("audit-status").textContent = "Аудит источников: " + error.message;
  }
}
$("cognitive-details").addEventListener("toggle", () => {
  if ($("cognitive-details").open) refreshAudits();
});

canvas.addEventListener("keydown", (event) => {
  const key = event.key;
  if (key==="Escape" && atlasView.active) {
    event.preventDefault();
    atlasView.back().then(() => {if (reducedMotion) paint();}).catch(
      (error) => { $("soul-live-state").textContent=error.message; }
    );
    return;
  }
  if (key === "+" || key === "=") graphScale = Math.min(3.2, graphScale * 1.13);
  else if (key === "-") graphScale = Math.max(.45, graphScale / 1.13);
  else if (key === "ArrowLeft") graphPanX += 22;
  else if (key === "ArrowRight") graphPanX -= 22;
  else if (key === "ArrowUp") graphPanY += 22;
  else if (key === "ArrowDown") graphPanY -= 22;
  else if (key === "Home") {graphScale = 1; graphPanX = 0; graphPanY = 0;}
  else return;
  event.preventDefault();
  if (reducedMotion) paint();
});

$("restore-test").addEventListener("click", async () => {
  const name=$("recovery-snapshot").value;
  if (!name) {
    $("restore-result").textContent="Сначала создай или выбери резервную копию.";
    return;
  }
  const button=$("restore-test");
  button.disabled=true;
  $("restore-result").textContent="Проверяю копию во временной SQLite…";
  try {
    const result=await requestJSON("/api/system/recovery-check?"+
      new URLSearchParams({name}));
    $("restore-result").textContent=result.restorable
      ? "✓ Копия прошла тест восстановления, диалогов: "+
        result.history_counts.conversations+
        ", сообщений: "+result.history_counts.messages+
        ". Рабочая база не изменялась."
      : "Не удалось подтвердить восстановление.";
  } catch (error) {
    $("restore-result").textContent="Ошибка проверки копии: "+error.message;
  } finally {
    button.disabled=false;
  }
});

$("budget-save").addEventListener("click",async () => {
  const button=$("budget-save");
  button.disabled=true;
  try {
    const report=await requestJSON("/api/system/cloud-budget","POST",{
      chat_per_day:Number($("budget-chat").value),
      embeddings_per_day:Number($("budget-embeddings").value),
      per_minute:Number($("budget-minute").value)
    });
    $("budget-status").textContent="Лимиты сохранены локально в SQLite. "+
      "Диалогов в день: "+report.limits.chat_per_day+".";
  } catch (error) {
    $("budget-status").textContent="Не удалось сохранить лимиты: "+error.message;
  } finally {button.disabled=false;}
});
$("brain-find-btn").addEventListener("click",async () => {
  const n=Number($("brain-find-id").value);
  if (!Number.isSafeInteger(n) || n<=0) {
    memoryFeedback("Введи целочисленный ID воспоминания.");
    return;
  }
  const button=$("brain-find-btn");
  button.disabled=true;
  try {
    const target=await requestJSON("/api/soul/atlas?"+
      new URLSearchParams({level:"locate",id:String(n)}));
    await atlasView.goto(target);
    graphScale=1;graphPanX=0;graphPanY=0;
    $("soul-live-state").textContent="Выбрана сота #"+target.memory_id+
      " · "+target.kind;
    dialog.close();
    canvas.focus();
    paint();
  } catch (error) {
    memoryFeedback("Не удалось показать соту: "+error.message);
  } finally {button.disabled=false;}
});


// Documents remain local; importing requires a file picker, an explicit
// confirmation and a same-origin JSON POST. Never read arbitrary disk paths.
async function refreshDocuments() {
  const pane=$("document-list");
  pane.replaceChildren();
  try {
    const [data,cloud]=await Promise.all([
      requestJSON("/api/soul/documents"),
      requestJSON("/api/cloud/status"),
    ]);
    $("document-cloud-consent").checked=Boolean(cloud.document_context_allowed);
    for (const doc of data.items) {
      const row=document.createElement("div");
      row.className="memory-record";
      const label=document.createElement("small");
      label.textContent="Документ #"+doc.id+" · "+doc.name+
        " · редакция "+doc.revision+" · "+doc.bytes+" байт";
      const button=document.createElement("button");
      button.type="button";button.className="quiet-button";
      button.textContent="Удалить";
      button.addEventListener("click",async () => {
        if (!window.confirm("Удалить документ и все его индексированные фрагменты?")) return;
        button.disabled=true;
        try {
          await requestJSON("/api/soul/documents/delete","POST",
                            {id:doc.id,confirmed:true});
          $("document-status").textContent="Удалён локальный документ: "+doc.name;
          await refreshDocuments();
        } catch(error) {
          $("document-status").textContent="Ошибка удаления: "+error.message;
          button.disabled=false;
        }
      });
      const analysis=document.createElement("button");
      analysis.type="button";analysis.className="quiet-button";
      analysis.textContent="Условия";
      analysis.addEventListener("click",async () => {
        analysis.disabled=true;
        const target=$("contract-insights");
        target.replaceChildren();
        try {
          const data=await requestJSON("/api/soul/contracts/insights?"+
            new URLSearchParams({id:String(doc.id)}));
          const heading=document.createElement("p");
          heading.className="memory-empty";
          heading.textContent="Найдены кандидаты: "+data.items.length+
            ". Это не юридический анализ и не гарантия полноты.";
          target.append(heading);
          for (const item of data.items.slice(0,35)) {
            const line=document.createElement("p");
            line.className="memory-empty";
            line.textContent=item.citation+" · "+item.types.join(", ")+
              " · "+item.excerpt;
            target.append(line);
          }
          for (const issue of data.possible_differences) {
            const warning=document.createElement("p");
            warning.className="memory-empty";
            warning.textContent="Проверь различие: "+issue.first+" / "+
              issue.second+". "+issue.note;
            target.append(warning);
          }
        } catch(error) {
          $("document-status").textContent="Ошибка анализа договора: "+error.message;
        } finally {analysis.disabled=false;}
      });
      row.append(label,analysis,button);
      pane.append(row);
    }
    if (!data.items.length) pane.textContent="Пока нет импортированных документов.";
  } catch(error) {
    $("document-status").textContent="Ошибка списка документов: "+error.message;
  }
}
$("document-refresh").addEventListener("click",refreshDocuments);
$("document-details").addEventListener("toggle",() => {
  if ($("document-details").open) refreshDocuments();
});
$("document-import").addEventListener("click",async () => {
  const chosen=$("document-file").files?.[0];
  if (!chosen) {
    $("document-status").textContent="Сначала выбери TXT, MD, DOCX или PDF.";
    return;
  }
  if (!/\.(txt|md|docx|pdf)$/i.test(chosen.name) || chosen.size>1000000) {
    $("document-status").textContent="Разрешены TXT, Markdown, DOCX и PDF до 1 МБ.";
    return;
  }
  if (!window.confirm("Добавить выбранный файл в локальный индекс SQLite? "+
                      "В облако он автоматически не отправляется.")) return;
  const button=$("document-import");
  button.disabled=true;
  try {
    const encoded=await new Promise((resolve,reject) => {
      const reader=new FileReader();
      reader.onload=() => resolve(String(reader.result).split(",")[1]||"");
      reader.onerror=() => reject(new Error("Не удалось прочитать локальный файл."));
      reader.readAsDataURL(chosen);
    });
    const result=await requestJSON("/api/soul/documents/import-binary","POST",
                                  {name:chosen.name,base64:encoded,confirmed:true});
    $("document-status").textContent="Индексировано локально: "+
      result.name+" · фрагментов "+result.chunks+
      " · редакция "+result.revision+
      (result.complete_extraction_guaranteed ? "." :
        ". Извлечение могло пропустить сложное форматирование — проверь исходник.");
    await refreshDocuments();
  } catch(error) {
    $("document-status").textContent="Ошибка импорта: "+error.message;
  } finally {button.disabled=false;}
});

$("document-cloud-consent").addEventListener("change",async () => {
  const input=$("document-cloud-consent");
  const allowed=input.checked;
  input.disabled=true;
  try {
    const state=await requestJSON("/api/cloud/documents/consent","POST",{allowed});
    input.checked=Boolean(state.document_context_allowed);
    $("document-status").textContent=input.checked
      ? "Согласие действует только до остановки Python-сервера. "+
        "Выбранные поиском фрагменты могут попадать в Cloud.ru при ответах."
      : "Документы остаются только локально. Cloud.ru не получает их содержимое.";
  } catch(error) {
    input.checked=!allowed;
    $("document-status").textContent="Ошибка разрешения: "+error.message;
  } finally {input.disabled=false;}
});

$("academy-practice-skill").addEventListener("change",() => {
  const selected=academyPracticeQuestions.find(
    x=>x.skill===$("academy-practice-skill").value);
  $("academy-practice-question").textContent=
    selected?.question||"Нет вопроса.";
});
$("academy-practice-evaluate").addEventListener("click",async () => {
  const button=$("academy-practice-evaluate");
  const answer=$("academy-practice-answer").value.trim();
  if (!answer) {
    $("academy-practice-result").textContent="Нужен текст проверяемого ответа.";
    return;
  }
  button.disabled=true;
  try {
    const result=await requestJSON("/api/soul/academy/practice/evaluate","POST",{
      skill:$("academy-practice-skill").value,answer,confirmed:true
    });
    $("academy-practice-result").textContent=result.passed
      ? "✓ Пройдена проверка заданных слов и длины. Это не проверка фактической истины."
      : "Критерии локальной проверки не выполнены. Это не оценка интеллекта модели.";
  } catch(error) {
    $("academy-practice-result").textContent="Ошибка проверки: "+error.message;
  } finally {button.disabled=false;}
});
