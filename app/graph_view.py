"""Cyber-forensics "night sky" fund-flow graph.

Renders the investigation graph as a field of glowing stars (wallets) linked by
lines with particles flowing in the direction funds moved. Embedded as a
self-contained HTML/JS component (force-graph on canvas).
"""

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import streamlit.components.v1 as components

from src.domain.enums import NodeType

_TYPE_COLOR = {
    NodeType.SEED.value: "#00ff9c",
    NodeType.EXCHANGE.value: "#ff5c7a",
    NodeType.SANCTIONED_ADDRESS.value: "#ff2d2d",
    NodeType.MIXER.value: "#ffb020",
    NodeType.BRIDGE.value: "#ff8f1f",
    NodeType.CONTRACT_SERVICE.value: "#8a7dff",
    NodeType.SUSPICIOUS_WALLET.value: "#ff4d4d",
    NodeType.INTERMEDIATE_WALLET.value: "#4da3ff",
    NodeType.UNKNOWN.value: "#4a5b78",
}

_LEGEND = [
    ("Seed / victim", "#00ff9c"),
    ("Exchange", "#ff5c7a"),
    ("Sanctioned", "#ff2d2d"),
    ("Mixer / bridge", "#ffb020"),
    ("Suspect · low conf", "#ffd23f"),
    ("Suspect · medium", "#ff9f1a"),
    ("Suspect · high", "#ff2d2d"),
    ("Intermediate", "#4da3ff"),
    ("Contract", "#8a7dff"),
    ("Unknown", "#8494a8"),
]


MAX_RENDER_NODES = 110

# A suspect wallet is coloured by how confident the pipeline is that it matters,
# not by a flat "suspicious" red. Hard entity types (exchange / sanctioned /
# mixer / bridge / contract) keep their own colour: those are facts, not scores.
_CONF_RAMP = [
    (0.40, "#ffd23f"),   # weak lead - worth a look
    (0.60, "#ffa62b"),
    (0.80, "#ff5c33"),
    (1.01, "#ff2d2d"),   # strong lead
]
_FACT_TYPES = {
    NodeType.SEED.value,
    NodeType.EXCHANGE.value,
    NodeType.SANCTIONED_ADDRESS.value,
    NodeType.MIXER.value,
    NodeType.BRIDGE.value,
    NodeType.CONTRACT_SERVICE.value,
}


def _conf_color(score):
    for ceiling, color in _CONF_RAMP:
        if score < ceiling:
            return color
    return _CONF_RAMP[-1][1]


def _keep_nodes(result, highlighted_path):
    """A 400-node hairball is unreadable and force-layout on it never settles
    (graph looks blank). Keep the investigative core + the biggest wallets."""
    graph = result.graph
    if len(graph.nodes) <= MAX_RENDER_NODES:
        return set(graph.nodes), False

    keep = set(highlighted_path or [])
    lead_addrs = {
        l["address"]
        for l in (result.case_summary or {}).get("recommended_leads", [])
    }
    for nid, n in graph.nodes.items():
        if (
            n.is_seed
            or n.is_endpoint
            or n.is_suspicious
            or n.is_obfuscation_point
            or n.address.entity_type.value in ("exchange", "sanctioned", "mixer", "bridge")
            or nid in lead_addrs
        ):
            keep.add(nid)

    def _amt(n):
        try:
            return float(n.incoming_amount) + float(n.outgoing_amount)
        except (TypeError, ValueError):
            return 0.0

    rest = sorted(
        (nid for nid in graph.nodes if nid not in keep),
        key=lambda x: _amt(graph.nodes[x]),
        reverse=True,
    )
    for nid in rest:
        if len(keep) >= MAX_RENDER_NODES:
            break
        keep.add(nid)

    # Keeping a node without the wallets it received from leaves it floating in
    # the render with no visible connection - a star that appears related to
    # nothing. Pull in each kept node's strongest inbound chain back toward the
    # seed so what is drawn is actually a graph.
    best_parent = {}
    for e in graph.edges:
        f, t = e.transfer.normalized_from(), e.transfer.normalized_to()
        if f not in graph.nodes or t not in graph.nodes or f == t:
            continue
        if graph.nodes[f].depth >= graph.nodes[t].depth:
            continue  # only walk back toward the seed
        cur = best_parent.get(t)
        if cur is None or e.transfer.amount_float > cur[1]:
            best_parent[t] = (f, e.transfer.amount_float)

    # Bounded: a deep chain could otherwise drag in 12 ancestors per kept node
    # and blow the render budget the cap exists to protect.
    ceiling = int(MAX_RENDER_NODES * 1.3)
    for nid in list(keep):
        hops = 0
        cur = nid
        while hops < 12 and len(keep) < ceiling:
            parent = best_parent.get(cur)
            if parent is None or parent[0] in keep:
                break
            keep.add(parent[0])
            cur = parent[0]
            hops += 1

    # anything still isolated genuinely has no traced link to the rest
    linked = set()
    for e in graph.edges:
        f, t = e.transfer.normalized_from(), e.transfer.normalized_to()
        if f in keep and t in keep and f != t:
            linked.add(f)
            linked.add(t)
    keep = {n for n in keep if n in linked or n == graph.seed_address}
    return keep, True


