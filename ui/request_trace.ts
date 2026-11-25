const stations = {
  chat: ["Чат", "#85d9f4"],
  db: ["SQLite", "#64e2bd"],
  core: ["Контекст", "#a4f9da"],
  cloud: ["Cloud.ru", "#b3aeff"],
};
const duration = 480;
const gap = 65;

function curve(from, to, bend) {
  return [
    from,
    {x: (from.x + to.x) / 2 - (to.y - from.y) * bend,
     y: (from.y + to.y) / 2 + (to.x - from.x) * bend},
    to,
  ];
}

function point([a, b, c], t) {
  const u = 1 - t;
  return {x: u * u * a.x + 2 * u * t * b.x + t * t * c.x,
          y: u * u * a.y + 2 * u * t * b.y + t * t * c.y};
}

function line(ctx, path, color, opacity, width) {
  const [a, b, c] = path;
  ctx.globalAlpha = opacity;
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.beginPath();
  ctx.moveTo(a.x, a.y);
  ctx.quadraticCurveTo(b.x, b.y, c.x, c.y);
  ctx.stroke();
}

function dot(ctx, {x, y}, radius, color) {
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}

function packet(ctx, path, t, color, size) {
  // A tapered tail makes the direction legible even on a small map.
  for (let i = 6; i >= 0; i--) {
    ctx.globalAlpha = 1 - i * .14;
    const position = point(path, Math.max(0, t - i * .022));
    dot(ctx, position, size * (1 - i * .115), i === 0 ? "#e7fff7" : color);
  }
}

export class RequestTrace {
  constructor() {
    this.clear();
  }

  clear() {
    this.steps = [];
    this.outcome = "";
  }

  add(from, to, sources = []) {
    const previous = this.steps.at(-1);
    const start = Math.max(performance.now(), previous ? previous.start + duration + gap : 0);
    this.steps.push({from, to, sources, start});
  }

  replay() {
    const now = performance.now();
    this.steps.forEach((step, index) => { step.start = now + index * (duration + gap); });
  }

  draw(ctx, core, radius, resolveSources, reducedMotion) {
    if (!this.steps.length || radius <= 0) return;
    const now = performance.now();
    const stationPoint = (angle) => ({
      x: core.x + Math.cos(angle) * radius * .76,
      y: core.y + Math.sin(angle) * radius * .76,
    });
    const points = {
      chat: stationPoint(-Math.PI / 2),
      db: stationPoint(Math.PI * .83),
      cloud: stationPoint(Math.PI * .17),
      core,
    };
    const reached = new Set();
    let active = null;
    let last = null;
    ctx.save();
    // Clip the whole route, including the animated trails and source halos,
    // to the same moving ring as the memory nodes.
    ctx.beginPath();
    ctx.arc(core.x, core.y, radius * .98, 0, Math.PI * 2);
    ctx.clip();
    for (const step of this.steps) {
      if (!reducedMotion && now < step.start) break;
      const progress = reducedMotion ? 1 : Math.min(1, (now - step.start) / duration);
      const bend = step.from === "chat" ? (last ? -.22 : .20) : step.from === "cloud" ? -.21 : .27;
      const path = curve(points[step.from], points[step.to], bend);
      const color = stations[step.to][1];
      reached.add(step.from);
      if (progress === 1) reached.add(step.to);
      last = step;
      if (progress < 1) active = {step, path, progress, color};
      else line(ctx, path, color, .28, 1.15);
      if (step.sources.length) {
        for (const source of resolveSources(step.sources)) {
          if (Math.hypot(source.x - core.x, source.y - core.y) > radius * .96) continue;
          const branch = curve(source, points.core, -.19);
          line(ctx, branch, source.color, progress < 1 ? .56 : .23, 1.2);
          ctx.globalAlpha = progress < 1 ? 1 : .7;
          ctx.strokeStyle = source.color;
          ctx.beginPath();
          ctx.arc(source.x, source.y, source.radius || 5, 0, Math.PI * 2);
          ctx.stroke();
          if (progress < 1) packet(ctx, branch, progress, source.color, 2.8);
        }
      }
    }
    if (active) {
      const {step, path, progress, color} = active;
      line(ctx, path, color, .13, 1);
      const tip = point(path, progress);
      const control = point(path, progress / 2);
      const partial = [path[0], {x: 2 * control.x - (path[0].x + tip.x) / 2,
                                 y: 2 * control.y - (path[0].y + tip.y) / 2}, tip];
      line(ctx, partial, color, .19, 7);
      line(ctx, partial, color, .87, 1.8);
      packet(ctx, path, progress, color, 4.2);
      reached.add(step.to);
    }
    for (const key of reached) {
      const position = points[key];
      const [label, color] = stations[key];
      const lit = active ? active.step.to === key || active.step.from === key : last?.to === key;
      const accent = lit && !active && this.outcome === "interrupted" ? "#f5a389" : color;
      ctx.globalAlpha = 1;
      if (key !== "core") dot(ctx, position, 8.5, "#130c06");
      ctx.strokeStyle = accent + (lit ? "ed" : "85");
      ctx.lineWidth = lit ? 1.6 : 1;
      ctx.beginPath();
      ctx.arc(position.x, position.y, key === "core" ? 17 : 8.5, 0, Math.PI * 2);
      ctx.stroke();
      if (key !== "core") dot(ctx, position, lit ? 3.4 : 2, accent);
      ctx.font = "600 11px Consolas, monospace";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const labelWidth = ctx.measureText(label).width + 12;
      const y = position.y + (key === "core" ? -26 : position.y > core.y ? -17 : 17);
      ctx.fillStyle = "#130c06";
      ctx.fillRect(position.x - labelWidth / 2, y - 7, labelWidth, 14);
      ctx.fillStyle = lit ? "#e2fff4" : "#a8c7ca";
      ctx.fillText(label, position.x, y);
    }
    ctx.restore();
  }
}
