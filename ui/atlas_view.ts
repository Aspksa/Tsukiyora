// Progressive HTML5 Canvas atlas. The only source of nodes and cluster counts
// is the local SQLite API. No fake memories, no third-party renderer.
const NAMES = {fact:"ФАКТЫ",preference:"ПРЕДПОЧТЕНИЯ",
               event:"СОБЫТИЯ",biography:"БИОГРАФИЯ"};
const GOLDEN = Math.PI * (3-Math.sqrt(5));

function hex(ctx,x,y,r,fill,stroke) {
  ctx.beginPath();
  for (let i=0;i<6;i++) {
    const a = Math.PI/3*i;
    const px=x+Math.cos(a)*r,py=y+Math.sin(a)*r;
    if (i===0) ctx.moveTo(px,py); else ctx.lineTo(px,py);
  }
  ctx.closePath();
  ctx.fillStyle=fill;ctx.strokeStyle=stroke;
  ctx.fill();ctx.stroke();
}

function drawAtlasNode(ctx,x,y,r,fill,stroke,layout) {
  if (layout === "hex") {
    hex(ctx,x,y,r,fill,stroke);
    return;
  }
  ctx.beginPath();
  ctx.arc(x,y,r,0,Math.PI*2);
  ctx.fillStyle=fill;ctx.strokeStyle=stroke;
  ctx.fill();ctx.stroke();
}

function compact(n) {
  return n>=1000 ? (n/1000).toFixed(n>=10000 ? 0 : 1)+"k" : String(n);
}

// Static dial marks describe the four memory categories. Only API records
// produce selectable cells. Cache the dial so idle frames use one blit.
const categories = [
  ["fact", "Факты", "#64e2bd"], ["preference", "Предпочтения", "#85d9f4"],
  ["event", "События", "#b3aeff"], ["biography", "Биография", "#f4c184"],
];
export const memoryColors = Object.fromEntries(categories.map(([kind,,color]) => [kind,color]));
export const memoryKinds = Object.keys(memoryColors);
export const sectorAngle = (sector) => -Math.PI / 2 + (sector + .5) * Math.PI / 4;
export const memoryMapRadius = (width, height, zoom) => Math.min(width, height) * .415 * zoom;
const honeycomb = [[0,-1], [1,-1], [2,-1], [1,0], [0,1], [-1,1], [-2,1], [-1,0]];

export function sectorPoint(sector, cx, cy, radius, layout, distance = .59) {
  if (layout === "hex") {
    const [column, row] = honeycomb[sector];
    const cell = radius * .215;
    return {x: cx + 1.5 * column * cell, y: cy + Math.sqrt(3) * (row + column / 2) * cell,
            cellRadius: cell * .94};
  }
  const angle = sectorAngle(sector);
  return {x: cx + Math.cos(angle) * radius * distance, y: cy + Math.sin(angle) * radius * distance};
}

export function drawHexSector(ctx, {x,y,cellRadius}, kind, count, aggregate = true) {
  const color = memoryColors[kind];
  ctx.save();
  ctx.lineWidth = 1;
  hex(ctx,x,y,cellRadius,color+"10",color+"62");
  ctx.beginPath();
  ctx.arc(x,y,cellRadius*.38,0,Math.PI*2);
  ctx.strokeStyle=color+"30";
  ctx.stroke();
  if (cellRadius >= 28) {
    ctx.textAlign="center";ctx.textBaseline="middle";
    ctx.font="600 "+Math.min(10,Math.round(cellRadius*.27))+"px Consolas, monospace";
    ctx.fillStyle=color;
    ctx.fillText(NAMES[kind],x,y-cellRadius*.56,cellRadius*1.5);
  }
  if (aggregate || cellRadius >= 36) {
    ctx.textAlign="center";ctx.textBaseline="middle";
    ctx.font=(aggregate ? "12px" : "9px")+" Consolas, monospace";
    ctx.fillStyle=aggregate ? "#e9e2d6" : color+"ad";
    ctx.fillText(compact(count),x,y+(aggregate ? 0 : cellRadius*.72));
  }
  ctx.restore();
}
const dial = document.createElement("canvas");
let drawDialLabels = false;

