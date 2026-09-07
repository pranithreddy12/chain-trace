import { useRef, useEffect, useCallback } from "react";

interface Props {
  nodes: Array<{
    id: string;
    short: string;
    label: string;
    type: string;
    color: string;
    depth: number;
    inAmt: string;
    outAmt: string;
    patterns: string[];
    conf: number;
    isSeed: boolean;
    isEndpoint: boolean;
    isSuspicious: boolean;
    isObf: boolean;
    onPath: boolean;
    val: number;
  }>;
  links: Array<{
    source: string;
    target: string;
    count: number;
    total: number;
    tokens: string;
    onPath: boolean;
  }>;
}

declare global {
  interface Window {
    ForceGraph: any;
  }
}

const LEGEND_HTML = [
  ["Seed / victim", "#00ff9c"],
  ["Exchange", "#ff5c7a"],
  ["Sanctioned", "#ff2d2d"],
  ["Mixer / bridge", "#ffb020"],
  ["Suspicious", "#ff4d4d"],
  ["Intermediate", "#4da3ff"],
  ["Contract", "#8a7dff"],
  ["Unknown", "#8494a8"],
]
  .map(([name, c]) => `<span class="lg"><i style="background:${c}"></i>${name}</span>`)
  .join("");

function hexA(hex: string, a: number): string {
  const h = hex.replace("#", "");
  const r = Math.min(255, parseInt(h.slice(0, 2), 16));
  const g = Math.min(255, parseInt(h.slice(2, 4), 16));
  const b = Math.min(255, parseInt(h.slice(4, 6), 16));
  return `rgba(${r},${g},${b},${a})`;
}