def _build_payload(result, highlighted_path):
    graph = result.graph
    hl = set(highlighted_path or [])
    hl_pairs = set()
    if highlighted_path:
        hl_pairs = {
            (highlighted_path[i], highlighted_path[i + 1])
            for i in range(len(highlighted_path) - 1)
        }

    keep, pruned = _keep_nodes(result, highlighted_path)

    # who sent to / received from each kept wallet (for the flow readout)
    inbound, outbound = defaultdict(set), defaultdict(set)
    for e in graph.edges:
        f, t = e.transfer.normalized_from(), e.transfer.normalized_to()
        if f in keep and t in keep:
            outbound[f].add(t)
            inbound[t].add(f)

    def _sh(a):
        return f"{a[:6]}..{a[-4:]}"

    # per-wallet confidence from the ranked leads (0..1)
    lead_score = {
        l["address"]: l.get("score", 0.0)
        for l in (result.case_summary or {}).get("recommended_leads", [])
    }

    def _node_score(n, ranked):
        """How confident are we that this wallet matters to the case?

        A wallet that made the ranked-leads list uses that score directly.
        Otherwise it is derived from the evidence already on the node: how much
        of the traced money reached it, how strong its behavioural verdict is,
        and how many patterns fired on it. Shown as a number in the tooltip so
        the colour is never the only thing asserting it.
        """
        if ranked is not None:
            return round(float(ranked), 2)
        if not (n.is_suspicious or n.is_obfuscation_point):
            return None
        derived = (
            0.20
            + 0.45 * min(1.0, n.taint_fraction)
            + 0.25 * n.behavior_confidence
            + 0.05 * min(2, len(n.pattern_flags))
        )
        return round(min(0.80, derived), 2)

    def _node_color(n, score):
        t = n.node_type.value
        # the seed is retyped suspicious_wallet once fan-out fires on it, but
        # it is still the reported wallet and keeps the seed colour
        if n.is_seed:
            return _TYPE_COLOR[NodeType.SEED.value]
        if t in _FACT_TYPES:
            return _TYPE_COLOR.get(t, "#8494a8")
        if score is None:
            return _TYPE_COLOR.get(t, "#8494a8")
        return _conf_color(score)

    nodes = []
    for nid, n in graph.nodes.items():
        if nid not in keep:
            continue
        try:
            amt = float(n.incoming_amount) + float(n.outgoing_amount)
        except (TypeError, ValueError):
            amt = 0.0
        if n.is_seed:
            base = 9.0
        elif n.is_endpoint:
            base = 7.0
        elif n.is_suspicious or n.is_obfuscation_point:
            base = 5.5
        else:
            base = 3.0
        nodes.append(
            {
                "id": nid,
                "short": f"{nid[:6]}...{nid[-4:]}",
                "label": n.address.label or "",
                "type": n.node_type.value,
                "color": _node_color(n, _node_score(n, lead_score.get(nid))),
            "score": _node_score(n, lead_score.get(nid)),
            "ranked": nid in lead_score,
                "depth": n.depth,
                "inAmt": n.incoming_amount,
                "outAmt": n.outgoing_amount,
                "patterns": list(n.pattern_flags),
                "conf": round(n.address.confidence, 2),
                "isSeed": n.is_seed,
                "isEndpoint": n.is_endpoint,
                "isSuspicious": n.is_suspicious,
                "isObf": n.is_obfuscation_point,
                "onPath": nid in hl,
                "val": round(base + min(6.0, math.log10(amt + 1)), 2),
                "fromN": len(inbound[nid]),
                "toN": len(outbound[nid]),
                "fromEx": [_sh(a) for a in sorted(inbound[nid])[:6]],
                "toEx": [_sh(a) for a in sorted(outbound[nid])[:6]],
            }
        )

    # how fast each wallet forwarded funds it received (seconds); drives the
    # particle flow speed on edges arriving at that wallet
    fwd_gap = {
        d["address"]: d["received_to_forwarded_seconds"]
        for d in result.pattern_detections.get("hop_velocity", [])
    }

    def _flow(target_addr, on_path):
        gap = fwd_gap.get(target_addr)
        if gap is None:  # not a fast-forwarder: dead-end / endpoint / dormant
            speed, parts = 0.0011, 1
        elif gap <= 30:
            speed, parts = 0.0075, 3
        elif gap <= 120:
            speed, parts = 0.0045, 3
        elif gap <= 300:
            speed, parts = 0.0028, 2
        else:
            speed, parts = 0.0016, 1
        if on_path:
            speed = min(0.011, speed * 1.4)
            parts = min(6, parts + 2)
        return round(speed, 5), parts

    pairs = {}
    for e in graph.edges:
        f, t = e.transfer.normalized_from(), e.transfer.normalized_to()
        if f not in keep or t not in keep:
            continue
        rec = pairs.setdefault(
            (f, t),
            {"source": f, "target": t, "count": 0, "total": 0.0, "tokens": set()},
        )
        rec["count"] += 1
        rec["total"] += e.transfer.amount_float
        if e.transfer.token_symbol:
            rec["tokens"].add(e.transfer.token_symbol)

    links = []
    for (f, t), rec in pairs.items():
        on_path = (f, t) in hl_pairs
        speed, parts = _flow(t, on_path)
        links.append(
            {
                "source": f,
                "target": t,
                "count": rec["count"],
                "total": round(rec["total"], 2),
                "tokens": "/".join(sorted(rec["tokens"])) or "-",
                "onPath": on_path,
                "speed": speed,
                "parts": parts,
                "fwdGap": fwd_gap.get(t),
            }
        )

    return {"nodes": nodes, "links": links, "pruned": pruned, "total": len(graph.nodes)}


