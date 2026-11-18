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
