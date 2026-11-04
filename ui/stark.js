/* Stark-HUD: the room display in an Iron Man skin — the same parts as tablet.js (mic, his words,
   cards, tiles, detail sheets), plus what a workshop wall shows: a day strip, a clock with
   seconds, the machine room as instruments, and an arc reactor as his avatar. tablet.js calls
   in here only when this layout is picked (body.stark); nothing here talks to the server. */
(function () {
  var q = function (s) { return document.querySelector(s); };
  var TAU = Math.PI * 2;
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };
  var num = function (v, d) { return v == null || isNaN(v) ? '—' : Number(v).toFixed(d || 0).replace('.', ','); };

  // cyan for everything calm, gold like a hologram while he hears you, red when something is wrong
  var COLOURS = { idle: '#2a9fd0', sleep: '#1f8fc0', listen: '#3fdcff', hear: '#ffb52e', think: '#8fe3ff',
                  speak: '#4fe6ff', alert: '#ff3b4e' };
  function rgba(hex, a) {
    var n = parseInt(hex.slice(1), 16);
    return 'rgba(' + (n >> 16 & 255) + ',' + (n >> 8 & 255) + ',' + (n & 255) + ',' + a + ')';
  }

  var LABELS = ['PWR', 'SYS', 'NET', 'CPU', 'MEM', 'COM', 'NAV', 'SEC'];

  /* The arc reactor. Rings turn like instruments, ten coils glow with his voice, the
     triangle core burns brighter while he speaks; thinking runs a light around the coils,
     hearing closes four gold brackets in on the core. */
  function reactor(ctx, W, H, t, c, mode, level) {
    ctx.clearRect(0, 0, W, H);
    var cx = W / 2, cy = H / 2, S = Math.min(W, H) / 2 * 0.97, i, a, r0, r1;
    var calm = mode === 'sleep' || mode === 'idle';
    var spin = calm ? 0.3 : mode === 'think' ? 2.4 : 1;
    var flick = mode === 'alert' ? 0.6 + 0.4 * Math.abs(Math.sin(t * 9)) : 1;
    var stroke = function (w, col) { ctx.lineWidth = w; ctx.strokeStyle = col; };
    var ring = function (r, from, to) { ctx.beginPath(); ctx.arc(cx, cy, r, from, to); ctx.stroke(); };

    // glow behind everything
    var g = ctx.createRadialGradient(cx, cy, S * 0.1, cx, cy, S);
    g.addColorStop(0, rgba(c, (calm ? 0.28 : 0.42) * flick)); g.addColorStop(0.5, rgba(c, 0.08)); g.addColorStop(1, rgba(c, 0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, S, 0, TAU); ctx.fill();

    // outer scale
    var rot = t * 0.04 * spin;
    stroke(1, rgba(c, 0.45)); ring(S * 0.99, 0, TAU);
    for (i = 0; i < 90; i++) {
      a = rot + i / 90 * TAU;
      r1 = S * 0.97; r0 = r1 - (i % 15 === 0 ? S * 0.07 : i % 5 === 0 ? S * 0.045 : S * 0.022);
      stroke(i % 15 === 0 ? 2 : 1, rgba(c, i % 5 === 0 ? 0.8 : 0.35));
      ctx.beginPath(); ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0);
      ctx.lineTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1); ctx.stroke();
    }

    // dashed band turning the other way — lengths from a fixed pattern, so it reads as data
    var band = -t * 0.18 * spin, pat = [0.5, 0.12, 0.25, 0.08, 0.4, 0.2, 0.1, 0.3, 0.16, 0.45];
    stroke(S * 0.035, rgba(c, 0.5 + 0.3 * level));
    for (i = 0, a = band; i < pat.length; i++) { ring(S * 0.87, a, a + pat[i]); a += pat[i] + 0.09; }
    stroke(1, rgba(c, 0.35)); ring(S * 0.82, 0, TAU);

    // labels around the coils
    ctx.save();
    ctx.font = '600 ' + Math.max(7, Math.round(S * 0.055)) + 'px ui-monospace, Menlo, Consolas, monospace';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = rgba(c, 0.75);
    for (i = 0; i < LABELS.length; i++) {
      a = t * 0.02 * spin + i / LABELS.length * TAU;
      ctx.save(); ctx.translate(cx + Math.cos(a) * S * 0.77, cy + Math.sin(a) * S * 0.77); ctx.rotate(a + Math.PI / 2);
      ctx.fillText(LABELS[i], 0, 0); ctx.restore();
    }
    ctx.restore();

    // his voice: a meter just outside the coils
    if (level > 0.02 || mode === 'speak' || mode === 'hear') {
      for (i = 0; i < 80; i++) {
        a = i / 80 * TAU;
        var w = 0.5 + 0.5 * Math.sin(i * 1.9 + t * 10) * Math.cos(i * 0.7 - t * 6);
        r0 = S * 0.71; r1 = r0 + S * (0.01 + level * 0.06 * w);
        stroke(2, rgba(c, 0.4 + 0.6 * level));
        ctx.beginPath(); ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0);
        ctx.lineTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1); ctx.stroke();
      }
    }

    // ten coils
    var chase = mode === 'think' ? (t * 6) % 10 : -1;
    ctx.save();
    ctx.shadowColor = c; ctx.shadowBlur = S * (0.04 + 0.1 * level);
    for (i = 0; i < 10; i++) {
      var a0 = -Math.PI / 2 + i / 10 * TAU + 0.07, a1 = a0 + TAU / 10 - 0.14;
      var lit = chase >= 0 ? Math.max(0.25, 1 - Math.min(Math.abs(i - chase), 10 - Math.abs(i - chase)) / 3) : 1;
      var cg = ctx.createRadialGradient(cx, cy, S * 0.5, cx, cy, S * 0.68);
      cg.addColorStop(0, rgba(c, (0.35 + 0.45 * level) * lit * flick));
      cg.addColorStop(1, rgba(c, (0.75 + 0.25 * level) * lit * flick));
      ctx.fillStyle = cg;
      ctx.beginPath(); ctx.arc(cx, cy, S * 0.68, a0, a1); ctx.arc(cx, cy, S * 0.52, a1, a0, true); ctx.closePath(); ctx.fill();
    }
    ctx.restore();
    stroke(1.5, rgba(c, 0.8)); ring(S * 0.5, 0, TAU);

    // the core: white heat in the middle
    var breathe = 1 + Math.sin(t * (calm ? 0.9 : 2.2)) * 0.03;
    var cr = S * 0.46 * breathe;
    var k = ctx.createRadialGradient(cx, cy, 0, cx, cy, cr);
    k.addColorStop(0, rgba('#ffffff', (0.85 + 0.15 * level) * flick)); k.addColorStop(0.35, rgba(c, 0.75 * flick));
    k.addColorStop(0.8, rgba(c, 0.18)); k.addColorStop(1, rgba(c, 0));
    ctx.fillStyle = k; ctx.beginPath(); ctx.arc(cx, cy, cr, 0, TAU); ctx.fill();

    // the triangle (Mark II), point down
    ctx.save();
    ctx.shadowColor = c; ctx.shadowBlur = S * (0.08 + 0.12 * level);
    [[0.36, 3.2, 0.95], [0.25, 1.6, 0.7]].forEach(function (tr) {
      stroke(tr[1], rgba('#ffffff', tr[2] * flick));
      ctx.beginPath();
      for (var j = 0; j < 3; j++) {
        var ta = Math.PI / 2 + j * TAU / 3;
        var x = cx + Math.cos(ta) * S * tr[0], y = cy + Math.sin(ta) * S * tr[0];
        if (j) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      }
      ctx.closePath(); ctx.stroke();
    });
    ctx.restore();

    // hearing: gold brackets close in on the core
    if (mode === 'hear' || mode === 'think') {
      var br = S * (0.95 - 0.08 * Math.abs(Math.sin(t * 3)));
      stroke(3, rgba(c, 0.9));
      for (i = 0; i < 4; i++) { a = Math.PI / 4 + i * TAU / 4 + t * 0.5; ring(br, a - 0.22, a + 0.22); }
    }
  }

  // ── the day strip and the clock's seconds ──
  var stripDay = '';
  function tick() {
    var now = new Date(), key = now.toDateString(), tm = q('#tTime');
    if (tm) tm.dataset.sec = ':' + String(now.getSeconds()).padStart(2, '0');
    if (key === stripDay) return;
    stripDay = key;
    var n = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate(), out = [];
    for (var i = 1; i <= n; i++) {
      var we = [0, 6].indexOf(new Date(now.getFullYear(), now.getMonth(), i).getDay()) >= 0;
      out.push('<li class="' + (i === now.getDate() ? 'now' : i < now.getDate() ? 'gone' : '') + (we ? ' we' : '') + '">'
        + String(i).padStart(2, '0') + '</li>');
    }
    var st = q('#sStrip');
    if (st) st.innerHTML = out.join('');
  }

  // ── the machine room as instruments ──
  function gauge(v, label, sub, warn) {
    var p = v == null || isNaN(v) ? 0 : Math.max(0, Math.min(100, v)), C = 2 * Math.PI * 26;
    var cls = v == null ? 'dim' : p >= (warn || 85) ? 'hot' : '';
    return '<div class="sgauge ' + cls + '"><svg viewBox="0 0 64 64"><circle cx="32" cy="32" r="26" class="trk"/>'
      + '<circle cx="32" cy="32" r="26" class="val" stroke-dasharray="' + (C * p / 100).toFixed(1) + ' ' + C.toFixed(1) + '"/>'
      + '<circle cx="32" cy="32" r="20" class="in"/></svg><b>' + (v == null ? '—' : num(v)) + '<small>%</small></b>'
      + '<span>' + label + '</span>' + (sub ? '<em>' + sub + '</em>' : '') + '</div>';
  }
  var line = function (k, v, cls) { return '<li class="' + (cls || '') + '"><span>' + k + '</span><b>' + v + '</b></li>'; };
  var panel = function (title, body) { return '<section class="spanel"><h4>' + title + '</h4>' + body + '</section>'; };

  function render(d) {
    var box = q('#sSys');
    if (!box) return;
    var pc = d.pc || {}, hl = d.homelab || {}, h = d.house || {}, n = d.nas || {}, pi = d.pi || {}, out = [];
    if (!pc.error) {
      out.push(panel('System · PC', '<div class="sgauges">' + gauge(pc.pc_cpu, 'CPU') + gauge(pc.pc_ram, 'RAM')
        + gauge(pc.pc_vram, 'VRAM', pc.pc_gpu_temp != null ? num(pc.pc_gpu_temp) + ' °C' : '') + gauge(pc.pc_disk, 'DISK', '', 90) + '</div>'));
    }
    if (!hl.error) {
      out.push(panel('Network', '<ul class="slines">'
        + line('Download', hl.link === 0 ? 'offline' : num(hl.down, 1) + ' Mbit/s', hl.link === 0 ? 'bad' : '')
        + line('Upload', num(hl.up, 1) + ' Mbit/s')
        + (hl.ph_pct != null ? line('Pi-hole', num(hl.ph_pct, 1) + ' % blocked', hl.ph_on === false ? 'bad' : '') : '')
        + (hl.pi_temp != null ? line('Pi', num(hl.pi_temp, 1) + ' °C', hl.pi_temp > 70 ? 'warn' : '') : '') + '</ul>'));
    }
    if (!h.error && h.solar_w != null) {
      out.push(panel('Energy', '<div class="sgauges two">' + gauge(h.soc, 'Battery', h.charge_w > 5 ? '+' + num(h.charge_w) + ' W'
        : h.out_w > 5 ? '−' + num(h.out_w) + ' W' : 'idle', 101) + '</div><ul class="slines">'
        + line('Solar', num(h.solar_w) + ' W', h.solar_w > 50 ? 'good' : '') + line('Today', num(h.gen_kwh, 1) + ' kWh') + '</ul>'));
    }
    var st = [];
    if (n.model || n.error) st.push(line('NAS', n.error ? 'not reachable' : n.ok ? 'fine · ' + num(n.used_pct) + ' %' : 'check', n.error || !n.ok ? 'bad' : 'good'));
    if (pi.temp != null || pi.error) st.push(line('Container', pi.error ? 'Pi offline' : pi.up + ' / ' + pi.containers, pi.error || pi.up < pi.containers ? 'bad' : 'good'));
    if (h.router && !h.error) st.push(line('Internet', h.router.online ? 'online' : 'offline', h.router.online ? 'good' : 'bad'));
    if (st.length) out.push(panel('Status', '<ul class="slines">' + st.join('') + '</ul>'));
    box.innerHTML = out.join('');
  }

  window.Stark = {
    colour: function (mode) { return COLOURS[mode] || COLOURS.idle; },
    reactor: reactor, tick: tick, render: render
  };
})();