export function drawMemoryDial(ctx, x, y, radius) {
  if (radius <= 0) return;
  const size = Math.max(1, Math.ceil(radius * 2.12 * Math.min(devicePixelRatio, 2)));
  const showLabels = radius >= 170;
  if (dial.width !== size || dial.height !== size || drawDialLabels !== showLabels) {
    dial.width = dial.height = size;
    drawDialLabels = showLabels;
    const g = dial.getContext("2d");
    g.translate(size / 2, size / 2);
    g.scale(size / 424, size / 424);
    function ring(cx, cy, r, stroke, weight = 1) {
      g.beginPath();
      g.arc(cx, cy, r, 0, Math.PI * 2);
      g.strokeStyle = stroke;
      g.lineWidth = weight;
      g.stroke();
    }
    function point(angle, distance) {
      return [Math.cos(angle) * distance, Math.sin(angle) * distance];
    }
    for (const r of [53, 70, 101, 129, 157, 185, 198]) ring(0, 0, r, "#93cacc25", .85);
    for (let i = 0; i < 120; i++) {
      const a = i * Math.PI / 60;
      g.beginPath();
      g.moveTo(...point(a, i % 5 === 0 ? 191 : 195));
      g.lineTo(...point(a, 198));
      g.strokeStyle = i % 5 === 0 ? "#95c8d290" : "#95c8d248";
      g.stroke();
    }
    for (const [sector, [, label, color]] of categories.entries()) {
      const start = sector * Math.PI / 2 - Math.PI / 2;
      const lo = start + .09, hi = start + Math.PI / 2 - .09;
      g.beginPath();
      g.arc(0, 0, 186, lo, hi);
      g.arc(0, 0, 75, hi, lo, true);
      g.closePath();
      g.fillStyle = color + "08";
      g.fill();
      g.beginPath();
      g.arc(0, 0, 198, lo, hi);
      g.lineWidth = 2;
      g.strokeStyle = color + "86";
      g.stroke();
      g.lineWidth = 1;
      g.beginPath();
      g.moveTo(...point(start, 65));
      g.lineTo(...point(start, 181));
      g.strokeStyle = "#9bccca30";
      g.stroke();
      if (showLabels) {
        const [lx, ly] = point(start + Math.PI / 4, 137);
        g.textAlign = "center";
        g.textBaseline = "middle";
        g.font = "600 10px Consolas, monospace";
        g.fillStyle = color;
        g.fillText(label, lx, ly);
      }
    }
  }
  const half = radius * 1.06;
  ctx.drawImage(dial, x - half, y - half, half * 2, half * 2);
}

// A rotating point sphere is the core glyph, drawn independently of record counts.
export function drawCore(ctx, x, y, radius, seconds) {
  if (radius <= 0) return;
  ctx.save();
  const glow = ctx.createRadialGradient(x, y, radius * .31, x, y, radius * 2.55);
  glow.addColorStop(0, "#61e3c644");
  glow.addColorStop(1, "#61e3c600");
  ctx.fillStyle = glow;
  ctx.fillRect(x - radius * 2.6, y - radius * 2.6, radius * 5.2, radius * 5.2);
  ctx.beginPath();
  ctx.arc(x, y, radius * 1.46, 0, Math.PI * 2);
  ctx.fillStyle = "#081a20";
  ctx.strokeStyle = "#81e6cdb0";
  ctx.lineWidth = 1.15;
  ctx.fill();
  ctx.stroke();
  const phase = seconds * .48;
  for (let i = 0; i < 96; i++) {
    const latitude = 1 - (i + .5) / 48;
    const band = Math.sqrt(1 - latitude * latitude);
    const angle = i * GOLDEN + phase;
    const depth = Math.cos(angle) * band;
    const px = Math.sin(angle) * band;
    const py = latitude * .94 - depth * .34;
    ctx.globalAlpha = .22 + (depth + 1) * .36;
    ctx.fillStyle = depth > 0 ? "#c7ffe9" : "#52c9b0";
    const size = Math.max(.8, radius * (.045 + .021 * depth));
    ctx.fillRect(x + px * radius, y + py * radius, size, size);
  }
  ctx.restore();
}

