/* Force-directed memory galaxy on canvas: the layout runs in 3D, every frame is
   projected with a slow auto-rotation and perspective, far nodes fade into the fog.
   No libraries — the whole point is that this file is readable and the vault it
   draws is a real folder of markdown, not a demo animation. */
(function (global) {
  'use strict';

  /* #rrggbb -> rgba() without allocating a canvas gradient per edge per frame. */
  var HEXCACHE = {};
  function hexA(hex, a) {
    var key = hex + '|' + a.toFixed(3);
    if (HEXCACHE[key]) return HEXCACHE[key];
    var n = parseInt((hex || '#8fa3bf').slice(1), 16);
    var out = 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
    if (Object.keys(HEXCACHE).length < 900) HEXCACHE[key] = out;
    return out;
  }

  /* Fibonacci sphere filled by volume: an even starting cloud, so the sim only
     has to relax it rather than untangle a knot at the origin. */
  function seed(i, n, W, H) {
    var spread = Math.min(W, H) * 0.42 || 260;
    var yy = 1 - 2 * (i + 0.5) / n, ring = Math.sqrt(1 - yy * yy), ang = i * 2.39996;
    var rad = spread * Math.cbrt((i + 1) / n);
    return { x: W / 2 + Math.cos(ang) * ring * rad, y: H / 2 + yy * rad, z: Math.sin(ang) * ring * rad };
  }

  function MemoryGraph(canvas, opts) {
    this.cv = canvas;
    this.ctx = canvas.getContext('2d');
    this.opts = opts || {};
    this.nodes = [];
    this.links = [];
    this.byId = new Map();
    this.adj = new Map();
    this.hidden = new Set();          // filtered-out types
    this.live = {};                   // Home Assistant state per node id
    this.focus = null;                // focused node id
    this.trace = null;                // [ids] shortest path
    this.showLabels = true;
    this.dim = false;
    this.tx = 0; this.ty = 0; this.scale = 1;
    this.yaw = 0; this.pitch = -0.32;  // camera angles
    this.idleSince = 0;               // auto-rotation pauses after you turn it by hand
    // Layout constants, kept together so the cloud can be retuned in one place.
    this.p = {
      repulsion: 6000,    // node-node push, falls off with distance squared
      alphaFloor: 0.05,    // never freeze: the cloud keeps breathing
      drift: 0.05,         // amplitude of the idle wander
      activity: 0,         // raised while JARVIS is working
      reach: 600,        // ignore pairs further apart than this
      spring: 0.035,       // link pull toward its rest length
      rest: 120,          // rest length before radii
      centre: 0.0026,      // drift toward the middle
      damping: 0.82,
      depth: 1100,         // camera distance — smaller = stronger perspective
      spin: 0.0016         // auto-rotation per frame, faster while working
    };
    this.alpha = 1;                   // simulation temperature
    this.grab = null; this.hover = null;
    this.pulses = [];    // packets of light travelling along edges
    this.stars = [];
    for (var s = 0; s < 260; s++) {   // fixed backdrop, rotates with the camera
      var u = Math.random() * 2 - 1, th = Math.random() * Math.PI * 2, q = Math.sqrt(1 - u * u);
      this.stars.push({ x: Math.cos(th) * q, y: u, z: Math.sin(th) * q,
                        b: 0.15 + Math.random() * 0.5, r: Math.random() < 0.08 ? 1.4 : 0.8 });
    }
    this._bind();
    this._needsFit = false;
    this._loop = this._loop.bind(this);
    if (global.ResizeObserver) {
      var self = this;
      new ResizeObserver(function () {
        if (self._needsFit) self.fit();
      }).observe(canvas);
    }
    requestAnimationFrame(this._loop);
  }

  MemoryGraph.prototype.setData = function (data) {
    var prev = new Map(this.nodes.map(function (n) { return [n.id, n]; }));
    var W = this.cv.clientWidth || 900, H = this.cv.clientHeight || 600;
    this.nodes = data.nodes.map(function (n, i) {
      var old = prev.get(n.id), at = old || seed(i, data.nodes.length, W, H);
      return Object.assign({}, n, {
        x: at.x, y: at.y, z: at.z || 0,
        vx: 0, vy: 0, vz: 0,
        phase: (i * 1.7) % (Math.PI * 2),   // unique drift offset per node
        wob: 0.55 + ((i * 37) % 100) / 140, // and a slightly different speed
        // "groesse" in a note's frontmatter makes a hand-picked star bigger.
        r: 4 + Math.min(16, Math.sqrt(n.degree || 0) * 2.6) + (parseFloat(n.groesse) || 0)
      });
    });
    this.byId = new Map(this.nodes.map(function (n) { return [n.id, n]; }));
    var self = this;
    this.links = data.links.filter(function (l) {
      return self.byId.has(l.source) && self.byId.has(l.target);
    }).map(function (l) {
      return { s: self.byId.get(l.source), t: self.byId.get(l.target) };
    });
    this.adj = new Map();
    this.nodes.forEach(function (n) { self.adj.set(n.id, []); });
    this.links.forEach(function (l) {
      self.adj.get(l.s.id).push(l.t.id);
      self.adj.get(l.t.id).push(l.s.id);
    });
    this.alpha = 1;
    this.warmup(260);
    this.fit();
  };

  /* Run the simulation off-screen so the first frame the user sees is already
     settled, instead of watching 133 nodes crawl apart. */
  MemoryGraph.prototype.warmup = function (steps) {
    for (var i = 0; i < steps; i++) this._tick();
  };

  MemoryGraph.prototype.relayout = function (steps) {
    var W = this.cv.clientWidth || 900, H = this.cv.clientHeight || 600, n = this.nodes.length;
    this.nodes.forEach(function (nd, i) {
      var at = seed(i, n, W, H);
      nd.x = at.x; nd.y = at.y; nd.z = at.z;
      nd.vx = nd.vy = nd.vz = 0;
    });
    this.alpha = 1;
    this.warmup(steps || 400);
    this.fit();
    return this.span();
  };

  MemoryGraph.prototype.span = function () {
    this._project();
    var vis = this.nodes.filter(this.visible, this);
    var xs = vis.map(function (n) { return n.sx; }).sort(function (a, b) { return a - b; });
    var ys = vis.map(function (n) { return n.sy; }).sort(function (a, b) { return a - b; });
    var c = Math.floor(xs.length * 0.03);
    return {
      w: Math.round(xs[xs.length - 1 - c] - xs[c]),
      h: Math.round(ys[ys.length - 1 - c] - ys[c]),
      scale: +this.scale.toFixed(2)
    };
  };

  MemoryGraph.prototype.visible = function (n) { return !this.hidden.has(n.type); };

  /* { id: {online: bool|null, pegel: 0..1|null} } — kept by id, so a vault
     reload doesn't wipe it. */
  MemoryGraph.prototype.setLive = function (map) { this.live = map || {}; };

  MemoryGraph.prototype.setFilter = function (hiddenTypes) {
    this.hidden = new Set(hiddenTypes);
    this.alpha = Math.max(this.alpha, 0.45);
  };

  MemoryGraph.prototype.setFocus = function (id, additive) {
    if (!id) { this.focus = null; this.trace = null; this._emit(null); return; }
    id = String(id).toLowerCase();
    if (!this.byId.has(id)) return false;
    if (additive && this.focus && this.focus !== id) {
      this.trace = this._path(this.focus, id);
    } else {
      this.focus = id; this.trace = null;
    }
    this._emit(this.byId.get(id));
    return true;
  };

  MemoryGraph.prototype._emit = function (node) {
    if (this.opts.onSelect) this.opts.onSelect(node, this.trace);
  };

  /* BFS shortest path, so "shift-click to trace" means something real. */
  MemoryGraph.prototype._path = function (a, b) {
    var prev = new Map([[a, null]]), queue = [a];
    while (queue.length) {
      var cur = queue.shift();
      if (cur === b) break;
      var nbrs = this.adj.get(cur) || [];
      for (var i = 0; i < nbrs.length; i++) {
        if (!prev.has(nbrs[i])) { prev.set(nbrs[i], cur); queue.push(nbrs[i]); }
      }
    }
    if (!prev.has(b)) return null;
    var path = [], step = b;
    while (step) { path.unshift(step); step = prev.get(step); }
    return path;
  };

  MemoryGraph.prototype._neighbours = function (id) {
    var set = new Set([id]);
    (this.adj.get(id) || []).forEach(function (x) { set.add(x); });
    return set;
  };

  /* ── simulation (3D) ────────────────────── */
  MemoryGraph.prototype._tick = function () {
    this.t = (this.t || 0) + 1;
    var nodes = this.nodes, n = nodes.length;
    var W = this.cv.clientWidth, H = this.cv.clientHeight;
    var cx = W / 2, cy = H / 2;

    for (var i = 0; i < n; i++) {
      var a = nodes[i];
      for (var j = i + 1; j < n; j++) {
        var b = nodes[j];
        var dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
        var d2 = dx * dx + dy * dy + dz * dz || 0.01;
        if (d2 > this.p.reach * this.p.reach) continue;
        var d = Math.sqrt(d2);
        var f = (this.p.repulsion * this.alpha) / d2;
        var fx = (dx / d) * f, fy = (dy / d) * f, fz = (dz / d) * f;
        a.vx -= fx; a.vy -= fy; a.vz -= fz; b.vx += fx; b.vy += fy; b.vz += fz;
      }
    }
    for (var k = 0; k < this.links.length; k++) {
      var l = this.links[k];
      var dx2 = l.t.x - l.s.x, dy2 = l.t.y - l.s.y, dz2 = l.t.z - l.s.z;
      var dist = Math.sqrt(dx2 * dx2 + dy2 * dy2 + dz2 * dz2) || 0.01;
      var target = this.p.rest + (l.s.r + l.t.r);
      // Divide by the smaller endpoint degree: without this a 47-link hub
      // gets 47x the pull and drags the whole cloud into a knot.
      var strength = 1 / Math.max(1, Math.min(l.s.degree || 1, l.t.degree || 1));
      var force = ((dist - target) / dist) * this.p.spring * strength * this.alpha;
      var ax = dx2 * force, ay = dy2 * force, az = dz2 * force;
      l.s.vx += ax; l.s.vy += ay; l.s.vz += az; l.t.vx -= ax; l.t.vy -= ay; l.t.vz -= az;
    }
    for (var m = 0; m < n; m++) {
      var node = nodes[m];
      node.vx += (cx - node.x) * this.p.centre * this.alpha;
      node.vy += (cy - node.y) * this.p.centre * this.alpha;
      node.vz += -node.z * this.p.centre * this.alpha;
      // Idle wander. Without this the layout converges and sits dead still,
      // which reads as a screenshot rather than a live system.
      var amp = this.p.drift * (1 + this.p.activity * 2.2);
      node.vx += Math.cos(this.t * 0.009 * node.wob + node.phase) * amp;
      node.vy += Math.sin(this.t * 0.011 * node.wob + node.phase * 1.6) * amp;
      node.vz += Math.cos(this.t * 0.010 * node.wob + node.phase * 0.7) * amp;
      node.vx *= this.p.damping; node.vy *= this.p.damping; node.vz *= this.p.damping;
      node.x += node.vx; node.y += node.vy; node.z += node.vz;
    }
    this.alpha = Math.max(this.p.alphaFloor, this.alpha * 0.994);
  };

  /* Rotate around the cloud's centre and apply perspective. Writes sx/sy (layout
     space, before pan/zoom), f (size factor) and fade (fog) onto every node. */
  MemoryGraph.prototype._project = function () {
    var W = this.cv.clientWidth, H = this.cv.clientHeight, cx = W / 2, cy = H / 2, D = this.p.depth;
    var cY = Math.cos(this.yaw), sY = Math.sin(this.yaw), cP = Math.cos(this.pitch), sP = Math.sin(this.pitch);
    for (var i = 0; i < this.nodes.length; i++) {
      var n = this.nodes[i], x = n.x - cx, y = n.y - cy, z = n.z;
      var x1 = x * cY - z * sY, z1 = x * sY + z * cY;
      var y2 = y * cP - z1 * sP, z2 = y * sP + z1 * cP;
      var f = D / Math.max(D * 0.25, D + z2);
      n.sx = cx + x1 * f; n.sy = cy + y2 * f; n.f = f;
      n.fade = Math.max(0.22, Math.min(1, 0.45 + (f - 0.8) * 1.6));
    }
  };

  /* ── render ─────────────────────────────── */
  MemoryGraph.prototype._loop = function () {
    this._resize();
    if (this._needsFit && this.cv.clientWidth >= 50 && this.cv.clientHeight >= 50) this.fit();
    this._tick();
    if (!this.grab && performance.now() - this.idleSince > 3500) {
      this.yaw += this.p.spin * (1 + this.p.activity * 2.5);
    }
    this._project();
    var ctx = this.ctx, W = this.cv.clientWidth, H = this.cv.clientHeight;
    ctx.clearRect(0, 0, W, H);
    this._starfield(ctx, W, H);
    ctx.save();
    ctx.translate(this.tx, this.ty);
    ctx.scale(this.scale, this.scale);

    var lit = this.focus ? this._neighbours(this.focus) : null;
    var traceSet = this.trace ? new Set(this.trace) : null;
    var base = this.dim ? 0.09 : 0.3;

    for (var i = 0; i < this.links.length; i++) {
      var l = this.links[i];
      if (!this.visible(l.s) || !this.visible(l.t)) continue;
      var onTrace = traceSet && traceSet.has(l.s.id) && traceSet.has(l.t.id)
        && Math.abs(this.trace.indexOf(l.s.id) - this.trace.indexOf(l.t.id)) === 1;
      var onFocus = lit && lit.has(l.s.id) && lit.has(l.t.id);
      var fog = (l.s.fade + l.t.fade) / 2;
      ctx.beginPath();
      ctx.moveTo(l.s.sx, l.s.sy);
      ctx.lineTo(l.t.sx, l.t.sy);
      if (onTrace) { ctx.strokeStyle = 'rgba(255,179,64,.95)'; ctx.lineWidth = 2.2; }
      else if (onFocus) { ctx.strokeStyle = 'rgba(120,235,255,.8)'; ctx.lineWidth = 1.5; }
      else if (lit) { ctx.strokeStyle = 'rgba(95,228,255,.05)'; ctx.lineWidth = 0.8; }
      else {
        // Tint each edge with its source node's colour so the web reads as a
        // structure rather than a uniform cyan mesh; far edges sink into the fog.
        ctx.strokeStyle = hexA(l.s.colour, base * fog * (0.72 + (i % 5) * 0.09));
        ctx.lineWidth = 0.6 + fog * 0.6;
      }
      ctx.stroke();
    }

    this._pulses(ctx, lit);

    // Painter's order: far nodes first, so near ones sit on top.
    var order = this.nodes.slice().sort(function (a, b) { return a.f - b.f; });
    for (var j = 0; j < order.length; j++) {
      var nd = order[j];
      if (!this.visible(nd)) continue;
      var isLit = !lit || lit.has(nd.id);
      var isTrace = traceSet && traceSet.has(nd.id);
      var alpha = (isTrace ? 1 : (isLit ? 1 : 0.16)) * (isTrace || this.focus === nd.id ? 1 : nd.fade);
      var r = nd.r * nd.f;
      var lv = this.live[nd.id], off = lv && lv.online === false;
      var hasLvl = lv && lv.pegel !== null && lv.pegel !== undefined;
      if (off) alpha *= 0.32;                    // device not in the network right now
      // Level stars stand out in traffic-light colours: green full, amber half, red low.
      var col = hasLvl ? (lv.pegel >= 0.5 ? '#39ff88' : lv.pegel >= 0.2 ? '#ffb340' : '#ff4d4d') : nd.colour;
      ctx.globalAlpha = alpha;
      if ((isTrace || (this.focus === nd.id)) || (nd.r > 9 && !lit)) {
        ctx.shadowColor = col; ctx.shadowBlur = (isTrace ? 22 : 14) * nd.f;
      } else { ctx.shadowBlur = 0; }
      if (hasLvl && !off) {                      // fuller battery, brighter star
        ctx.shadowColor = col;
        ctx.shadowBlur = (10 + 30 * lv.pegel) * nd.f * (1 + Math.sin(this.t * 0.04) * 0.15);
      }
      ctx.beginPath();
      ctx.arc(nd.sx, nd.sy, r, 0, Math.PI * 2);
      ctx.fillStyle = off ? '#5a6577' : col;
      ctx.fill();
      if (hasLvl) {                              // gauge ring: dim track, lit share = level
        ctx.shadowBlur = 0;
        ctx.lineWidth = 2 * nd.f;
        ctx.beginPath();
        ctx.arc(nd.sx, nd.sy, r + 4 * nd.f, 0, Math.PI * 2);
        ctx.strokeStyle = hexA(col, 0.18);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(nd.sx, nd.sy, r + 4 * nd.f, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * lv.pegel);
        ctx.strokeStyle = col;
        ctx.stroke();
      }
      if (this.focus === nd.id || isTrace) {
        ctx.shadowBlur = 0;
        ctx.beginPath();                         // the gauge may have replaced the node path
        ctx.arc(nd.sx, nd.sy, r, 0, Math.PI * 2);
        ctx.lineWidth = 2; ctx.strokeStyle = '#fff'; ctx.stroke();
        var breathe = 1 + Math.sin(this.t * 0.06) * 0.18;
        ctx.beginPath();
        ctx.arc(nd.sx, nd.sy, r * 1.9 * breathe, 0, Math.PI * 2);
        ctx.strokeStyle = hexA(nd.colour, 0.34);
        ctx.lineWidth = 1;
        ctx.stroke();
      }
      ctx.shadowBlur = 0;
      if (this.showLabels && (nd.r >= 11.5 || isTrace || this.focus === nd.id
          || (lit && lit.has(nd.id))) && this.scale > 0.3) {
        ctx.globalAlpha = alpha * 0.92;
        ctx.font = Math.round(9 + nd.f * 2) + 'px "SF Mono", ui-monospace, Menlo, monospace';
        ctx.fillStyle = isTrace ? '#ffd79a' : '#cfe3f2';
        ctx.textAlign = 'center';
        ctx.fillText(nd.title + (hasLvl ? ' · ' + Math.round(lv.pegel * 100) + ' %' : '') + (off ? ' · offline' : ''),
                     nd.sx, nd.sy - r - (hasLvl ? 10 : 6));
      }
      ctx.globalAlpha = 1;
    }
    ctx.restore();
    requestAnimationFrame(this._loop);
  };

  /* Distant stars on a sphere around the camera — they turn with the galaxy,
     which is what sells the depth when it rotates. */
  MemoryGraph.prototype._starfield = function (ctx, W, H) {
    var cY = Math.cos(this.yaw * 0.6), sY = Math.sin(this.yaw * 0.6);
    var cP = Math.cos(this.pitch * 0.6), sP = Math.sin(this.pitch * 0.6);
    var R = Math.max(W, H) * 0.9;
    for (var i = 0; i < this.stars.length; i++) {
      var s = this.stars[i];
      var x1 = s.x * cY - s.z * sY, z1 = s.x * sY + s.z * cY;
      var y2 = s.y * cP - z1 * sP, z2 = s.y * sP + z1 * cP;
      if (z2 < -0.1) continue;                     // behind the camera
      var tw = 0.75 + Math.sin((this.t || 0) * 0.03 + i) * 0.25;
      ctx.globalAlpha = s.b * tw * Math.min(1, z2 + 0.4);
      ctx.fillStyle = '#bfe9ff';
      ctx.fillRect(W / 2 + x1 * R, H / 2 + y2 * R, s.r, s.r);
    }
    ctx.globalAlpha = 1;
  };

  /* Traffic on the graph: small bright packets run along random edges. Rate
     scales with activity, so the whole cloud visibly wakes up while Claude is
     working and settles back to a slow trickle when idle. */
  MemoryGraph.prototype._pulses = function (ctx, lit) {
    var want = 5 + Math.round(this.p.activity * 16);
    if (this.links.length && this.pulses.length < want && Math.random() < 0.28) {
      var link = this.links[(Math.random() * this.links.length) | 0];
      if (this.visible(link.s) && this.visible(link.t)) {
        this.pulses.push({ l: link, t: 0, v: 0.006 + Math.random() * 0.012 });
      }
    }
    for (var i = this.pulses.length - 1; i >= 0; i--) {
      var pz = this.pulses[i];
      pz.t += pz.v * (1 + this.p.activity);
      if (pz.t >= 1 || !this.visible(pz.l.s) || !this.visible(pz.l.t)) {
        this.pulses.splice(i, 1);
        continue;
      }
      if (lit && !(lit.has(pz.l.s.id) && lit.has(pz.l.t.id))) continue;
      var x = pz.l.s.sx + (pz.l.t.sx - pz.l.s.sx) * pz.t;
      var y = pz.l.s.sy + (pz.l.t.sy - pz.l.s.sy) * pz.t;
      var f = pz.l.s.f + (pz.l.t.f - pz.l.s.f) * pz.t;
      var fade = Math.sin(pz.t * Math.PI);          // fade in and out at the ends
      ctx.beginPath();
      ctx.arc(x, y, 1.9 * f, 0, Math.PI * 2);
      ctx.fillStyle = hexA(pz.l.t.colour, 0.85 * fade);
      ctx.shadowColor = pz.l.t.colour;
      ctx.shadowBlur = 9 * fade;
      ctx.fill();
      ctx.shadowBlur = 0;
    }
  };

  /* 0 = idle, 1 = working. Drives drift amplitude, rotation and pulse traffic. */
  MemoryGraph.prototype.setActivity = function (level) {
    this.p.activity = Math.max(0, Math.min(1, level));
    if (level > 0) this.alpha = Math.max(this.alpha, 0.22);
  };

  MemoryGraph.prototype._resize = function () {
    var dpr = Math.min(global.devicePixelRatio || 1, 2);
    var w = this.cv.clientWidth, h = this.cv.clientHeight;
    if (this.cv.width !== w * dpr || this.cv.height !== h * dpr) {
      this.cv.width = w * dpr; this.cv.height = h * dpr;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
  };

  MemoryGraph.prototype.fit = function () {
    var vis = this.nodes.filter(this.visible, this);
    // A zero-size canvas (layout not settled yet) would clamp the scale to its
    // minimum and strand the cloud in a corner. Wait for real dimensions.
    if (!vis.length || this.cv.clientWidth < 50 || this.cv.clientHeight < 50) {
      this._needsFit = true;
      return;
    }
    this._needsFit = false;
    this._project();
    // Trim the extremes before measuring: two stray nodes on the rim must not
    // shrink the whole cloud to a dot in the middle of the canvas. The extra
    // margin leaves room for the cloud to turn without leaving the frame.
    var xs = vis.map(function (n) { return n.sx; }).sort(function (a, b) { return a - b; });
    var ys = vis.map(function (n) { return n.sy; }).sort(function (a, b) { return a - b; });
    var cut = Math.floor(xs.length * 0.03);
    var minX = xs[cut], maxX = xs[xs.length - 1 - cut];
    var minY = ys[cut], maxY = ys[ys.length - 1 - cut];
    var W = this.cv.clientWidth, H = this.cv.clientHeight, pad = 46;
    var s = Math.min((W - pad * 2) / Math.max(maxX - minX, 1),
                     (H - pad * 2) / Math.max(maxY - minY, 1)) * 0.92;
    this.scale = Math.max(0.25, Math.min(2.4, s));
    this.tx = W / 2 - (W / 2) * this.scale;
    this.ty = H / 2 - (H / 2) * this.scale;
  };

  MemoryGraph.prototype._at = function (ev) {
    var rect = this.cv.getBoundingClientRect();
    var x = (ev.clientX - rect.left - this.tx) / this.scale;
    var y = (ev.clientY - rect.top - this.ty) / this.scale;
    var best = null;
    for (var i = 0; i < this.nodes.length; i++) {
      var n = this.nodes[i];
      if (!this.visible(n) || n.sx === undefined) continue;
      var dx = n.sx - x, dy = n.sy - y, r = n.r * n.f + 5;
      if (dx * dx + dy * dy <= r * r && (!best || n.f > best.f)) best = n;   // nearest wins
    }
    return best;
  };

  /* Drag turns the galaxy, right-drag or shift-drag pans, wheel zooms,
     a click without movement selects (shift-click traces a path). */
  MemoryGraph.prototype._bind = function () {
    var self = this, cv = this.cv;

    cv.addEventListener('contextmenu', function (e) { e.preventDefault(); });

    cv.addEventListener('mousedown', function (e) {
      var hit = self._at(e);
      self.grab = { x: e.clientX, y: e.clientY, yaw: self.yaw, pitch: self.pitch,
                    tx: self.tx, ty: self.ty, node: hit, moved: false,
                    pan: e.button === 2 || (e.shiftKey && !hit) };
      cv.classList.add('dragging');
    });

    global.addEventListener('mousemove', function (e) {
      var g = self.grab;
      if (g) {
        var dx = e.clientX - g.x, dy = e.clientY - g.y;
        if (Math.abs(dx) + Math.abs(dy) > 4) g.moved = true;
        if (!g.moved) return;
        if (g.pan) { self.tx = g.tx + dx; self.ty = g.ty + dy; }
        else {
          self.yaw = g.yaw + dx * 0.006;
          self.pitch = Math.max(-1.35, Math.min(1.35, g.pitch + dy * 0.006));
        }
        self.idleSince = performance.now();
      } else {
        var hit = self._at(e);
        cv.style.cursor = hit ? 'pointer' : 'grab';
        if (self.opts.onHover && hit !== self.hover) { self.hover = hit; self.opts.onHover(hit); }
      }
    });

    global.addEventListener('mouseup', function (e) {
      var g = self.grab;
      self.grab = null; cv.classList.remove('dragging');
      if (!g || g.moved || e.target !== cv) return;
      if (g.node) self.setFocus(g.node.id, e.shiftKey);
      else self.setFocus(null);
    });

    cv.addEventListener('wheel', function (e) {
      e.preventDefault();
      var rect = cv.getBoundingClientRect();
      var mx = e.clientX - rect.left, my = e.clientY - rect.top;
      var factor = e.deltaY < 0 ? 1.12 : 0.89;
      var next = Math.max(0.2, Math.min(4, self.scale * factor));
      self.tx = mx - (mx - self.tx) * (next / self.scale);
      self.ty = my - (my - self.ty) * (next / self.scale);
      self.scale = next;
    }, { passive: false });
  };

  global.MemoryGraph = MemoryGraph;
})(window);