export default function ConstellationGraph({ nodes, links }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphiteRef = useRef<any>(null);
  const fitTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const renderGraph = useCallback(() => {
    const wrap = containerRef.current;
    if (!wrap) return;
    const W = wrap.clientWidth || 800;
    const H = 660;

    const skyEl = wrap.querySelector('#sky');
    if (!skyEl) return;
    const sky = skyEl as HTMLCanvasElement;
    sky.width = W;
    sky.height = H;
    const sctx = sky.getContext('2d');
    if (!sctx) return;

    const bg: Array<{ x: number; y: number; r: number; p: number; s: number }> = [];
    for (let i = 0; i < 220; i++) {
      bg.push({
        x: Math.random() * W,
        y: Math.random() * H,
        r: Math.random() * 1.3 + 0.2,
        p: Math.random() * 6.2832,
        s: Math.random() * 0.02 + 0.004,
      });
    }

    let raf: number | undefined;
    function drawSky(t: number) {
      sctx!.clearRect(0, 0, sky.width, sky.height);
      for (const s of bg) {
        const a = 0.30 + 0.55 * Math.abs(Math.sin(s.p + t * s.s));
        sctx!.beginPath();
        sctx!.fillStyle = `rgba(150,180,230,${a.toFixed(3)})`;
        sctx!.arc(s.x, s.y, s.r, 0, 6.2832);
        sctx!.fill();
      }
      raf = requestAnimationFrame(drawSky);
    }
    raf = requestAnimationFrame(drawSky);

    if (!nodes || nodes.length === 0) {
      const emptyEl = document.createElement('div');
      emptyEl.style.cssText = 'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#8ea6c6;padding:24px;font-size:12px';
      emptyEl.textContent = 'No fund movement was observed from this address.';
      wrap.appendChild(emptyEl);
      graphiteRef.current = { Graph: null as any, raf: null, wrap, root: emptyEl, panel: null as any };
      return;
    }

    const root = document.createElement('div');
    root.id = 'graph';
    root.style.cssText = 'position:absolute;inset:0';
    wrap.appendChild(root);

    if (typeof window.ForceGraph === 'undefined') {
      root.innerHTML =
        '<div style="color:#8ea6c6;padding:20px;font-size:12px">Graph engine failed to load (offline?).</div>';
      graphiteRef.current = { Graph: null as any, raf: null, wrap, root, panel: null as any };
      return;
    }

    const Graph = window.ForceGraph()(root)
      .width(W)
      .height(H)
      .backgroundColor("rgba(0,0,0,0)")
      .cooldownTicks(180)
      .nodeRelSize(1)
      .nodeVal((n: any) => n.val)
      .nodeLabel((n: any) => {
        const pats = n.patterns.length ? n.patterns.join(", ") : "none";
        return (
          '<div style="font-family:ui-monospace,Menlo,monospace;padding:6px 9px;'
          + 'background:#0a1220;border:1px solid #26344b;border-radius:7px;color:#cdd9ec;'
          + 'font-size:11px;max-width:280px"><b style="color:#eef4ff">'
          + (n.label || n.short) + '</b><br>'
          + n.id + '<br>type: ' + n.type + ' &middot; depth ' + n.depth
          + '<br>in ' + n.inAmt + ' &middot; out ' + n.outAmt
          + '<br>patterns: ' + pats + '</div>'
        );
      })
      .linkColor((l: any) => (l.onPath ? 'rgba(0,255,156,.6)' : 'rgba(130,170,235,.22)'))
      .linkWidth((l: any) => (l.onPath ? 2.4 : 0.8))
      .linkDirectionalParticles((l: any) => (l.onPath ? 7 : 3))
      .linkDirectionalParticleWidth((l: any) => Math.min(4, 1.4 + Math.log10(l.total + 1)))
      .linkDirectionalParticleSpeed((l: any) => (l.onPath ? 0.011 : 0.006))
      .linkDirectionalParticleColor((l: any) => (l.onPath ? '#7dffc4' : '#5fecff'))
      .nodeCanvasObject(
        (
          node: any,
          ctx: CanvasRenderingContext2D,
          scale: number
        ) => {
          const t = performance.now();
          const hero =
            node.isSeed ||
            node.isEndpoint ||
            node.isSuspicious ||
            node.isObf ||
            node.onPath ||
            node.type === 'sanctioned_address' ||
            node.type === 'exchange';
          const col = node.color;
          let r: number, glowR: number, glowA: number, coreA: number;
          if (hero) {
            r = (2.6 + Math.sqrt(node.val)) * 1.0;
            const pulse =
              node.isSeed
                ? 1 + 0.22 * Math.sin(t / 300)
                : node.isSuspicious || node.type === 'sanctioned_address'
                  ? 1 + 0.18 * Math.sin(t / 170)
                  : 1;
            r *= pulse;
            glowR = r * 2.7;
            glowA = 0.9;
            coreA = 0.95;
          } else {
            r = 1.5 + Math.min(2.2, Math.sqrt(node.val) * 0.35);
            glowR = r * 2.0;
            glowA = 0.28;
            coreA = 0.5;
          }

          const glow = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, glowR);
          glow.addColorStop(0, hexA(col, glowA));
          glow.addColorStop(0.3, hexA(col, glowA * 0.4));
          glow.addColorStop(1, hexA(col, 0));
          ctx.fillStyle = glow;
          ctx.beginPath();
          ctx.arc(node.x, node.y, glowR, 0, 6.2832);
          ctx.fill();

          if (hero) {
            const sp = r * 3.0;
            ctx.strokeStyle = hexA(col, node.onPath ? 0.95 : 0.7);
            ctx.lineWidth = Math.max(0.35, 0.9 / scale);
            ctx.beginPath();
            ctx.moveTo(node.x - sp, node.y);
            ctx.lineTo(node.x + sp, node.y);
            ctx.moveTo(node.x, node.y - sp);
            ctx.lineTo(node.x, node.y + sp);
            ctx.stroke();
          }

          ctx.fillStyle = hexA('#ffffff', hero ? 1 : coreA);
          ctx.beginPath();
          ctx.arc(node.x, node.y, Math.max(0.6, r * (hero ? 0.5 : 0.7)), 0, 6.2832);
          ctx.fill();
          if (hero) {
            ctx.fillStyle = hexA(col, 0.95);
            ctx.beginPath();
            ctx.arc(node.x, node.y, Math.max(0.4, r * 0.3), 0, 6.2832);
            ctx.fill();
          }

          if (node.onPath || node.isSeed || node.isEndpoint) {
            ctx.fillStyle = 'rgba(224,234,250,0.92)';
            ctx.font = (10 / scale).toFixed(1) + 'px ui-monospace, Menlo, monospace';
            ctx.textAlign = 'center';
            ctx.fillText(node.label || node.short, node.x, node.y + r * 3.6);
          }
        }
      )
      .nodePointerAreaPaint((node: any, color: string, ctx: CanvasRenderingContext2D) => {
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(node.x, node.y, 6 + Math.sqrt(node.val) * 2, 0, 6.2832);
        ctx.fill();
      })
      .onNodeClick((node: any) => {
        Graph.centerAt(node.x, node.y, 600);
        Graph.zoom(Math.max(2.5, Graph.zoom()), 600);
        showPanel(node);
      })
      .onBackgroundClick(() => hidePanel())
      .onEngineStop(() => fitView(0));

    Graph.d3Force('charge').strength(-180);
    Graph.d3VelocityDecay(0.34);
    Graph.d3Force('link').distance((l: any) => (l.onPath ? 70 : 55));

    Graph.graphData({ nodes, links });
    // fit immediately as a first attempt; the interval below keeps re-fitting while layout settles
    if (typeof Graph.zoomToFit === 'function') {
      try { Graph.zoomToFit(500, 55); } catch (_) {}
    } else if (typeof Graph.fitView === 'function') {
      try { Graph.fitView(); } catch (_) {}
    }

    let fits = 0;
    fitTimerRef.current = setInterval(() => {
      fitView();
      if (++fits > 12) {
        clearInterval(fitTimerRef.current!);
        fitTimerRef.current = null;
      }
    }, 350);

    function fitView(pad?: number) {
      try {
        if (typeof Graph.zoomToFit === 'function') {
          Graph.zoomToFit(500, pad == null ? 55 : pad);
        } else if (typeof Graph.fitView === 'function') {
          Graph.fitView();
        }
      } catch (_) {}
    }

    (root.querySelector('#fit') as HTMLButtonElement)?.addEventListener('click', () => {
      if (typeof Graph.zoomToFit === 'function') Graph.zoomToFit(500, 55);
      else if (typeof Graph.fitView === 'function') Graph.fitView();
      hidePanel();
    });

    // avoid duplicate listeners / stale closures across StrictMode remounts    if (!wrap) return;
    const _rh = resizeHandler;
    window.removeEventListener('resize', _rh);
    window.addEventListener('resize', _rh);

    function resizeHandler() {
      const w = wrap.clientWidth || W;
      if (typeof Graph.width === 'function') Graph.width(w);
      if (sky.width !== undefined) sky.width = w;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (window as any).W = w;
      seedSky(w);
    }

    function seedSky(w: number) {
      bg.length = 0;
      for (let i = 0; i < 220; i++) {
        bg.push({
          x: Math.random() * w,
          y: Math.random() * H,
          r: Math.random() * 1.3 + 0.2,
          p: Math.random() * 6.2832,
          s: Math.random() * 0.02 + 0.004,
        });
      }
    }

    // panel
    const panel = document.createElement('div');
    panel.id = 'panel';
    panel.className = 'hidden';
    panel.innerHTML = `
      <div id="panel-head"></div>
      <div id="panel-body"></div>
      <button id="panel-close">close</button>
    `;
    wrap.appendChild(panel);

    function showPanel(n: any) {
      const head = document.getElementById('panel-head')!;
      head.innerHTML = '<b>' + (n.label || 'Unlabelled wallet') + '</b><br>'
        + '<span style="color:#8ea6c6">' + n.id + '</span>';
      const pats = n.patterns.length ? n.patterns.join(', ') : 'none';
      const body = document.getElementById('panel-body')!;
      body.innerHTML =
        '<div>type: <b>' + n.type + '</b></div>'
        + '<div>depth: <b>' + n.depth + '</b> &middot; confidence: <b>' + n.conf + '</b></div>'
        + '<div>incoming: <b>' + n.inAmt + '</b></div>'
        + '<div>outgoing: <b>' + n.outAmt + '</b></div>'
        + '<div>patterns: <b>' + pats + '</b></div>'
        + '<div>obfuscation point: <b>' + (n.isObf ? 'yes' : 'no') + '</b></div>';
      panel.classList.remove('hidden');
    }
    function hidePanel() {
      panel.classList.add('hidden');
    }
    (document.getElementById('panel-close') as HTMLButtonElement)?.addEventListener('click', hidePanel);

    graphiteRef.current = { Graph, raf: raf as number | undefined, wrap, root, panel, resizeHandler };
  }, [nodes, links]);

  useEffect(() => {
    renderGraph();
    return () => {
      const g = graphiteRef.current;
      if (!g) return;
      if (g.raf != null) cancelAnimationFrame(g.raf);
      if (fitTimerRef.current) clearInterval(fitTimerRef.current);
      if (g.Graph && typeof g.Graph._destructor === 'function') {
        try { g.Graph._destructor(); } catch (_) {}
      }
      if (g.root && g.root.parentNode) g.root.remove();
      if (g.panel && g.panel.parentNode) g.panel.remove();
      if (typeof g.resizeHandler === 'function') {
        window.removeEventListener('resize', g.resizeHandler);
      }
      graphiteRef.current = null;
    };
  }, [renderGraph]);

  if (nodes.length === 0) {
    return null;
  }

  return (
    <div className="graph-card" ref={containerRef}>
      <div
        style={{
          position: "relative",
          width: "100%",
          height: 660,
          background:
            "radial-gradient(ellipse at 50% 40%, #080c16 0%, #04050a 60%, #020205 100%)",
          border: "1px solid #1b2436",
          borderRadius: 10,
          overflow: "hidden",
          fontFamily: "ui-monospace,Menlo,Consolas,monospace",
        }}
      >
        <canvas id="sky" />
        <div id="legend">{LEGEND_HTML}</div>
        <div id="hint">drag to pan &middot; scroll to zoom &middot; click a star for detail</div>
        <button id="fit">reset view</button>
      </div>
      <style>{`
        #legend{position:absolute;top:10px;right:12px;display:flex;flex-wrap:wrap;gap:5px 11px;
          max-width:44%;justify-content:flex-end;z-index:5;font-size:10.5px;color:#9fb2cc;
          background:rgba(5,7,12,.62);padding:6px 9px;border-radius:8px;border:1px solid #172033;}
        #legend .lg{display:flex;align-items:center;gap:5px;}
        #legend .lg i{width:9px;height:9px;border-radius:50%;display:inline-block;box-shadow:0 0 6px;}
        #hint{position:absolute;bottom:9px;left:12px;z-index:5;font-size:11px;color:#61748f;letter-spacing:.02em;}
        #fit{position:absolute;bottom:8px;right:12px;z-index:6;background:#111a2b;color:#9fb2cc;
          border:1px solid #263349;border-radius:6px;padding:4px 10px;font:inherit;font-size:11px;cursor:pointer;}
        #fit:hover{background:#17233a;color:#dce6f5;}
        #panel{position:absolute;left:12px;bottom:34px;z-index:7;width:320px;max-width:70%;
          background:rgba(9,14,25,.92);border:1px solid #26344b;border-radius:10px;padding:12px 13px;
          color:#c7d4e8;font-size:12px;backdrop-filter:blur(4px);box-shadow:0 8px 30px rgba(0,0,0,.5);}
        #panel.hidden{display:none;}
        #panel-head{font-size:13px;color:#eaf1fb;margin-bottom:7px;word-break:break-all;line-height:1.35;}
        #panel-body div{margin:2px 0;color:#9fb2cc;}
        #panel-body b{color:#dbe6f6;font-weight:600;}
        #panel-close{margin-top:9px;background:#17233a;color:#8ea6c6;border:1px solid #2b3b55;
          border-radius:6px;padding:3px 9px;font:inherit;font-size:11px;cursor:pointer;}
      `}</style>
    </div>
  );
}
