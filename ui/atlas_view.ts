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

function compact(n) {
  return n>=1000 ? (n/1000).toFixed(n>=10000 ? 0 : 1)+"k" : String(n);
}

// Static dial marks describe the four memory categories. Only API records
// produce selectable cells. Cache the dial so idle frames use one blit.
const categories = [
  ["fact", "Факты", "#68d7cb"], ["preference", "Предпочтения", "#93adf4"],
  ["event", "События", "#d8aa74"], ["biography", "Биография", "#b599e6"],
];
export const memoryColors = Object.fromEntries(categories.map(([kind,,color]) => [kind,color]));
const dial = document.createElement("canvas");

export function drawMemoryDial(ctx, x, y, radius) {
  if (radius <= 0) return;
  const size = Math.max(1, Math.ceil(radius * 2.12 * Math.min(devicePixelRatio, 2)));
  if (dial.width !== size || dial.height !== size) {
    dial.width = dial.height = size;
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
    for (const r of [26, 38, 60, 169, 186, 198]) ring(0, 0, r, "#49717d66", .7);
    for (let i = 0; i < 144; i++) {
      const a = i * Math.PI / 72;
      g.beginPath();
      g.moveTo(...point(a, 193));
      g.lineTo(...point(a, i % 6 === 0 ? 198 : 195));
      g.strokeStyle = "#719ba280";
      g.stroke();
    }
    for (const [sector, [, label, color]] of categories.entries()) {
      const start = sector * Math.PI / 2 - Math.PI / 2;
      for (let i = 0; i < 20; i++) {
        const a = start + (i + 1) * Math.PI / 43;
        const [px, py] = point(a, 178);
        ring(px, py, 4.4, color + "88", .8);
        ring(px, py, 1.6, color + "70", .7);
      }
      for (let i = 0; i < 18; i++) {
        const a = start + (i + 1) * Math.PI / 40;
        for (let j = 0; j < 5; j++) {
          const [px, py] = point(a, 75 + j * 5);
          g.fillStyle = color + "59";
          g.fillRect(px - .75, py - .75, 1.5, 1.5);
        }
      }
      const mid = start + Math.PI / 4;
      g.beginPath();
      g.moveTo(...point(mid, 120));
      g.lineTo(...point(mid, 169));
      g.strokeStyle = color + "45";
      g.stroke();
      const [lx, ly] = point(mid, 134);
      g.textAlign = "center";
      g.font = "10px Segoe UI, sans-serif";
      g.fillStyle = color;
      g.fillText(label, lx, ly);
    }
    ring(0, 0, 17, "#64e0cf", 1.4);
    ring(0, 0, 13, "#418c94", .7);
    ring(0, 0, 5, "#dbf5eb", 1.2);
  }
  const half = radius * 1.06;
  ctx.drawImage(dial, x - half, y - half, half * 2, half * 2);
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

  async goto(target) {
    if (!target || !target.is_real_record) throw new Error("Сота не найдена.");
    const sector=Number(target.sector),bucket=Number(target.bucket);
    const result=await this.fetch("nodes",sector,bucket);
    if (!result.nodes.some(item=>item.id===target.memory_id)) {
      throw new Error("Выбранная запись не попала в ограниченную выдачу.");
    }
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

  draw(width,height,zoom,panX,panY,reducedMotion) {
    const ctx=this.ctx;
    ctx.clearRect(0,0,width,height);
    this.shapes=[];
    const cx=width/2+panX,cy=height*.48+panY;
    const base=Math.min(width*.34,height*.35,242);
    if (this.level === "overview") drawMemoryDial(ctx, cx, cy, base * 1.15 * zoom);
    const flicker=(!reducedMotion && performance.now()-this.pulseAt<1800)
      ? .18*(1-(performance.now()-this.pulseAt)/1800) : 0;
    ctx.lineWidth=.9;
    if (!this.data) return;

    if (this.level==="overview") {
      const sectors=this.data.sectors||[];
      // Only aggregated real SQLite memory_links contribute to these lines.
      for (const edge of (this.data.sector_links||[]).slice(0,64)) {
        if (edge.source===edge.target) continue;
        const a=-Math.PI/2+edge.source*Math.PI/4;
        const b=-Math.PI/2+edge.target*Math.PI/4;
        const sx=cx+Math.cos(a)*base*.68*zoom;
        const sy=cy+Math.sin(a)*base*.68*zoom;
        const tx=cx+Math.cos(b)*base*.68*zoom;
        const ty=cy+Math.sin(b)*base*.68*zoom;
        ctx.beginPath();ctx.moveTo(sx,sy);
        ctx.quadraticCurveTo(cx,cy,tx,ty);
        ctx.strokeStyle="rgba(218,160,83,"+
          Math.min(.55,.13+Math.log2(1+edge.count)*.055).toFixed(3)+")";
        ctx.lineWidth=Math.min(2.8,.65+Math.log2(1+edge.count)*.2);
        ctx.stroke();
      }
      for (const item of sectors) {
        const angle=-Math.PI/2+item.id*Math.PI/4;
        const px=cx+Math.cos(angle)*base*.68*zoom;
        const py=cy+Math.sin(angle)*base*.68*zoom;
        const radius=18+Math.min(12,Math.log2(item.count+1)*1.8);
        if (px<-40||px>width+40||py<-40||py>height+40) continue;
        // Glow only on actual local memory updates or navigation events.
        // Idle frames remain inexpensive, and reduced-motion removes pulses.
        if (flicker>0) {
          ctx.save();ctx.shadowBlur=15+flicker*55;
          ctx.shadowColor="rgba(228,164,72,.6)";
        }
        const color = memoryColors[item.kind] || "#89beca";
        hex(ctx,px,py,radius*zoom,"#0b1a24",color);
        if (flicker>0) ctx.restore();
        ctx.textAlign="center";ctx.textBaseline="middle";
        ctx.fillStyle="#f0d0a0";ctx.font="12px system-ui,sans-serif";
        ctx.fillText(compact(item.count),px,py-4);
        ctx.font="9px system-ui,sans-serif";
        ctx.fillText(String(item.id+1),px,py+9);
        this.shapes.push({x:px,y:py,radius:radius*zoom,
                          type:"sector",sector:item.id,
                          preview:NAMES[item.kind]+" · "+item.count+" записей"});
      }
    } else if (this.level==="clusters" || this.level==="regions") {
      // Regions and 256-ID clusters share one bounded virtualized renderer.
      const groups=this.level==="regions"
        ? (this.data.regions||[]) : (this.data.clusters||[]);
      const spacing=Math.min(15,Math.max(7,base/19))*zoom;
      // Stable golden-angle placement and screen-space culling keep draw bounded.
      for (let i=0;i<groups.length;i++) {
        const cluster=groups[i];
        const angle=i*GOLDEN;
        const distance=Math.sqrt(i+.5)*spacing*1.16;
        const px=cx+Math.cos(angle)*distance,py=cy+Math.sin(angle)*distance;
        if (px<-25||px>width+25||py<-25||py>height+25) continue;
        const radius=(5+Math.min(4,Math.sqrt(cluster.count)*.32))*zoom;
        hex(ctx,px,py,radius,"rgba(179,127,65,.17)",
            "rgba(236,184,104,"+(.58+flicker)+")");
        if (zoom>1.35 || groups.length<40) {
          ctx.fillStyle="#dec29b";ctx.font="9px system-ui,sans-serif";
          ctx.textAlign="center";ctx.textBaseline="middle";
          ctx.fillText(compact(cluster.count),px,py);
        }
        this.shapes.push({x:px,y:py,radius:Math.max(radius,8),
                          type:this.level==="regions"?"region":"cluster",
                          region:cluster.region,bucket:cluster.bucket,
                          preview:(this.level==="regions"?"Область памяти":"Сота-кластер")+
                            " · "+cluster.count+" записей · ID "+
                            cluster.first_id+"–"+cluster.last_id});
      }
      if (this.data.has_more) {
        const px=width-45,py=height-37;
        hex(ctx,px,py,19,"rgba(171,117,50,.25)","rgba(240,186,108,.85)");
        ctx.fillStyle="#f0d1a1";ctx.textAlign="center";ctx.font="11px system-ui";
        ctx.fillText("Ещё",px,py+4);
        this.shapes.push({x:px,y:py,radius:22,type:"more",
                          preview:"Показать следующую страницу кластеров"});
      }
    } else if (this.level==="nodes") {
      const nodes=this.data.nodes||[];
      const map=new Map();
      const spacing=11*zoom;
      for (let i=0;i<nodes.length;i++) {
        const node=nodes[i];
        const angle=i*GOLDEN;
        const distance=Math.sqrt(i+.3)*spacing;
        const px=cx+Math.cos(angle)*distance,py=cy+Math.sin(angle)*distance;
        map.set(node.id,{x:px,y:py});
      }
      for (const edge of (this.data.edges||[]).slice(0,400)) {
        const a=map.get(edge.source_id),b=map.get(edge.target_id);
        if (!a||!b) continue;
        ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);
        ctx.strokeStyle=edge.relation==="possible_conflict"
          ? "rgba(244,124,103,.42)" : "rgba(223,167,105,.23)";
        ctx.stroke();
      }
      for (const node of nodes) {
        const point=map.get(node.id);
        if (!point) continue;
        if (point.x<-20||point.x>width+20||point.y<-20||point.y>height+20) continue;
        const radius=5*zoom;
        hex(ctx,point.x,point.y,radius,"rgba(190,143,75,.13)",
            "rgba(243,195,130,"+(.72+flicker)+")");
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
    ctx.fillStyle="rgba(222,189,146,.65)";
    ctx.font="10px system-ui,sans-serif";ctx.textAlign="center";
    ctx.fillText(label,width*.5,height-22);
  }
}