export class BrainAtlasView {
  constructor(ctx) {
    this.ctx=ctx;
    this.active=false;this.level="overview";
    this.data=null;this.sector=null;this.bucket=null;this.region=null;this.cursor=null;
    this.shapes=[];this.lastTotal=0;this.pulseAt=0;
  }

  async fetch(level,sector=null,bucket=null,cursor=null,region=null) {
    const params = new URLSearchParams({level});
    if (sector!==null) params.set("sector", String(sector));
    if (bucket!==null) params.set("bucket", String(bucket));
    if (cursor!==null) params.set("cursor", String(cursor));
    if (region!==null) params.set("region", String(region));
    const response=await window.fetch("/api/soul/atlas?"+params,{cache:"no-store"});
    if (!response.ok) {
      const err=await response.json();
      throw new Error(err.error || "Не удалось открыть карту памяти");
    }
    return response.json();
  }

  overview(data) {
    const oldTotal=this.lastTotal;
    this.lastTotal=data.total;
    if (oldTotal && oldTotal !== data.total) this.pulseAt=performance.now();
    this.active=true;this.level="overview";
    this.data=data;this.sector=null;this.bucket=null;this.region=null;this.cursor=null;
  }

  async open(node) {
    if (!node) return null;
    if (node.type==="sector") {
      this.sector=node.sector;this.bucket=null;this.region=null;this.cursor=null;
      const count=this.data?.sectors?.find(x=>x.id===this.sector)?.count || 0;
      this.level=count>5000?"regions":"clusters";
      this.data=await this.fetch(this.level,this.sector);
      this.pulseAt=performance.now();
      return null;
    }
    if (node.type==="region") {
      this.region=node.region;this.cursor=null;this.bucket=null;
      this.data=await this.fetch("clusters",this.sector,null,null,this.region);
      this.level="clusters";this.pulseAt=performance.now();
      return null;
    }
    if (node.type==="cluster") {
      this.bucket=node.bucket;
      this.data=await this.fetch("nodes",this.sector,this.bucket);
      this.level="nodes";this.pulseAt=performance.now();
      return null;
    }
    if (node.type==="more") {
      this.cursor=this.data.next_cursor;
      this.data=await this.fetch(this.level,this.sector,null,this.cursor,this.region);
      return null;
    }
    if (node.type==="memory") return node;
    return null;
  }

  async goto(target, shouldApply = () => true) {
    if (!target || !target.is_real_record) throw new Error("Сота не найдена.");
    const sector=Number(target.sector),bucket=Number(target.bucket);
    const result=await this.fetch("nodes",sector,bucket);
    if (!result.nodes.some(item=>item.id===target.memory_id)) {
      throw new Error("Выбранная запись не попала в ограниченную выдачу.");
    }
    if (!shouldApply()) return;
    this.active=true;
    this.level="nodes";this.sector=sector;
    this.region=Number(target.region);this.bucket=bucket;
    this.cursor=null;this.data=result;
    this.pulseAt=performance.now();
  }

  async back() {
    if (this.level==="nodes") {
      this.data=await this.fetch("clusters",this.sector,null,this.cursor,this.region);
      this.level="clusters";this.bucket=null;
    } else if (this.level==="clusters" && this.region!==null) {
      this.data=await this.fetch("regions",this.sector);
      this.level="regions";this.region=null;this.cursor=null;
    } else if (this.level==="regions" || this.level==="clusters") {
      this.data=await this.fetch("overview");
      this.level="overview";this.sector=null;this.region=null;this.cursor=null;
    }
  }

  async refresh(overview) {
    if (!this.active || this.level==="overview") {
      this.overview(overview);
      return;
    }
    if (this.lastTotal && overview.total!==this.lastTotal) {
      this.pulseAt=performance.now();
    }
    this.lastTotal=overview.total;
    this.data=await this.fetch(this.level,this.sector,this.bucket,this.cursor,this.region);
  }

  hit(x,y) {
    let result=null;
    let closest=Infinity;
    for (const shape of this.shapes) {
      const dx=x-shape.x,dy=y-shape.y;
      const dist=dx*dx+dy*dy;
      if (dist<shape.radius*shape.radius*1.6 && dist<closest) {
        closest=dist;result=shape;
      }
    }
    return result;
  }

