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
    $("memory-info").textContent = status.database === "ready" ? "SQLite готова" : "Ошибка";
    $("model-info").textContent = "Не подключена";
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