def render_star_graph(result, highlighted_path=None, height: int = 660):
    payload = _build_payload(result, highlighted_path)
    if payload.get("pruned"):
        st.caption(
            f"Showing the {len(payload['nodes'])} most relevant of "
            f"{payload['total']} wallets (seed, endpoints, suspicious nodes, "
            f"leads, strongest path, and the largest-value wallets)."
        )
    data_json = json.dumps(payload).replace("</", "<\\/")
    legend_html = "".join(
        f'<span class="lg"><i style="background:{c}"></i>{name}</span>'
        for name, c in _LEGEND
    )
    html = (
        _TEMPLATE.replace("__DATA__", data_json)
        .replace("__LEGEND__", legend_html)
        .replace("__HEIGHT__", str(height))
    )
    components.html(html, height=height + 6, scrolling=False)


_TEMPLATE = r"""
<div id="wrap" style="position:relative;width:100%;height:__HEIGHT__px;
     background:radial-gradient(ellipse at 50% 40%, #080c16 0%, #04050a 60%, #020205 100%);
     border:1px solid #1b2436;border-radius:10px;overflow:hidden;
     font-family:ui-monospace,Menlo,Consolas,monospace;">
  <canvas id="sky"></canvas>
  <div id="graph" style="position:absolute;inset:0;"></div>
  <div id="legend">__LEGEND__</div>
  <div id="panel" class="hidden">
    <div id="panel-head"></div>
    <div id="panel-body"></div>
    <button id="panel-close">close</button>
  </div>
  <div id="hint">flows left &rarr; right by hop &middot; drag to pan &middot; scroll to zoom &middot; click a star</div>
  <div id="toolbar">
    <button id="dag" title="toggle flow / free layout">flow view</button>
    <button id="fit">reset view</button>
    <button id="full" title="fullscreen">&#9974; full screen</button>
  </div>
</div>

<style>
  #legend{position:absolute;top:10px;right:12px;display:flex;flex-wrap:wrap;gap:5px 11px;
    max-width:44%;justify-content:flex-end;z-index:5;font-size:10.5px;color:#9fb2cc;
    background:rgba(5,7,12,.62);padding:6px 9px;border-radius:8px;border:1px solid #172033;}
  #legend .lg{display:flex;align-items:center;gap:5px;}
  #legend .lg i{width:9px;height:9px;border-radius:50%;display:inline-block;box-shadow:0 0 6px;}
  #hint{position:absolute;bottom:9px;left:12px;z-index:5;font-size:11px;color:#61748f;letter-spacing:.02em;}
  #toolbar{position:absolute;bottom:8px;right:12px;z-index:6;display:flex;gap:6px;}
  #toolbar button{background:#111a2b;color:#9fb2cc;border:1px solid #263349;border-radius:6px;
    padding:4px 10px;font:inherit;font-size:11px;cursor:pointer;}
  #toolbar button:hover{background:#17233a;color:#dce6f5;}
  #toolbar button.on{background:#1c3350;color:#dfeaff;border-color:#3a5a86;}
  #wrap:fullscreen{width:100vw;height:100vh;border-radius:0;}
  #wrap.overlay{position:fixed;inset:0;width:100%;height:100%;border-radius:0;z-index:99999;}
  #panel{position:absolute;left:12px;bottom:34px;z-index:7;width:320px;max-width:70%;
    background:rgba(9,14,25,.92);border:1px solid #26344b;border-radius:10px;padding:12px 13px;
    color:#c7d4e8;font-size:12px;backdrop-filter:blur(4px);box-shadow:0 8px 30px rgba(0,0,0,.5);}
  #panel.hidden{display:none;}
  #panel-head{font-size:13px;color:#eaf1fb;margin-bottom:7px;word-break:break-all;line-height:1.35;}
  #panel-body div{margin:2px 0;color:#9fb2cc;}
  #panel-body b{color:#dbe6f6;font-weight:600;}
  #panel-close{margin-top:9px;background:#17233a;color:#8ea6c6;border:1px solid #2b3b55;
    border-radius:6px;padding:3px 9px;font:inherit;font-size:11px;cursor:pointer;}
</style>

<script src="https://cdn.jsdelivr.net/npm/force-graph@1.43.5/dist/force-graph.min.js"></script>
<script>
window.addEventListener('error', function(e){
  var el = document.getElementById('graph');
  if (el && !el.querySelector('canvas')) {
    el.innerHTML = '<div style="color:#c98;padding:16px;font:12px ui-monospace">'
      + 'graph error: ' + (e && e.message) + ' @' + (e && e.lineno) + '</div>';
  }
});
(function(){
  var DATA = __DATA__;
  var wrap = document.getElementById('wrap');
  var W = (wrap && wrap.clientWidth) || 800, H = __HEIGHT__;

  var sky = document.getElementById('sky');
  sky.width = W; sky.height = H;
  var sctx = sky.getContext('2d');
  var bg = [];
  function seedSky(w){
    bg = [];
    for (var i=0;i<120;i++){
      bg.push({x:Math.random()*w, y:Math.random()*H, r:Math.random()*1.1+0.15,
        p:Math.random()*6.2832, s:Math.random()*0.02+0.004});
    }
  }
  seedSky(W);
  function drawSky(t){
    sctx.clearRect(0,0,sky.width,H);
    for (var i=0;i<bg.length;i++){
      var s=bg[i], a=0.12+0.30*Math.abs(Math.sin(s.p + t*s.s));
      sctx.beginPath();
      sctx.fillStyle='rgba(140,170,220,'+a.toFixed(3)+')';
      sctx.arc(s.x,s.y,s.r,0,6.2832); sctx.fill();
    }
    requestAnimationFrame(drawSky);
  }
  requestAnimationFrame(drawSky);

  // colour == confidence, so always spell the number out next to it
  function scoreLine(n){
    if (n.score === null || n.score === undefined) return '';
    return '<br>' + (n.ranked ? 'lead confidence' : 'signal strength')
      + ': <b>' + (n.score * 100).toFixed(0) + '%</b>';
  }

  function hexA(hex,a){
    hex=hex.replace('#','');
    if(hex.length===3) hex=hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
    var r=parseInt(hex.slice(0,2),16),g=parseInt(hex.slice(2,4),16),b=parseInt(hex.slice(4,6),16);
    return 'rgba('+r+','+g+','+b+','+a+')';
  }

  if (typeof ForceGraph === 'undefined'){
    document.getElementById('graph').innerHTML =
      '<div style="color:#8ea6c6;padding:20px;font-size:12px">Graph engine failed to load (offline?).</div>';
    return;
  }
  if (!DATA.nodes || DATA.nodes.length === 0){
    document.getElementById('graph').innerHTML =
      '<div style="color:#8ea6c6;padding:24px;font-size:12px">No fund movement was observed from this address.</div>';
    return;
  }

  var Graph = ForceGraph()(document.getElementById('graph'))
    .width(W).height(H)
    .backgroundColor('rgba(0,0,0,0)')
    .cooldownTicks(200)
    .dagMode('lr')
    .dagLevelDistance(DATA.nodes.length > 80 ? 46 : 68)
    .onDagError(function(){ return true; })   // tolerate cycles (money flowing back)
    .linkDirectionalArrowLength(3.6)
    .linkDirectionalArrowRelPos(0.92)
    .linkDirectionalArrowColor(function(l){
      return l.onPath ? '#7dffc4' : 'rgba(150,180,235,.55)';
    })
    .nodeRelSize(1)
    .nodeVal(function(n){ return n.val; })
    .nodeLabel(function(n){
      var pats = n.patterns.length ? n.patterns.join(', ') : 'none';
      return '<div style="font-family:ui-monospace,Menlo,monospace;padding:6px 9px;'
        + 'background:#0a1220;border:1px solid #26344b;border-radius:7px;color:#cdd9ec;'
        + 'font-size:11px;max-width:290px"><b style="color:#eef4ff">'
        + (n.label || n.short) + '</b><br>' + n.id
        + '<br>hop ' + n.depth + ' &middot; ' + n.type
        + '<br>in ' + n.inAmt + ' &middot; out ' + n.outAmt
        + '<br>from ' + n.fromN + ' wallet(s) &rarr; to ' + n.toN + ' wallet(s)'
        + scoreLine(n)
        + '<br>patterns: ' + pats + '</div>';
    })
    .linkColor(function(l){ return l.onPath ? 'rgba(0,255,156,.6)' : 'rgba(130,170,235,.22)'; })
    .linkWidth(function(l){ return l.onPath ? 2.4 : 0.8; })
    .linkDirectionalParticles(function(l){ return l.parts; })
    .linkDirectionalParticleWidth(function(l){ return Math.min(4, 1.4 + Math.log10(l.total+1)); })
    .linkDirectionalParticleSpeed(function(l){ return l.speed; })
    .linkDirectionalParticleColor(function(l){ return l.onPath ? '#7dffc4' : '#5fecff'; })
    .nodeCanvasObject(function(node, ctx, scale){
      var t = performance.now();
      var hero = node.isSeed || node.isEndpoint || node.isSuspicious
              || node.isObf || node.onPath || node.type==='sanctioned_address'
              || node.type==='exchange';
      // A node with no valid position yet (or NaN after a layout switch) would
      // make createRadialGradient/arc throw, killing the render loop for EVERY
      // node and leaving a blank canvas. Skip it instead.
      if (!isFinite(node.x) || !isFinite(node.y)) return;
      var col = node.color;
      var r, glowR, glowA, coreA;
      if (hero){
        r = (2.6 + Math.sqrt(node.val)) * 1.0;
        var pulse = node.isSeed ? 1 + 0.22*Math.sin(t/300)
                  : (node.isSuspicious || node.type==='sanctioned_address') ? 1 + 0.18*Math.sin(t/170)
                  : 1;
        r *= pulse; glowR = r*2.7; glowA = 0.9; coreA = 0.95;
      } else {
        // crowd stars: smaller than heroes but still clearly readable
        r = 2.4 + Math.min(3.0, Math.sqrt(node.val) * 0.5);
        glowR = r*2.3; glowA = 0.5; coreA = 0.85;
      }
      // Radii are graph units, so a zoomed-out view renders sub-pixel stars
      // that read as an empty canvas. Hold a minimum on-screen size.
      var minR = (hero ? 3.4 : 2.2) / scale;
      if (r < minR){ glowR *= minR / r; r = minR; }

      var glow = ctx.createRadialGradient(node.x,node.y,0, node.x,node.y, glowR);
      glow.addColorStop(0, hexA(col, glowA));
      glow.addColorStop(0.30, hexA(col, glowA*0.4));
      glow.addColorStop(1, hexA(col, 0));
      ctx.fillStyle = glow;
      ctx.beginPath(); ctx.arc(node.x,node.y,glowR,0,6.2832); ctx.fill();

      ctx.fillStyle = hexA('#ffffff', hero ? 1 : coreA);
      ctx.beginPath(); ctx.arc(node.x,node.y,Math.max(0.6, r*(hero?0.5:0.7)),0,6.2832); ctx.fill();
      if (hero){
        ctx.fillStyle = hexA(col,0.95);
        ctx.beginPath(); ctx.arc(node.x,node.y,Math.max(0.4,r*0.3),0,6.2832); ctx.fill();
      }

      if (node.onPath || node.isSeed || node.isEndpoint){
        ctx.fillStyle = 'rgba(224,234,250,0.92)';
        ctx.font = (10/scale).toFixed(1) + 'px ui-monospace, Menlo, monospace';
        ctx.textAlign = 'center';
        ctx.fillText(node.label || node.short, node.x, node.y + r*3.6);
      }
    })
    .nodePointerAreaPaint(function(node, color, ctx){
      if (!isFinite(node.x) || !isFinite(node.y)) return;
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(node.x,node.y, 6 + Math.sqrt(node.val)*2, 0, 6.2832); ctx.fill();
    })
    .onNodeClick(function(node){
      Graph.centerAt(node.x, node.y, 600);
      Graph.zoom(Math.max(2.5, Graph.zoom()), 600);
      showPanel(node);
    })
    .onBackgroundClick(function(){ hidePanel(); })
    .onEngineStop(function(){ fitView(); });

  // graphData FIRST — the d3 forces are only created once data is set, so
  // touching Graph.d3Force('charge') before this throws and the canvas stays blank.
  Graph.graphData(DATA);

  var BIG = DATA.nodes.length > 60;
  // Flow mode packs nodes into dag columns, so it wants weak repulsion and
  // short links. Free mode has no columns holding the shape apart - it needs
  // much stronger repulsion and longer links or it collapses into a blob.
  function applyForces(dag){
    try {
      Graph.d3Force('charge').strength(
        dag ? (BIG ? -55 : -140) : (BIG ? -260 : -420)
      );
      Graph.d3Force('link').distance(function(l){
        var base = dag ? 26 : (BIG ? 70 : 95);
        return l.onPath ? base * 1.9 : base;
      });
      Graph.d3VelocityDecay(dag ? 0.45 : 0.30);
      Graph.d3ReheatSimulation();
    } catch (e) {}
  }
  applyForces(true);

  // ms MUST stay below the settling-interval below: zoomToFit's tween is
  // cancelled by the next call, so a long one never reaches its target and
  // the graph stays zoomed out in a corner.
  function fitView(ms){
    ms = ms || 0;
    try {
      // No centerAt here: zoomToFit pans and zooms itself, and a second
      // concurrent tween just fights it, leaving the graph off-centre.
      // Always fit EVERY node. Framing only the "core" wallets clipped whole
      // hops off the top and bottom whenever the interesting nodes did not
      // happen to span the graph, so a deeper trace could look emptier than a
      // shallow one. Padding clears the address captions, not just the dots.
      Graph.zoomToFit(ms, DATA.nodes.length <= 60 ? 80 : 55);
      var z = Graph.zoom();
      // No meaningful zoom floor: clamping the fit is what pushed big graphs
      // off-screen. Stars stay readable via the min-screen-radius in the
      // node painter instead.
      if (z < 0.05) Graph.zoom(0.05, ms);
      else if (z > 2.6) Graph.zoom(2.6, ms);
    } catch (e) {}
  }
  // Re-fit repeatedly while the layout is still settling, then stop.
  var fitTimer = null;
  function settleAndFit(n){
    if (fitTimer) clearInterval(fitTimer);
    var fits = 0;
    fitTimer = setInterval(function(){
      fitView();
      if (++fits > n) { clearInterval(fitTimer); fitTimer = null; }
    }, 280);
  }
  settleAndFit(18);

  document.getElementById('fit').onclick = function(){ fitView(55); hidePanel(); };

  // --- flow / free layout toggle ---
  var dagOn = true;
  document.getElementById('dag').classList.add('on');
  document.getElementById('dag').onclick = function(){
    dagOn = !dagOn;
    this.classList.toggle('on', dagOn);
    this.textContent = dagOn ? 'flow view' : 'free layout';
    try {
      Graph.dagMode(dagOn ? 'lr' : null);
      // dagMode pins each node on the dag axis via fx/fy and does NOT release
      // them when you turn it off — nodes stay frozen, and any node the dag
      // never levelled (cycle nodes skipped by onDagError) carries an undefined
      // fx that becomes NaN once the simulation runs. Clear and re-seed.
      DATA.nodes.forEach(function(n){
        if (!dagOn) { n.fx = undefined; n.fy = undefined; }
        if (!isFinite(n.x)) n.x = (Math.random() - 0.5) * 200;
        if (!isFinite(n.y)) n.y = (Math.random() - 0.5) * 200;
      });
    } catch (e) {}
    applyForces(dagOn);
    // Free mode re-lays the whole graph from the dag positions, which takes a
    // while - keep re-fitting until it settles instead of framing the old shape.
    settleAndFit(dagOn ? 12 : 26);
  };

  // --- fullscreen ---
  function syncSize(){
    var fs = document.fullscreenElement === wrap;
    var w = fs ? window.innerWidth  : (wrap.clientWidth  || W);
    var h = fs ? window.innerHeight : __HEIGHT__;
    W = w; H = h;
    Graph.width(w).height(h);
    sky.width = w; sky.height = h; seedSky(w);
    setTimeout(function(){ fitView(); }, 120);
  }
  document.getElementById('full').onclick = function(){
    if (document.fullscreenElement) { document.exitFullscreen(); return; }
    var req = wrap.requestFullscreen || wrap.webkitRequestFullscreen;
    if (req) { req.call(wrap).catch(function(){ overlayFallback(); }); }
    else overlayFallback();
  };
  document.addEventListener('fullscreenchange', syncSize);
  function overlayFallback(){
    // no iframe fullscreen permission — expand within the frame instead
    wrap.classList.toggle('overlay');
    syncSize();
  }
  window.addEventListener('resize', syncSize);

  function showPanel(n){
    document.getElementById('panel-head').innerHTML =
      '<b>' + (n.label || 'Unlabelled wallet') + '</b><br>'
      + '<span style="color:#8ea6c6">' + n.id + '</span>';
    var pats = n.patterns.length ? n.patterns.join(', ') : 'none';
    var fromLine = n.isSeed
      ? '<div>this is the <b>reported wallet</b> (hop 0)</div>'
      : '<div>received from <b>' + n.fromN + '</b> wallet(s)'
        + (n.fromEx.length ? ': ' + n.fromEx.join(', ') : '') + '</div>';
    var toLine = n.toN
      ? '<div>sent onward to <b>' + n.toN + '</b> wallet(s): ' + n.toEx.join(', ')
        + (n.toN > n.toEx.length ? ' &hellip;' : '') + '</div>'
      : '<div>no onward transfers observed (trail ends / not expanded)</div>';
    document.getElementById('panel-body').innerHTML =
      '<div>type: <b>' + n.type + '</b> &middot; hop <b>' + n.depth + '</b></div>'
      + '<div>in <b>' + n.inAmt + '</b> &middot; out <b>' + n.outAmt + '</b></div>'
      + fromLine + toLine
      + (scoreLine(n) ? '<div>' + scoreLine(n).slice(4) + '</div>' : '')
      + '<div>patterns: <b>' + pats + '</b></div>';
    document.getElementById('panel').classList.remove('hidden');
  }
  function hidePanel(){ document.getElementById('panel').classList.add('hidden'); }
  document.getElementById('panel-close').onclick = hidePanel;
})();
</script>
"""