  draw(width,height,zoom,panX,panY,reducedMotion,layout = "rings",selectedId = null) {
    const ctx=this.ctx;
    ctx.clearRect(0,0,width,height);
    this.shapes=[];
    const cx=width/2+panX,cy=height*.48+panY;
    const radius=memoryMapRadius(width,height,zoom);
    const base=radius/1.15/zoom;
    if (this.level === "overview" && layout === "rings") {
      drawMemoryDial(ctx, cx, cy, radius);
    } else {
      ctx.beginPath();ctx.arc(cx,cy,radius*.99,0,Math.PI*2);
      ctx.strokeStyle="#93cacc38";ctx.lineWidth=1;ctx.stroke();
    }
    drawCore(ctx, cx, cy, base * .145 * zoom, reducedMotion ? 0 : performance.now() / 1000);
    const flicker=(!reducedMotion && performance.now()-this.pulseAt<1800)
      ? .18*(1-(performance.now()-this.pulseAt)/1800) : 0;
    ctx.lineWidth=.9;
    if (!this.data) return;

    if (this.level==="overview") {
      const sectors=this.data.sectors||[];
      // Only aggregated real SQLite memory_links contribute to these lines.
      for (const edge of (this.data.sector_links||[]).slice(0,64)) {
        if (edge.source===edge.target) continue;
        const a=sectorPoint(edge.source,cx,cy,radius,layout);
        const b=sectorPoint(edge.target,cx,cy,radius,layout);
        ctx.beginPath();ctx.moveTo(a.x,a.y);
        ctx.quadraticCurveTo(cx,cy,b.x,b.y);
        ctx.strokeStyle="rgba(115,204,198,"+
          Math.min(.55,.13+Math.log2(1+edge.count)*.055).toFixed(3)+")";
        ctx.lineWidth=Math.min(2.8,.65+Math.log2(1+edge.count)*.2);
        ctx.stroke();
      }
      for (const item of sectors) {
        const position=sectorPoint(item.id,cx,cy,radius,layout);
        const {x:px,y:py}=position;
        if (layout === "hex") {
          drawHexSector(ctx,position,item.kind,item.count);
          this.shapes.push({x:px,y:py,radius:position.cellRadius,
                            type:"sector",sector:item.id,kind:item.kind,
                            preview:NAMES[item.kind]+" · "+item.count+" записей"});
          continue;
        }
        const markerRadius=12+Math.min(11,Math.log2(item.count+1)*1.2);
        if (px<-40||px>width+40||py<-40||py>height+40) continue;
        // Glow only on actual local memory updates or navigation events.
        // Idle frames remain inexpensive, and reduced-motion removes pulses.
        if (flicker>0) {
          ctx.save();ctx.shadowBlur=15+flicker*55;
          ctx.shadowColor="#79e6cb99";
        }
        const color = memoryColors[item.kind] || "#89beca";
        ctx.beginPath();
        ctx.arc(px,py,markerRadius*zoom,0,Math.PI*2);
        ctx.fillStyle="#091b23";ctx.strokeStyle=color;
        ctx.fill();ctx.stroke();
        if (flicker>0) ctx.restore();
        ctx.textAlign="center";ctx.textBaseline="middle";
        ctx.fillStyle="#dff9f2";ctx.font="12px Consolas, monospace";
        ctx.fillText(compact(item.count),px,py);
        this.shapes.push({x:px,y:py,radius:markerRadius*zoom,
                          type:"sector",sector:item.id,kind:item.kind,
                          preview:NAMES[item.kind]+" · "+item.count+" записей"});
      }
    } else if (this.level==="clusters" || this.level==="regions") {
      // Regions and 256-ID clusters share one bounded virtualized renderer.
      const groups=this.level==="regions"
        ? (this.data.regions||[]) : (this.data.clusters||[]);
      const spacing=Math.min(15,Math.max(7,base/19),base*.6/Math.sqrt(groups.length || 1))*zoom;
      const color=memoryColors[this.data.kind] || "#84d8cf";
      // Stable golden-angle placement and screen-space culling keep draw bounded.
      for (let i=0;i<groups.length;i++) {
        const cluster=groups[i];
        const angle=i*GOLDEN;
        const distance=Math.sqrt(i+.5)*spacing*1.16+base*.23*zoom;
        const px=cx+Math.cos(angle)*distance,py=cy+Math.sin(angle)*distance;
        if (px<-25||px>width+25||py<-25||py>height+25) continue;
        const radius=(5+Math.min(4,Math.sqrt(cluster.count)*.32))*zoom;
        drawAtlasNode(ctx,px,py,radius,color+"1f",color+(flicker ? "ce" : "9a"),layout);
        if (zoom>1.35 || groups.length<40) {
          ctx.fillStyle="#d3ece7";ctx.font="9px Consolas, monospace";
          ctx.textAlign="center";ctx.textBaseline="middle";
          ctx.fillText(compact(cluster.count),px,py);
        }
        this.shapes.push({x:px,y:py,radius:Math.max(radius,8),
                          type:this.level==="regions"?"region":"cluster",
                          region:cluster.region,bucket:cluster.bucket,kind:this.data.kind,
                          firstId:cluster.first_id,lastId:cluster.last_id,
                          preview:(this.level==="regions"?"Область памяти":"Сота-кластер")+
                            " · "+cluster.count+" записей · ID "+
                            cluster.first_id+"–"+cluster.last_id});
      }
      if (this.data.has_more) {
        const px=width-45,py=height-37;
        hex(ctx,px,py,19,"#64e2bd24","#64e2bdc0");
        ctx.fillStyle="#d3ece7";ctx.textAlign="center";ctx.font="11px Consolas, monospace";
        ctx.fillText("Ещё",px,py+4);
        this.shapes.push({x:px,y:py,radius:22,type:"more",
                          preview:"Показать следующую страницу кластеров"});
      }
    } else if (this.level==="nodes") {
      const nodes=this.data.nodes||[];
      if (layout === "hex" && radius > 0) {
        ctx.lineWidth=1;
        hex(ctx,cx,cy,radius*.88,"#02010000","#93725070");
      }
      const map=new Map();
      const spacing=Math.min(11,base*.72/Math.sqrt(nodes.length || 1))*zoom;
      for (let i=0;i<nodes.length;i++) {
        const node=nodes[i];
        const angle=i*GOLDEN;
        const distance=Math.sqrt(i+.3)*spacing+base*.23*zoom;
        const px=cx+Math.cos(angle)*distance,py=cy+Math.sin(angle)*distance;
        map.set(node.id,{x:px,y:py});
      }
      for (const edge of (this.data.edges||[]).slice(0,400)) {
        const a=map.get(edge.source_id),b=map.get(edge.target_id);
        if (!a||!b) continue;
        ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);
        const selected=edge.source_id===selectedId || edge.target_id===selectedId;
        ctx.lineWidth=selected ? 1.8 : .75;
        ctx.strokeStyle=edge.relation==="possible_conflict"
          ? (selected ? "#f5a389dc" : "#f5a3894a")
          : (selected ? "#b3f1dfdc" : "#92cbc133");
        ctx.stroke();
      }
      for (const node of nodes) {
        const point=map.get(node.id);
        if (!point) continue;
        if (point.x<-20||point.x>width+20||point.y<-20||point.y>height+20) continue;
        const radius=5*zoom;
        const color=memoryColors[node.kind] || "#84d8cf";
        ctx.lineWidth=.9;
        drawAtlasNode(ctx,point.x,point.y,radius,color+"28",color+(flicker ? "e8" : "b5"),layout);
        if (node.id===selectedId) {
          ctx.beginPath();ctx.arc(point.x,point.y,radius+5,0,Math.PI*2);
          ctx.lineWidth=1.4;ctx.strokeStyle=color;ctx.stroke();
        }
        this.shapes.push({x:point.x,y:point.y,radius:Math.max(7,radius),
                          type:"memory",id:node.id,preview:node.preview,
                          kind:node.kind});
      }
    }
    // One concise hint is sufficient; no overlay control panel.
    const label=this.level==="overview" ? "Нажми сектор · углубиться в память" :
      this.level==="regions" ? "Нажми область · Esc — назад" :
      this.level==="clusters" ? "Нажми кластер · Esc — назад" :
      "Нажми воспоминание · двойной щелчок — обзор";
    ctx.fillStyle="#a0c2c7";
    ctx.font="10px Consolas, monospace";ctx.textAlign="center";
    ctx.fillText(label,width*.5,height-22);
  }
}
