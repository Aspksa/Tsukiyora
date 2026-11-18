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

export class BrainAtlasView {
  constructor(ctx) {
    this.ctx=ctx;
    this.active=false;this.level="overview";
    this.data=null;this.sector=null;this.bucket=null;this.cursor=null;
    this.shapes=[];this.lastTotal=0;this.pulseAt=0;
  }

  async fetch(level,sector=null,bucket=null,cursor=null) {
    const params = new URLSearchParams({level});
    if (sector!==null) params.set("sector", String(sector));
    if (bucket!==null) params.set("bucket", String(bucket));
    if (cursor!==null) params.set("cursor", String(cursor));
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
    this.data=data;this.sector=null;this.bucket=null;this.cursor=null;
  }

  async open(node) {
    if (!node) return null;
    if (node.type==="sector") {
      this.sector=node.sector;this.bucket=null;this.cursor=null;
      this.data=await this.fetch("clusters",this.sector);
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
      this.data=await this.fetch("clusters",this.sector,null,this.cursor);
      return null;
    }
    if (node.type==="memory") return node;
    return null;
  }

  async back() {
    if (this.level==="nodes") {
      this.data=await this.fetch("clusters",this.sector,null,this.cursor);
      this.level="clusters";this.bucket=null;
    } else if (this.level==="clusters") {
      this.data=await this.fetch("overview");
      this.level="overview";this.sector=null;this.cursor=null;
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
    this.data=await this.fetch(this.level,this.sector,this.bucket,this.cursor);
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
    const halo=ctx.createRadialGradient(cx,cy,3,cx,cy,base*1.5);
    halo.addColorStop(0,"rgba(185,132,72,.14)");
    halo.addColorStop(1,"rgba(185,132,72,0)");
    ctx.fillStyle=halo;
    ctx.fillRect(0,0,width,height);
    const flicker=(!reducedMotion && performance.now()-this.pulseAt<1800)
      ? .18*(1-(performance.now()-this.pulseAt)/1800) : 0;
    ctx.lineWidth=.9;
    if (!this.data) return;

    if (this.level==="overview") {
      const sectors=this.data.sectors||[];
      for (const item of sectors) {
        const angle=-Math.PI/2+item.id*Math.PI/4;
        const px=cx+Math.cos(angle)*base*.68*zoom;
        const py=cy+Math.sin(angle)*base*.68*zoom;
        const radius=18+Math.min(12,Math.log2(item.count+1)*1.8);
        if (px<-40||px>width+40||py<-40||py>height+40) continue;
        hex(ctx,px,py,radius*zoom,"rgba(174,125,67,.22)",
            "rgba(234,186,116,"+(.72+flicker)+")");
        ctx.textAlign="center";ctx.textBaseline="middle";
        ctx.fillStyle="#f0d0a0";ctx.font="12px system-ui,sans-serif";
        ctx.fillText(compact(item.count),px,py-4);
        ctx.font="9px system-ui,sans-serif";
        ctx.fillText(String(item.id+1),px,py+9);
        this.shapes.push({x:px,y:py,radius:radius*zoom,
                          type:"sector",sector:item.id,
                          preview:NAMES[item.kind]+" · "+item.count+" записей"});
      }
    } else if (this.level==="clusters") {
      // Golden-angle packing displays hundreds of grounded 256-ID clusters,
      // never thousands of individual cells at a distant zoom.
      const groups=this.data.clusters||[];
      const spacing=Math.min(15,Math.max(7,base/19))*zoom;
      const center=groups.length/2;
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
                          type:"cluster",bucket:cluster.bucket,
                          preview:"Сота-кластер · "+cluster.count+
                                  " записей · ID "+cluster.first_id+"–"+cluster.last_id});
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
      this.level==="clusters" ? "Нажми кластер · двойной щелчок — обзор" :
      "Нажми воспоминание · двойной щелчок — обзор";
    ctx.fillStyle="rgba(222,189,146,.65)";
    ctx.font="10px system-ui,sans-serif";ctx.textAlign="center";
    ctx.fillText(label,width*.5,height-22);
  }
}
