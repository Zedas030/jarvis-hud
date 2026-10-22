/* JARVIS · Claude Code memory HUD — front end.
   Talks to the local Python server over NDJSON. The brain is your `claude` CLI,
   so every token here is billed to your subscription, not an API key. */
'use strict';

var $ = function (s) { return document.querySelector(s); };
var TOKEN = document.querySelector('meta[name=jarvis-token]').content;
var headers = function (extra) {
  return Object.assign({ 'X-Jarvis-Token': TOKEN }, extra || {});
};
var esc = function (s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
};
var now = function () { return new Date().toTimeString().slice(0, 8); };

var graph = null, hiddenTypes = new Set(), running = false, activeCtl = null, streamSpeak = false;
var answerText = '', answerBubble = null, GRAPH = { nodes: [], links: [], hubs: [], counts: [] };

/* ── action log ───────────────────────────── */
function log(kind, label, msg) {
  var e = document.createElement('div');
  e.className = 'entry k-' + kind;
  e.innerHTML = '<div class="top"><span class="ts">' + now() + '</span>'
    + '<span class="kind">' + esc(label) + '</span></div>'
    + (msg ? '<div class="msg">' + esc(msg) + '</div>' : '');
  var box = $('#log');
  box.insertBefore(e, box.firstChild);
  while (box.children.length > 45) box.removeChild(box.lastChild);
}

/* Fish Audio reads [square brackets] as stage directions, never as words. We
   send the text through untouched so the model performs them, but on screen we
   render each tag as a chip so you can see the direction and hear the result.
   The browser speechSynthesis fallback has no such notion, so it gets a
   stripped copy — otherwise it literally reads "dry" out loud. */
var TONE_RE = /\[([^\][]{1,90})\]/g;

function stripTones(text) {
  return String(text || '').replace(TONE_RE, ' ').replace(/\s{2,}/g, ' ').trim();
}

function lastTone(text) {
  var found = null, m;
  TONE_RE.lastIndex = 0;
  while ((m = TONE_RE.exec(text)) !== null) found = m[1];
  return found;
}

function renderSpoken(el, text) {
  var html = '', last = 0, m;
  TONE_RE.lastIndex = 0;
  while ((m = TONE_RE.exec(text)) !== null) {
    html += esc(text.slice(last, m.index));
    html += '<span class="tone">' + esc(m[1]) + '</span>';
    last = m.index + m[0].length;
  }
  html += esc(text.slice(last));
  el.innerHTML = html;
}

function bubble(who, text) {
  var b = document.createElement('div');
  b.className = 'bubble ' + who;
  if (who === 'jarvis') renderSpoken(b, text); else b.textContent = text;
  document.body.classList.add('hasconvo');
  $('#transcript').appendChild(b);
  var turns = $('#transcript').querySelectorAll('.bubble.me').length;
  $('#convoCount').textContent = turns + (turns === 1 ? ' turn' : ' turns');
  $('#transcript').scrollTop = $('#transcript').scrollHeight;
  return b;
}

function setVoiceState(state, cls) {
  $('#voiceState').textContent = state;
  if (window.JarvisOrb) JarvisOrb.setState(state);
  $('#voiceDot').className = 'dot ' + (cls || '');
  updateMicButton();
}

/* The Live button says in plain words what the mic is doing right now. */
var micProblem = null;   // 'blocked' | 'needs-click' | null
function updateMicButton() {
  var btn = $('#btnMic'), label = $('#micLabel');
  if (!btn || !label || typeof Live === 'undefined') return;
  var st = 'off', text = 'Live starten';
  if (micProblem === 'blocked') { st = 'blocked'; text = 'Mikrofon blockiert'; }
  else if (Live.ptt) { st = 'hearing'; text = 'Loslassen = senden'; }
  else if (!Live.on) { st = 'off'; text = 'Live starten'; }
  else if (micProblem === 'needs-click' || (Live.ctx && Live.ctx.state === 'suspended')) {
    st = 'needs-click'; text = 'Klick = Mikro an';
  }
  else if (Live.armed) { st = 'hearing'; text = 'Höre dich …'; }
  else if (Live.sending) { st = 'busy'; text = 'Verstehe …'; }
  else if (Live.speaking) { st = 'speaking'; text = 'Spricht · reinreden'; }
  else if (running) { st = 'busy'; text = briefingActive ? 'Briefing · reden bricht ab' : 'Denkt …'; }
  else if (Ringing.on) { st = 'speaking'; text = 'Klingelt · reden = aus'; }
  else if (!Wake.awake()) { st = 'sleeping'; text = 'Sag „Jarvis“'; }
  else { st = 'listening'; text = Wake.mode() === 'off' ? 'Hört zu' : 'Ich höre …'; }
  btn.dataset.state = st;
  btn.classList.toggle('live', Live.on && st !== 'blocked');
  if (label.textContent !== text) label.textContent = text;
}

/* ── panels ───────────────────────────────── */
/* ── live widgets around the graph ───────────
   Haus, Gesundheit, Systeme and the top bar, fed by /api/dashboard. Each source
   is cached server-side; a missing one just shows a dash. */
var RING_C = 2 * Math.PI * 27;
function gauge(el, frac, colour, value, unit, label) {
  frac = Math.max(0, Math.min(1, frac || 0));
  el.innerHTML = '<svg viewBox="0 0 64 64"><circle cx="32" cy="32" r="27" class="gbase"/>'
    + '<circle cx="32" cy="32" r="27" class="gval" style="stroke:' + colour
    + ';stroke-dasharray:' + RING_C.toFixed(1) + ';stroke-dashoffset:' + (RING_C * (1 - frac)).toFixed(1)
    + ';filter:drop-shadow(0 0 4px ' + colour + ')"/></svg>'
    + '<div class="gtext"><b>' + esc(value) + '<small>' + esc(unit) + '</small></b></div>'
    + '<span class="glabel">' + esc(label) + '</span>';
}
function de(n, digits) {
  return n == null ? '—' : Number(n).toLocaleString('de-DE', { maximumFractionDigits: digits || 0 });
}
function tone(v, okAt, warnAt, higherIsBetter) {
  if (v == null) return 'var(--ink-dim)';
  var good = higherIsBetter ? v >= okAt : v <= okAt, mid = higherIsBetter ? v >= warnAt : v <= warnAt;
  return good ? 'var(--green)' : mid ? 'var(--amber)' : 'var(--pink)';
}

function renderHouse(h) {
  if (!h || h.error) { $('#houseLine').textContent = h ? 'Home Assistant: ' + h.error : 'lädt …'; return; }
  gauge($('#gSolar'), h.solar_w / 2000, 'var(--amber)', de(h.solar_w), 'W', 'Solar');
  gauge($('#gSoc'), (h.soc || 0) / 100, tone(h.soc, 50, 20, true), de(h.soc), '%', 'Speicher');
  $('#houseLine').innerHTML = '<span>heute <b>' + de(h.gen_kwh, 1) + ' kWh</b></span>'
    + '<span>Batterien <b>' + h.bats.map(function (b) { return de(b); }).join(' / ') + ' %</b></span>';
  $('#rooms').innerHTML = h.rooms.map(function (r) {
    return '<li><span class="nm">' + esc(r.name) + '</span>'
      + (r.heating ? '<i class="flame" title="heizt auf ' + de(r.target, 1) + ' °C">▲</i>' : '')
      + '<b>' + de(r.temp, 1) + ' °C</b></li>';
  }).join('');
  var pm = h.pm25, col = tone(pm, 12, 35, false);
  $('#airFill').style.width = Math.min(100, (pm || 0) / 75 * 100) + '%';
  $('#airFill').style.background = col;
  $('#airVal').innerHTML = pm == null ? '—' : de(pm) + ' <small>µg</small>';
  $('#airVal').style.color = col;
}

function renderMe(m) {
  if (!m || m.error) {
    $('#meTiles').innerHTML = '<p class="hint">' + esc(m ? 'Garmin: ' + m.error : 'Uhr wird gelesen …') + '</p>';
    return;
  }
  gauge($('#gSleep'), (m.sleep_score || 0) / 100, tone(m.sleep_score, 75, 60, true),
    m.sleep_score == null ? '—' : m.sleep_score, '', 'Schlaf');
  gauge($('#gBattery'), (m.battery || 0) / 100, tone(m.battery, 50, 25, true),
    m.battery == null ? '—' : m.battery, '', 'Body Battery');
  var tile = function (k, v, u) { return '<div><span>' + k + '</span><b>' + v + '<small>' + u + '</small></b></div>'; };
  $('#meTiles').innerHTML = tile('SCHLAF', de(m.sleep_h, 1), ' h') + tile('SCHRITTE', de(m.steps), '')
    + tile('RUHEPULS', de(m.rhr), ' bpm') + tile('STRESS', de(m.stress), '');
  $('#meAge').textContent = 'garmin · ' + m.at;
}

/* Only sender and subject — the bodies stay in the mailbox. */
function renderMail(m) {
  if (!m || m.error) {
    $('#mailUnread').textContent = '—';
    $('#mailList').innerHTML = '<p class="hint">' + esc(m ? 'Postfach: ' + m.error : 'Postfach wird gelesen …') + '</p>';
    return;
  }
  $('#mailUnread').textContent = m.unread;
  $('#mailUnread').className = m.unread ? '' : 'zero';
  $('#mailList').innerHTML = m.latest.map(function (x) {
    return '<li class="ok"><i class="dot"></i><span class="nm">' + esc(x.sender)
      + ' <small>' + esc(x.when) + '</small></span><em>' + esc(x.subject) + '</em></li>';
  }).join('') || '<p class="hint">Alles gelesen.</p>';
  $('#mailAge').textContent = 'Mail · ' + m.at;
}

function renderSystems(d) {
  var rows = [], h = d.house, p = d.pi, n = d.nas;
  var row = function (state, name, detail) { rows.push([state, name, detail]); };
  if (p) row(p.error ? 'bad' : (p.up < p.containers || p.temp > 70 ? 'warn' : 'ok'), 'Raspberry Pi',
    p.error ? p.error : de(p.temp, 1) + ' °C · ' + p.up + '/' + p.containers + ' Container');
  if (n) row(n.error ? 'bad' : (!n.ok || n.used_pct > 85 ? 'warn' : 'ok'), 'NAS',
    n.error ? n.error : de(n.used_pct) + ' % belegt · ' + n.disks + ' Platten ' + (n.ok ? 'gesund' : 'prüfen'));
  if (h && !h.error) row(h.router.online ? 'ok' : 'bad', 'Router',
    h.router.online ? '↓ ' + de(h.router.down * 8 / 1000, 1) + ' · ↑ ' + de(h.router.up * 8 / 1000, 1) + ' Mbit/s' : 'offline');
  if (h) row(h.error ? 'bad' : 'ok', 'Home Assistant', h.error ? 'nicht erreichbar' : 'verbunden');
  var c = d.pc;
  if (c && !c.error) row((c.pc_gpu_temp > 85 || c.pc_disk > 92) ? 'warn' : 'ok', 'Dieser PC',
    'CPU ' + de(c.pc_cpu) + ' % · RAM ' + de(c.pc_ram) + ' %' + (c.pc_vram != null ? ' · VRAM ' + de(c.pc_vram) + ' %' : '')
    + (c.pc_gpu_temp != null ? ' · GPU ' + de(c.pc_gpu_temp) + ' °C' : ''));
  $('#sysList').innerHTML = rows.map(function (r) {
    return '<li class="' + r[0] + '"><i class="dot"></i><span class="nm">' + esc(r[1]) + '</span><em>' + esc(r[2]) + '</em></li>';
  }).join('') || '<p class="hint">lädt …</p>';
  var bad = rows.filter(function (r) { return r[0] !== 'ok'; }).length;
  $('#sysAge').textContent = bad ? bad + ' Hinweis' + (bad > 1 ? 'e' : '') : 'alles grün';
  $('#sysAge').className = bad ? 'warn' : '';
}

function renderTop(d) {
  var w = d.weather, e = d.event;
  if (w && !w.error) {
    $('#wNow').textContent = de(w.now) + '° · ' + w.sky;
    $('#wSub').textContent = de(w.tmin) + '–' + de(w.tmax) + '° · Regen ' + w.rain + ' % · Wind ' + w.wind;
    $('#gVerdict').textContent = w.golf;
    $('#chipGolf em').textContent = 'Golf ' + (w.golf_day || 'heute');
    $('#chipGolf').className = 'chip-lg ' + w.golf_cls;
  }
  if (e && !e.error) {
    $('#eTitle').textContent = e.title || 'frei';
    $('#eWhen').textContent = e.when;
  }
}

async function loadDashboard() {
  try {
    var res = await fetch('/api/dashboard', { headers: headers() });
    var d = await res.json();
    renderTop(d); renderHouse(d.house); renderMe(d.me); renderMail(d.mail); renderSystems(d);
  } catch (e) { /* server restarting — next tick */ }
}

function tickClock() {
  var now = new Date();
  $('#clockTime').textContent = now.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
  $('#clockDate').textContent = now.toLocaleDateString('de-DE', { weekday: 'long', day: 'numeric', month: 'long' });
}

function renderInspector(node, trace) {
  var box = $('#inspector');
  if (!node) {
    box.innerHTML = '<p class="hint">Click a node to focus it — only that node and its '
      + 'connections light up, and you can read its note here. Shift-click a second node '
      + 'to trace the path.</p>';
    return;
  }
  var links = (graph.adj.get(node.id) || []).slice(0, 12);
  var html = '<div class="ititle">' + esc(node.title) + '</div>'
    + '<div class="imeta">' + esc(node.type) + ' · ' + node.degree + ' links'
    + (node.updated ? ' · ' + esc(node.updated) : '') + '</div>'
    + '<div class="ibody">' + esc(node.snippet || 'No body text.') + '</div>';
  if (trace && trace.length > 1) {
    html += '<div class="imeta" style="margin-top:10px">path · ' + trace.length + ' hops</div>'
      + '<div class="ibody">' + trace.map(function (id) {
        var n = graph.byId.get(id); return esc(n ? n.title : id);
      }).join(' → ') + '</div>';
  }
  if (links.length) {
    html += '<div class="ilinks">' + links.map(function (id) {
      var n = graph.byId.get(id);
      return '<span class="chip" data-id="' + esc(id) + '">' + esc(n ? n.title : id) + '</span>';
    }).join('') + '</div>';
  }
  box.innerHTML = html;
  Array.prototype.forEach.call(box.querySelectorAll('.chip'), function (c) {
    c.onclick = function () { focusNode(c.dataset.id); };
  });
}

function focusNode(id) {
  if (graph.setFocus(id, false)) {
    log('mem', 'MEMORY', 'focused ' + id);
  }
}

/* ── data ─────────────────────────────────── */
async function loadGraph(force) {
  var res = await fetch('/api/graph' + (force ? '?force=1' : ''), { headers: headers() });
  GRAPH = await res.json();
  graph.setData(GRAPH);
  $('#stageCount').textContent = GRAPH.total + ' notes · ' + GRAPH.links.length + ' links';
  setTimeout(function () { graph.fit(); }, 350);
}

/* Live state from Home Assistant: offline devices fade, levels glow. */
async function loadLive() {
  try {
    graph.setLive(await (await fetch('/api/live', { headers: headers() })).json());
  } catch (e) { /* HA or server unreachable: stars keep their last state */ }
}

/* Each display step is guarded on its own: one missing element (the layout
   changes) must never stop the rest — that once left __serverSTT unset and
   the Live button silently refusing to start. */
function setText(sel, text) { var el = $(sel); if (el) el.textContent = text; }
function safely(fn) { try { fn(); } catch (e) { console.warn('[jarvis] status display:', e); } }

async function loadStatus() {
  var s;
  try {
    var res = await fetch('/api/status', { headers: headers() });
    if (res.status === 401) { location.href = '/pair.html'; return; }   // this device was removed
    s = await res.json();
  } catch (e) { setText('#pGateway', 'offline'); return; }
  window.__remote = !!s.remote;
  document.body.classList.toggle('remote', !!s.remote);
  // The server restarted after a code change: reload for the new code and token,
  // but never mid-conversation — otherwise try again on the next poll.
  if (window.__boot && s.boot && s.boot !== window.__boot) {
    if (!running && !Live.speaking && !Live.sending && !Live.armed && !Speech.active && !Ringing.on) location.reload();
    return;
  }
  window.__boot = s.boot;
  // Capabilities first — the mic and voice depend on these.
  window.__serverVoice = s.server_voice;
  window.__serverSTT = s.server_stt;
  safely(function () { setText('#pGateway', s.remote ? 'remote' : 'online :' + location.port); });
  safely(function () { setText('#pBrain', s.runtime === 'claude' ? (s.version || 'Claude CLI') : 'MOCK'); });
  safely(function () { setText('#pVoice', s.voice_name || (s.server_voice ? 'Fish Audio' : 'browser')); });
  safely(function () {
    var dot = $('#pVoice') && $('#pVoice').parentNode.querySelector('.dot');
    if (dot) dot.className = 'dot ' + (s.server_voice ? 'ok' : 'warn');
  });
  safely(function () { setText('#sModel', s.model); });
  safely(function () { setText('#cliBadge', s.runtime === 'claude' ? 'CLAUDE CLI' : 'CLI OFFLINE'); });
  safely(function () { if (s.usage) renderUsage(s.usage); });
  safely(function () { if (s.billing) renderBilling(s.billing); });
  safely(function () { if (s.tts) renderTts(s.tts); });
  safely(function () { if (s.listener) window.__listener = s.listener; });
  safely(function () { if (s.wake) Wake.configure(s.wake); });
  safely(function () {
    window.__quiet = !!s.quiet;
    if (s.persona) { window.__persona = s.persona; if (!running) $('#toneNow').textContent = s.persona; }
    if (typeof s.night_volume === 'number') window.__nightVolume = s.night_volume;
  });
  safely(function () { if (s.timers) Timers.set(s.timers); });
  safely(function () {
    var f = s.fallback, el = $('#pBrain');
    if (el && f && f.mode !== 'off') el.title = 'Ersatz bei Claude-Ausfall: ' + (f.chain || []).map(function (r) {
      return r.name + ' ' + r.model + (r.ok ? ' (bereit)' : ' — ' + r.why);
    }).join(' → ');
  });
  updateMicButton();
}

/* ── usage panel ───────────────────────────────
   Claude Code reports the subscription limits with every answer (5-hour and
   weekly window, extra-usage credit). The API credit balance itself has no
   public endpoint, so that one is a link to the Console. */
function fmtReset(ts) {
  if (!ts) return '';
  var d = new Date(ts * 1000), now = new Date();
  var hm = d.toTimeString().slice(0, 5);
  if (d.toDateString() === now.toDateString()) return '↻ ' + hm;
  return '↻ ' + ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'][d.getDay()] + ' ' + hm;
}

function setBar(id, win) {
  var bar = $('#' + id), val = $('#' + id + 'v'), rst = $('#' + id + 'r');
  if (!bar) return;
  if (!win || typeof win.utilization !== 'number') { val.textContent = '—'; return; }
  var pct = Math.round(win.utilization * 100);
  bar.style.width = Math.min(100, pct) + '%';
  bar.className = pct >= 90 ? 'crit' : (pct >= 70 ? 'warn' : '');
  val.textContent = pct + '%';
  rst.textContent = fmtReset(win.resetsAt);
}

function renderUsage(u) {
  if (!u || !$('#u5')) return;
  setBar('u5', u.five_hour);
  setBar('u7', u.seven_day);
  var extra = $('#uExtra');
  if (u.using_overage) { extra.textContent = 'wird genutzt'; extra.className = ''; }
  else if (u.overage_reason === 'out_of_credits') { extra.textContent = 'leer'; extra.className = 'empty'; }
  else if (u.overage === 'allowed') { extra.textContent = 'verfügbar'; extra.className = ''; }
  else { extra.textContent = u.overage_reason ? u.overage_reason.replace(/_/g, ' ') : 'aus'; extra.className = ''; }
  var mins = Math.round((Date.now() / 1000 - (u.at || 0)) / 60);
  $('#usageAge').textContent = mins < 1 ? 'gerade' : 'vor ' + mins + ' min';
}

/* Voice provider chain: who speaks now, what is left, who is paused until when. */
function renderTts(t) {
  var el = $('#uTts'), sub = $('#uTtsSub'), row = $('#uTtsRow');
  if (!el || !t) return;
  var active = (t.chain || []).filter(function (c) { return c.name === t.active; })[0];
  el.textContent = (t.active_label || '—') + (active && active.balance ? ' · ' + active.balance : '');
  var paused = (t.chain || []).filter(function (c) { return c.blocked_until; }).map(function (c) {
    var d = new Date(c.blocked_until * 1000);
    return c.label + ' leer bis ' + d.getDate() + '.' + (d.getMonth() + 1) + '.';
  });
  sub.textContent = paused.join(' · ');
  row.title = 'Stimmen-Kette:\n' + (t.chain || []).map(function (c, i) {
    return (i + 1) + '. ' + c.label + ': ' + (!c.configured ? 'kein Key' : c.blocked_until ? 'gesperrt (' + c.reason + ')'
      : 'bereit') + (c.balance ? ' — ' + c.balance : '');
  }).join('\n');
}

function usd(v) { return '$' + Number(v).toFixed(2); }

function renderBilling(b) {
  var el = $('#uBal'), sub = $('#uBalSub');
  if (!el || !b) return;
  el.className = '';
  if (!b.balance_set) {
    el.textContent = 'eintragen ✎';
    sub.textContent = b.configured ? '' : 'Für automatische Abzüge JARVIS_COST_KEY in .env setzen';
  } else if (typeof b.remaining === 'number') {
    el.textContent = usd(b.remaining);
    if (b.remaining < 2) el.className = 'crit';
    else if (b.remaining < 5) el.className = 'warn';
    var parts = [];
    if (typeof b.today === 'number') parts.push('heute ' + usd(b.today));
    if (typeof b.month === 'number') parts.push('Monat ' + usd(b.month));
    if (b.note) parts.push(b.note);
    sub.textContent = parts.join(' · ');
  } else {
    el.textContent = usd(b.entered) + '?';
    sub.textContent = b.error || '';
  }
  if (b.error && b.balance_set && typeof b.remaining !== 'number') sub.textContent = b.error;
}

(function initBalance() {
  var btn = $('#uBalEdit'), form = $('#uBalForm'), input = $('#uBalInput');
  if (!btn) return;
  var open = function () { form.hidden = !form.hidden; if (!form.hidden) input.focus(); };
  btn.onclick = open;
  $('#uBal').onclick = open;
  form.onsubmit = async function (e) {
    e.preventDefault();
    var res = await fetch('/api/balance', {
      method: 'POST', headers: headers({ 'content-type': 'application/json' }),
      body: JSON.stringify({ usd: input.value })
    });
    var data = await res.json();
    if (!res.ok) { log('error', 'GUTHABEN', data.error || 'Fehler'); return; }
    input.value = ''; form.hidden = true;
    renderBilling(data);
    log('ok', 'GUTHABEN', 'Kontostand gesetzt: ' + usd(data.entered));
  };
})();

/* ── the run ──────────────────────────────── */
// A typed question may get a long answer: only its first paragraph is read aloud.
var typedRun = false;
function speakable(text) {
  if (!typedRun) return text;
  var cut = text.search(/\n\s*\n/);
  return cut >= 0 ? text.slice(0, cut) : text;
}

async function transmit(message, opts) {
  message = (message || '').trim();
  if (!message) return;
  if (running) { log('note', 'BUSY', 'still working — "' + message.slice(0, 36) + '" not sent'); return; }
  opts = opts || {};
  running = true;
  interruptSpeech();   // a new question ends whatever he was still saying
  speechReset();
  streamSpeak = opts.speak !== false;
  answerText = '';
  document.body.classList.remove('boot');
  document.body.classList.add('running');
  if (graph) graph.setActivity(1);
  $('#toneNow').textContent = window.__persona || '—';
  typedRun = !!opts.typed;
  setVoiceState('THINKING', 'hot');
  bubble('me', message);
  answerBubble = bubble('jarvis', '');
  answerBubble.classList.add('thinking');
  log('run', 'RUN', message.slice(0, 70));

  var t0 = performance.now();
  try {
    activeCtl = new AbortController();
    var res = await fetch('/api/run', {
      method: 'POST',
      headers: headers({ 'content-type': 'application/json' }),
      body: JSON.stringify({ message: message, fresh: /^\/new\b/.test(message), typed: !!opts.typed }),
      signal: activeCtl.signal
    });
    if (!res.ok) throw new Error('backend HTTP ' + res.status);
    var reader = res.body.getReader(), dec = new TextDecoder(), buf = '';
    while (true) {
      var chunk = await reader.read();
      if (chunk.done) break;
      buf += dec.decode(chunk.value, { stream: true });
      var nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        var line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (line) handleEvent(JSON.parse(line));
      }
    }
  } catch (e) {
    if (e && e.name === 'AbortError') { log('note', 'CANCEL', 'run cancelled'); opts.speak = false; }
    else { log('error', 'ERROR', String(e).slice(0, 160)); answerBubble.textContent = String(e).slice(0, 160); }
  }
  activeCtl = null;
  running = false;
  document.body.classList.remove('running');
  if (graph) graph.setActivity(0);
  answerBubble.classList.remove('thinking');
  $('#sLatency').textContent = Math.round(performance.now() - t0) + 'ms';
  setVoiceState('IDLE', '');
  if (answerText.trim() && opts.speak !== false) {
    feedSpeech(speakable(answerText), true);   // whatever is left after the last full sentence
    await speechDrained();
  }
  streamSpeak = false;
}

/* Stop talking and drop the rest of the answer (Escape, or talking over him).
   running is cleared right away so live mode can record the interruption. */
function cancelRun() {
  interruptSpeech();
  streamSpeak = false;
  if (activeCtl) activeCtl.abort();
  running = false;
  fetch('/api/cancel', { method: 'POST', headers: headers({ 'content-type': 'application/json' }), body: '{}' });
}

function handleEvent(ev) {
  switch (ev.t) {
    case 'status':
      if (ev.session_id) $('#sSession').textContent = ev.session_id.slice(0, 8);
      if (ev.model) $('#sModel').textContent = ev.model;
      log('ok', 'STATUS', 'claude online · tools=' + (ev.tools || 0)
        + ' · permission=' + (ev.permission || '—'));
      break;
    case 'latency':
      $('#sLatency').textContent = ev.ms + 'ms';
      log('note', 'LATENCY', 'first token after ' + ev.ms + 'ms');
      break;
    case 'tool':
      if (ev.phase === 'use') log('tool', 'TOOL', '→ ' + ev.name + '(' + (ev.input || '') + ')');
      else log('tool', 'TOOL', '✓ ' + (ev.ok === false ? 'error' : 'ok'));
      break;
    case 'focus':
      if (graph.setFocus(ev.id, false)) log('mem', 'MEMORY', 'vault hit → ' + ev.id);
      break;
    case 'graph':
      loadGraph(true);
      break;
    case 'delta':
      answerText += ev.text;
      if (streamSpeak) feedSpeech(speakable(answerText), false);
      renderSpoken(answerBubble, answerText);
      var tone = lastTone(answerText);
      if (tone) $('#toneNow').textContent = tone;
      $('#transcript').scrollTop = $('#transcript').scrollHeight;
      break;
    case 'route':
      $('#routeNow').textContent = ev.model + (ev.groups && ev.groups.length ? ' · ' + ev.groups.join(', ') : '')
        + (ev.reason ? ' · ' + ev.reason : '');
      break;
    case 'ratelimit':
      $('#sLimit').textContent = (ev.status || '—') + (ev.window ? ' · ' + ev.window.replace('_', ' ') : '');
      renderUsage(Object.assign({ at: Date.now() / 1000 }, ev));
      break;
    case 'note':
      log('note', 'NOTE', ev.message || '');
      break;
    case 'error':
      log('error', 'ERROR', ev.message || '');
      answerBubble.textContent = ev.message || 'error';
      break;
    case 'confirm':
      showConfirm(ev);
      break;
    case 'confirm_done':
      closeConfirm(ev.id, ev.allow);
      break;
    case 'complete':
      if (ev.session_id) $('#sSession').textContent = String(ev.session_id).slice(0, 8);
      log('ok', 'DONE', (ev.ms || 0) + 'ms');
      break;
  }
}

/* ── confirmation gate ─────────────────────
   Shell commands and writes outside the Jarvis folder only run after the user
   confirms here: a click, or saying "bestätigt" / "nein". No answer = denied. */
var pendingConfirm = null;

function answerConfirm(allow) {
  if (!pendingConfirm) return;
  var id = pendingConfirm.id;
  fetch('/api/permission/answer', {
    method: 'POST', headers: headers({ 'content-type': 'application/json' }),
    body: JSON.stringify({ id: id, allow: !!allow })
  });
  closeConfirm(id, allow);
}

function showConfirm(ev) {
  var card = document.createElement('div');
  card.className = 'bubble confirm';
  card.innerHTML = '<div class="ctitle">Bestätigung erforderlich · ' + esc(ev.tool) + '</div>'
    + '<pre class="cdetail">' + esc(ev.detail || '') + '</pre>'
    + '<div class="cbtns"><button class="cyes">Bestätigen</button>'
    + '<button class="cno">Ablehnen</button><span class="ctimer"></span></div>';
  $('#transcript').appendChild(card);
  $('#transcript').scrollTop = $('#transcript').scrollHeight;
  var left = ev.timeout || 75;
  var timer = setInterval(function () {
    left -= 1;
    card.querySelector('.ctimer').textContent = 'automatisch abgelehnt in ' + left + ' s';
    if (left <= 0) clearInterval(timer);
  }, 1000);
  pendingConfirm = { id: ev.id, card: card, timer: timer };
  card.querySelector('.cyes').onclick = function () { answerConfirm(true); };
  card.querySelector('.cno').onclick = function () { answerConfirm(false); };
  log('note', 'FREIGABE', ev.tool + ': ' + String(ev.detail || '').slice(0, 80));
  setVoiceState('BESTÄTIGEN?', 'hot');
  speak('[serious] Bestätigung erforderlich, Sir.');
}

function closeConfirm(id, allow) {
  if (!pendingConfirm || pendingConfirm.id !== id) return;
  clearInterval(pendingConfirm.timer);
  var card = pendingConfirm.card;
  card.classList.add(allow ? 'approved' : 'denied');
  card.querySelector('.cbtns').innerHTML = allow ? '✓ bestätigt' : '✕ abgelehnt';
  pendingConfirm = null;
  log(allow ? 'ok' : 'note', 'FREIGABE', allow ? 'bestätigt' : 'abgelehnt');
  if (allow) approvalBeep();
  if (running) setVoiceState('THINKING', 'hot');
}

/* Short checkout-scanner beep when a confirmation goes through. */
var beepCtx = null;
function approvalBeep() {
  try {
    beepCtx = beepCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (beepCtx.state !== 'running') beepCtx.resume();
    var t = beepCtx.currentTime + 0.02;
    var osc = beepCtx.createOscillator(), gain = beepCtx.createGain();
    osc.type = 'square';
    osc.frequency.value = 1800;
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.08, t + 0.005);
    gain.gain.setValueAtTime(0.08, t + 0.12);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.15);
    osc.connect(gain); gain.connect(beepCtx.destination);
    osc.start(t); osc.stop(t + 0.16);
  } catch (e) { /* no audio, no beep */ }
}

/* ── voice ────────────────────────────────── */
/* stopSpeaking is set while JARVIS talks; calling it cuts him off mid-sentence
   (voice barge-in, Esc or Space). */
var stopSpeaking = null;

function interruptSpeech() {
  if (!stopSpeaking && !Speech.active) return false;
  Speech.cancelled = true;
  Speech.queue = [];
  if (!stopSpeaking) return true;
  var stop = stopSpeaking;
  stopSpeaking = null;
  stop();
  log('note', 'VOICE', 'unterbrochen');
  return true;
}

/* Feed JARVIS' own voice level into the orb while he talks. */
var orbAudioCtx = null;
function driveOrb(audio, isDone) {
  if (!window.JarvisOrb) return;
  try {
    orbAudioCtx = orbAudioCtx || new (window.AudioContext || window.webkitAudioContext)();
    // Routing through a suspended context would mute JARVIS — only tap the
    // audio once the context is actually running, otherwise skip the effect.
    if (orbAudioCtx.state !== 'running') { orbAudioCtx.resume(); return; }
    var src = orbAudioCtx.createMediaElementSource(audio);
    var an = orbAudioCtx.createAnalyser();
    an.fftSize = 512;
    src.connect(an); an.connect(orbAudioCtx.destination);
    Wave.speech = an;
    var buf = new Uint8Array(an.fftSize);
    (function tick() {
      if (isDone()) { JarvisOrb.setLevel(0); if (Wave.speech === an) Wave.speech = null; return; }
      an.getByteTimeDomainData(buf);
      var sum = 0;
      for (var i = 0; i < buf.length; i++) { var v = (buf[i] - 128) / 128; sum += v * v; }
      JarvisOrb.setLevel(Math.sqrt(sum / buf.length) * 4);
      requestAnimationFrame(tick);
    })();
  } catch (e) { /* audio still plays through the element */ }
}

/* Waveform in the right rail: Jarvis' voice while he speaks, otherwise the live microphone.
   Only real signal — a flat line when nothing is there. */
var Wave = { speech: null, cv: null };
(function waveLoop() {
  var cv = document.getElementById('wave');
  if (!cv) return;
  var g = cv.getContext('2d'), buf = new Uint8Array(512);
  (function draw() {
    var an = Wave.speech || (typeof Live !== 'undefined' && Live.on && Live.analyser) || null;
    g.clearRect(0, 0, cv.width, cv.height);
    g.strokeStyle = Wave.speech ? 'rgba(95,228,255,.95)' : 'rgba(95,228,255,.45)';
    g.lineWidth = 1.5;
    g.beginPath();
    if (an) {
      var n = Math.min(buf.length, an.fftSize || 512);
      an.getByteTimeDomainData(buf.subarray(0, n));
      for (var i = 0; i < n; i++) {
        var x = i / (n - 1) * cv.width, y = buf[i] / 255 * cv.height;
        if (i) g.lineTo(x, y); else g.moveTo(x, y);
      }
    } else { g.moveTo(0, cv.height / 2); g.lineTo(cv.width, cv.height / 2); }
    g.stroke();
    requestAnimationFrame(draw);
  })();
})();

/* Notification centre behind the bell: messages, homelab incidents, open proposals. */
var Notes = {
  unseen: 0,
  bump: function () {
    Notes.unseen++;
    var b = $('#bellCount'); if (b) { b.hidden = false; b.textContent = Notes.unseen > 9 ? '9+' : Notes.unseen; }
  },
  open: async function () {
    Notes.unseen = 0; $('#bellCount').hidden = true;
    $('#noteBox').hidden = false;
    try {
      var n = await (await fetch('/api/notifications', { headers: headers() })).json();
      var t = function (s) { return new Date(s * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }); };
      var html = '';
      if (n.paused_until) html += '<h3>NOT-AUS</h3><ul><li class="bad">Pausiert bis ' + t(n.paused_until) + ' — „weitermachen“ hebt es auf.</li></ul>';
      if (n.proposals.length) html += '<h3>OFFENE VORSCHLÄGE</h3><ul>' + n.proposals.map(function (p) {
        return '<li class="prop"><b>' + esc(p.title) + '</b><br>' + esc(p.why) + '</li>'; }).join('') + '</ul>';
      html += '<h3>MELDUNGEN</h3><ul>' + (n.items.map(function (e) {
        return '<li><small>' + t(e.at) + ' · ' + esc(e.kind) + '</small>' + esc(e.text) + '</li>'; }).join('')
        || '<li>Noch keine Meldungen seit dem Start.</li>') + '</ul>';
      if (n.incidents.length) html += '<h3>HOMELAB</h3><ul>' + n.incidents.map(function (i) {
        return '<li class="' + (i.state === 'ok' ? 'ok' : 'bad') + '"><small>' + esc(i.at) + '</small>' + esc(i.text) + '</li>'; }).join('') + '</ul>';
      $('#noteBody').innerHTML = html;
      $('#noteAge').textContent = 'Profil ' + (n.persona || '—');
    } catch (e) { $('#noteBody').innerHTML = '<p class="hint">Nicht erreichbar.</p>'; }
  }
};
(function initNotes() {
  var bell = document.getElementById('btnBell');
  if (!bell) return;
  bell.onclick = Notes.open;
  $('#noteClose').onclick = function () { $('#noteBox').hidden = true; };
  $('#noteBox').onclick = function (e) { if (e.target.id === 'noteBox') $('#noteBox').hidden = true; };
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') $('#noteBox').hidden = true; });
})();

/* Views: ?view=wall (command centre, e.g. a second monitor) or ?view=mini; remembered. */
(function initViews() {
  var set = function (v) {
    document.body.classList.remove('view-wall', 'view-mini');
    if (v) document.body.classList.add('view-' + v);
    try { localStorage.setItem('jarvis.view', v || ''); } catch (e) { /* private mode */ }
    ['Wall', 'Mini'].forEach(function (k) { var b = document.getElementById('btn' + k); if (b) b.classList.toggle('on', v === k.toLowerCase()); });
  };
  var q = new URLSearchParams(location.search).get('view'), saved = '';
  try { saved = localStorage.getItem('jarvis.view') || ''; } catch (e) { /* private mode */ }
  set(q !== null ? q : saved);
  var wall = document.getElementById('btnWall'), mini = document.getElementById('btnMini');
  if (wall) wall.onclick = function () {
    var on = !document.body.classList.contains('view-wall');
    set(on ? 'wall' : '');
    if (on && document.documentElement.requestFullscreen) document.documentElement.requestFullscreen().catch(function () {});
    else if (!on && document.fullscreenElement) document.exitFullscreen();
  };
  if (mini) mini.onclick = function () { set(document.body.classList.contains('view-mini') ? '' : 'mini'); };
})();

/* Output volume, 0–1 — set by the slider in the right rail, remembered across reloads. */
var voiceVolume = (function () {
  var v = parseFloat(localStorage.getItem('jarvis.volume'));
  return isNaN(v) ? 0.6 : Math.min(1, Math.max(0, v));
})();
var currentAudio = null;
(function initVolume() {
  var slider = document.getElementById('volume');
  var label = document.getElementById('volumeVal');
  if (!slider) return;
  slider.value = Math.round(voiceVolume * 100);
  label.textContent = slider.value;
  slider.addEventListener('input', function () {
    voiceVolume = slider.value / 100;
    label.textContent = slider.value;
    localStorage.setItem('jarvis.volume', String(voiceVolume));
    if (currentAudio) currentAudio.volume = outVolume();
  });
})();

/* What actually comes out: the slider, softer during the quiet hours (JARVIS_NIGHT_VOLUME). */
function outVolume() {
  return voiceVolume * (window.__quiet ? (window.__nightVolume == null ? 0.5 : window.__nightVolume) : 1);
}

/* Spotify quieter while he talks (server: pc.duck). Released a moment after the
   last sentence, so the music does not pump between sentences. */
var Duck = { on: false, timer: null };
function duckMusic(on) {
  clearTimeout(Duck.timer);
  var send = function (v) {
    if (Duck.on === v) return;
    Duck.on = v;
    fetch('/api/duck', { method: 'POST', headers: headers({ 'content-type': 'application/json' }),
      body: JSON.stringify({ on: v }) }).catch(function () {});
  };
  if (on) send(true);
  else Duck.timer = setTimeout(function () { if (!Speech.active && !Live.speaking) send(false); }, 900);
}

/* Runs fn once on the next click/key — browsers only unlock audio after one. */
function afterGesture(fn) {
  var go = function () {
    window.removeEventListener('pointerdown', go, true);
    window.removeEventListener('keydown', go, true);
    fn();
  };
  window.addEventListener('pointerdown', go, true);
  window.addEventListener('keydown', go, true);
}

function playBlob(blob) {
  return new Promise(function (resolve) {
    var url = URL.createObjectURL(blob);
    var audio = new Audio(url);
    audio.volume = outVolume();
    currentAudio = audio;
    var finished = false;
    var done = function () {
      if (finished) return;
      finished = true;
      stopSpeaking = null;
      URL.revokeObjectURL(url);
      resolve();
    };
    audio.onended = done;
    audio.onerror = done;
    stopSpeaking = function () { audio.pause(); done(); };
    driveOrb(audio, function () { return finished; });
    audio.play().catch(function (e) {
      // Autoplay blocked (no click yet on this page): play on the first gesture
      // instead of silently dropping the startup briefing.
      if (e && e.name === 'NotAllowedError') {
        log('note', 'VOICE', 'Ton blockiert — einmal klicken, dann spricht er');
        afterGesture(function () { if (!finished) audio.play().catch(done); });
      } else done();
    });
  });
}

/* ── sentence streaming ──────────────────────
   JARVIS starts talking as soon as his first sentence is written instead of
   after the whole answer: every finished sentence is sent to /api/speak right
   away (requests overlap with Claude still writing), and the clips play back
   in order. */
var Speech = { queue: [], upTo: 0, active: false, cancelled: false, waiters: [] };
var MIN_CHUNK = 28;   // don't vocalise tiny fragments on their own

function speechReset() {
  Speech.queue = []; Speech.upTo = 0; Speech.cancelled = false;
}

function fetchVoice(text) {
  if (!window.__serverVoice) return Promise.resolve(null);
  return fetch('/api/speak', {
    method: 'POST', headers: headers({ 'content-type': 'application/json' }),
    body: JSON.stringify({ text: text })
  }).then(function (res) { return res.ok ? res.blob() : null; })
    .catch(function () { return null; });
}

function speakBrowser(text) {
  if (!window.speechSynthesis) return Promise.resolve();
  return new Promise(function (resolve) {
    var u = new SpeechSynthesisUtterance(stripTones(text));
    u.rate = 1.02; u.pitch = 0.92; u.volume = outVolume(); u.lang = 'de-DE';
    u.onend = resolve; u.onerror = resolve;
    stopSpeaking = function () { speechSynthesis.cancel(); resolve(); };
    speechSynthesis.speak(u);
  });
}

/* Cut finished sentences off the growing answer and queue them. */
function feedSpeech(full, final) {
  if (Speech.cancelled) return;
  var rest = full.slice(Speech.upTo);
  var re = /[\s\S]*?[.!?…:](?=\s|$)/g, m, take = 0, chunk = '';
  while ((m = re.exec(rest)) !== null && m[0].length) {
    chunk = rest.slice(take, m.index + m[0].length);
    if (stripTones(chunk).length >= MIN_CHUNK) {
      queueSpeech(chunk);
      take = m.index + m[0].length;
    }
  }
  if (final && stripTones(rest.slice(take)).trim()) {
    queueSpeech(rest.slice(take));
    take = rest.length;
  }
  Speech.upTo += take;
}

function queueSpeech(text) {
  text = text.trim();
  if (!stripTones(text)) return;
  Speech.queue.push({ text: text, blob: fetchVoice(text) });
  playSpeechQueue();
}

async function playSpeechQueue() {
  if (Speech.active) return;
  Speech.active = true;
  setVoiceState('SPEAKING', 'hot');
  Live.speaking = true;
  duckMusic(true);
  try {
    while (Speech.queue.length && !Speech.cancelled) {
      var item = Speech.queue.shift();
      var blob = await item.blob;
      if (Speech.cancelled) break;
      if (blob) await playBlob(blob);
      else await speakBrowser(item.text);
    }
  } finally {
    Speech.active = false;
    stopSpeaking = null;
    Live.speaking = false;
    Live.spokeAt = performance.now();
    duckMusic(false);
    if (!Speech.cancelled) Wake.followUp(answerText);   // "Und morgen?" needs no wake word
    if (!Live.armed && !running) setVoiceState(Live.on ? 'LISTENING' : 'IDLE', Live.on ? 'hot' : '');
    var w = Speech.waiters; Speech.waiters = [];
    w.forEach(function (f) { f(); });
  }
}

/* Resolves once everything queued has been spoken (or cut off). */
function speechDrained() {
  if (!Speech.active && !Speech.queue.length) return Promise.resolve();
  return new Promise(function (resolve) { Speech.waiters.push(resolve); });
}

/* Resolves when playback actually FINISHES, not when it starts — live mode
   needs that to know when it is safe to listen again. */
async function speak(text) {
  setVoiceState('SPEAKING', 'hot');
  Live.speaking = true;
  duckMusic(true);
  try {
    if (window.__serverVoice) {
      try {
        var res = await fetch('/api/speak', {
          method: 'POST', headers: headers({ 'content-type': 'application/json' }),
          body: JSON.stringify({ text: text })
        });
        if (res.ok) { await playBlob(await res.blob()); return; }
        log('note', 'VOICE', 'server voice failed, using browser');
      } catch (e) { log('note', 'VOICE', 'server voice unreachable, using browser'); }
    }
    if (!window.speechSynthesis) return;
    await new Promise(function (resolve) {
      var u = new SpeechSynthesisUtterance(stripTones(text));
      u.rate = 1.02; u.pitch = 0.92;
      u.volume = outVolume();
      u.lang = 'de-DE';
      u.onend = resolve; u.onerror = resolve;
      stopSpeaking = function () { speechSynthesis.cancel(); resolve(); };
      speechSynthesis.cancel();
      speechSynthesis.speak(u);
    });
  } finally {
    stopSpeaking = null;
    Live.speaking = false;
    Live.spokeAt = performance.now();
    duckMusic(false);
    Wake.followUp(text);
    if (!Live.armed) setVoiceState(Live.on ? 'LISTENING' : 'IDLE', Live.on ? 'hot' : '');
  }
}

/* ── wake word ─────────────────────────────────
   JARVIS_WAKE on the server: off = every utterance counts (as before);
   text = only utterances addressed to "Jarvis" — at the start ("Jarvis, mach
   das Licht an") or at the end ("Mach das Licht an, Jarvis"); oww = the
   server also hears "Hey Jarvis" (openWakeWord) and opens the mic for a few
   seconds. After every answer there is a follow-up window without the wake word. */
var WAKE_NAME = '(?:jarvis|javis|jarwis|jervis|tscharvis|dschawis|charvis|jarvi)';
var WAKE_RE = new RegExp('^\\W*(?:(?:hey|hi|hallo|ok|okay|he|ey|du)\\W+)?' + WAKE_NAME + '\\b[\\s,.!?:;-]*', 'i');
var WAKE_END_RE = new RegExp('[\\s,;-]+(?:bitte\\W+)?' + WAKE_NAME + '\\W*$', 'i');
var Wake = {
  cfg: { mode: 'off', window_s: 7, followup_s: 8 },
  until: 0,
  configure: function (s) { Wake.cfg = Object.assign(Wake.cfg, s || {}); },
  mode: function () { return Wake.cfg.mode || 'off'; },
  awake: function () {
    return Wake.mode() === 'off' || !!pendingConfirm || performance.now() < Wake.until;
  },
  open: function (seconds) {
    Wake.until = Math.max(Wake.until, performance.now() + seconds * 1000);
    updateMicButton();
    setTimeout(updateMicButton, seconds * 1000 + 50);
  },
  /* after Jarvis spoke: a moment to answer him — longer if he asked something */
  followUp: function (text) {
    if (Wake.mode() === 'off') return;
    var asked = /\?\s*$/.test(stripTones(text || ''));
    Wake.open((Wake.cfg.followup_s || 8) * (asked ? 1.6 : 1));
  },
  /* the server heard "Hey Jarvis" */
  lastHeard: -1e9,
  heard: function () {
    Wake.lastHeard = performance.now();
    if (!Live.on) return;
    if (Live.speaking || Speech.active || Ringing.on) { interruptSpeech(); Ringing.stop(); }
    Wake.open(Wake.cfg.window_s || 7);
    wakeChime();
    log('note', 'WAKE', 'Aktivierungswort gehört');
  },
  /* text mode: strip the wake word, or say the utterance was not meant for us */
  check: function (text, startedAwake) {
    var m = WAKE_RE.exec(text);
    if (m) return { ok: true, text: text.slice(m[0].length).trim() };
    var e = WAKE_END_RE.exec(text);
    // keep the full stop / question mark, or the sentence sounds unfinished and is held back
    if (e) return { ok: true, text: text.slice(0, e.index).trim() + ((/[.!?]\s*$/.exec(text) || [''])[0].trim()) };
    if (startedAwake) return { ok: true, text: text };
    return { ok: false, text: text };
  }
};

function wakeChime() {
  try {
    beepCtx = beepCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (beepCtx.state !== 'running') beepCtx.resume();
    [880, 1320].forEach(function (f, i) {
      var t = beepCtx.currentTime + 0.02 + i * 0.09;
      var osc = beepCtx.createOscillator(), gain = beepCtx.createGain();
      osc.type = 'sine'; osc.frequency.value = f;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.06 * voiceVolume + 0.01, t + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.12);
      osc.connect(gain); gain.connect(beepCtx.destination);
      osc.start(t); osc.stop(t + 0.13);
    });
  } catch (e) { /* no audio, no chime */ }
}

/* ── alarm ringing ─────────────────────────────
   Timers, the alarm clock and reminders ring before Jarvis speaks. Talking,
   Esc, Space or the Stop button end the ringing (it counts as "speaking", so
   the normal barge-in path stops it). */
var Ringing = {
  on: false, timer: null, done: null,
  interrupted: false,
  start: function (seconds) {
    return new Promise(function (resolve) {
      Ringing.on = true;
      Ringing.interrupted = false;
      Ringing.done = resolve;
      Live.speaking = true;
      stopSpeaking = function () { Ringing.interrupted = true; Ringing.stop(); };
      setVoiceState('KLINGELT', 'hot');
      var end = performance.now() + seconds * 1000;
      (function pulse() {
        if (!Ringing.on) return;
        if (performance.now() > end) return Ringing.stop();
        try {
          beepCtx = beepCtx || new (window.AudioContext || window.webkitAudioContext)();
          if (beepCtx.state !== 'running') beepCtx.resume();
          [0, 0.18, 0.36].forEach(function (off) {
            var t = beepCtx.currentTime + 0.02 + off;
            var osc = beepCtx.createOscillator(), gain = beepCtx.createGain();
            osc.type = 'triangle'; osc.frequency.value = 1046;
            gain.gain.setValueAtTime(0.0001, t);
            gain.gain.exponentialRampToValueAtTime(0.18 * Math.max(voiceVolume, 0.3), t + 0.01);
            gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.14);
            osc.connect(gain); gain.connect(beepCtx.destination);
            osc.start(t); osc.stop(t + 0.15);
          });
        } catch (e) { /* no audio — the text still appears */ }
        Ringing.timer = setTimeout(pulse, 1300);
      })();
    });
  },
  stop: function () {
    if (!Ringing.on) return;
    Ringing.on = false;
    clearTimeout(Ringing.timer);
    if (stopSpeaking && !Speech.active) stopSpeaking = null;
    Live.speaking = false;
    Live.spokeAt = performance.now();
    var d = Ringing.done; Ringing.done = null;
    if (d) d();
    updateMicButton();
  }
};

/* ── push from the server (/api/events) ────────
   Timers ringing, "Die Waschmaschine ist fertig", appointments, new devices,
   the wake word. Spoken only when nothing else is going on; during the quiet
   hours notices are only shown (alarms still ring). */
var Push = { queue: [], busy: false, es: null };

function connectEvents() {
  try {
    Push.es = new EventSource('/api/events?token=' + encodeURIComponent(TOKEN));
  } catch (e) { return; }
  Push.es.onmessage = function (m) {
    var ev;
    try { ev = JSON.parse(m.data); } catch (e) { return; }
    // The wake word is heard by the PC's microphone — a phone elsewhere must not start listening.
    if (ev.t === 'wake') return window.__remote ? undefined : Wake.heard();
    if (ev.t === 'timers') return Timers.set(ev.items || []);
    if (ev.t === 'say' && window.__remote) ev = Object.assign({}, ev, { silent: true });   // the PC reads it out
    if (ev.t === 'say' || ev.t === 'alarm') { Push.queue.push(ev); drainPush(); Notes.bump(); }
  };
}

function pushBlocked() {
  return running || Live.armed || Live.sending || Live.speaking || Speech.active || !!pendingConfirm;
}

async function drainPush() {
  if (Push.busy) return;
  Push.busy = true;
  try {
    while (Push.queue.length) {
      if (pushBlocked()) { await new Promise(function (r) { setTimeout(r, 800); }); continue; }
      var ev = Push.queue.shift();
      var b = bubble('jarvis', ev.text || '');
      b.classList.add('push');
      log(ev.t === 'alarm' ? 'run' : 'note', ev.t === 'alarm' ? 'WECKER' : 'MELDUNG', ev.text || '');
      if (ev.t === 'alarm') {
        await Ringing.start(ev.kind === 'alarm' ? 45 : 8);
        // Talked over the ringing ("Stopp", "Schlummern"): he listens instead of reading it out.
        if (ev.text && !Ringing.interrupted) await speak(ev.text);
        else Wake.followUp('?');
      } else if (ev.silent) {
        b.classList.add('silent');
      } else if (ev.text) {
        wakeChime();
        await speak(ev.text);
      }
    }
  } finally { Push.busy = false; }
}

/* Countdown chip in the top bar: the next timer, alarm or reminder. */
var Timers = {
  items: [],
  set: function (items) { Timers.items = (items || []).slice().sort(function (a, b) { return a.at - b.at; }); Timers.render(); },
  render: function () {
    var chip = $('#chipTimer');
    if (!chip) return;
    var now = Date.now() / 1000, list = Timers.items.filter(function (i) { return i.at > now - 2; });
    if (!list.length) { chip.hidden = true; return; }
    chip.hidden = false;
    var next = list[0], left = Math.max(0, Math.round(next.at - now)), txt;
    if (next.kind === 'timer' || left < 3600) {
      var h = Math.floor(left / 3600), m = Math.floor(left % 3600 / 60), s = left % 60;
      txt = (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');
    } else {
      var d = new Date(next.at * 1000);
      txt = d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
      if (d.toDateString() !== new Date().toDateString()) txt = ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'][d.getDay()] + ' ' + txt;
    }
    var label = { timer: 'Timer', alarm: 'Wecker', reminder: 'Erinnerung', action: 'Geplant' }[next.kind] || '';
    $('#tNext').textContent = txt;
    $('#tSub').textContent = label + (next.text ? ' · ' + next.text : '') + (list.length > 1 ? ' · +' + (list.length - 1) : '');
    $('#chipTimer .ico').textContent = next.kind === 'alarm' ? '⏰' : next.kind === 'reminder' ? '🔔' : '⏱';
    chip.title = list.map(function (i) {
      return ({ timer: 'Timer', alarm: 'Wecker', reminder: 'Erinnerung', action: 'Geplant' }[i.kind] || i.kind) + ' '
        + new Date(i.at * 1000).toLocaleString('de-DE', { weekday: 'short', hour: '2-digit', minute: '2-digit' })
        + (i.text ? ' — ' + i.text : '');
    }).join('\n');
  }
};

/* ── live voice ───────────────────────────────
   Hold the mic open, watch the input level, and cut an utterance when you stop
   talking. No button press per turn.

   Two things this has to get right:
   - It must not hear JARVIS. Detection is suspended while a turn is running and
     while audio is playing, otherwise the reply gets transcribed as your next
     question and it talks to itself forever.
   - Room tone varies. The threshold is calibrated from your actual noise floor
     at start rather than hardcoded, so a noisy room does not trigger constantly. */
var CONTINUE_MS = 7000;        // talk again this soon after sending = same sentence
var CONTINUE_SUSTAIN_MS = 250; // ...for at least this long (a cough does not count)
var SILENCE_MS = 2000;    // quiet this long ends the utterance (people pause to think — shorter cuts them off)
// After that pause the clip is transcribed; if the text sounds unfinished (ends on
// "ähm", "und", "dass", a comma, or without a full stop) it is held this long for the rest.
var UNFINISHED_HOLD_MS = 3000;
var UNFINISHED_END = /(?:^|\s)(ähm|äh|ehm|öhm|hm+|mhm|und|oder|aber|also|dass|weil|wenn|ob|der|die|den|dem|des|ein|eine|einen|einem|mit|für|von|vom|zum|zur|im|halt|quasi|sozusagen|irgendwie|nämlich|bitte\s*,)\s*[,.…-]*\s*$/i;

function looksUnfinished(text) {
  var t = String(text || '').trim();
  if (!t) return false;
  if (/(\.\.\.|…|,|-|–)\s*$/.test(t)) return true;          // trailing comma / ellipsis / dash
  if (UNFINISHED_END.test(t)) return true;                   // trailing filler or connector word
  if (!/[.!?]["”'»)]?\s*$/.test(t) && t.split(/\s+/).length >= 3) return true;   // no full stop at all
  return false;
}
var MIN_SPEECH_MS = 350;   // shorter than this is a cough, not a sentence
var MAX_SPEECH_MS = 20000; // hard stop so a stuck mic cannot record forever
var BARGE_MS = 450;        // talk over JARVIS this long and he stops
var BARGE_FACTOR = 4;      // ...this many times above the noise threshold (raise if he cuts himself off)
var ECHO_TAIL_MS = 700;    // stay deaf this long after he stops talking (room echo of his own voice)
var BARGE_MIN = 0.03;      // ...and at least this loud
var BARGE_AMBIENT = 2.5;   // ...and this much louder than steady background (TV, music)
var BRIEFING_BARGE_FACTOR = 1.6;  // while the briefing is being prepared, normal speech is enough
var PTT_HOLD_MS = 350;     // hold the Live button this long = push-to-talk
// Background music: the threshold follows a slow average of the room level, so
// steady sound (music, TV, fan) lifts it while short speech bursts barely do.
var AMBIENT_RATE = 0.002;  // per frame — ~8 s to settle at 60 fps
var AMBIENT_FACTOR = 1.8;  // speak this much louder than the background
var NOISE_TEXT = /^\W*[\[(*♪]?\s*(musik|music|applaus|lachen|gelächter|untertitel|copyright|♪)/i;

var Live = {
  on: false, speaking: false, armed: false, sending: false,
  stream: null, ctx: null, analyser: null, data: null,
  recorder: null, chunks: [], raf: 0,
  threshold: 0.02, base: 0.02, ambient: 0, voiceStart: 0, lastVoice: 0, bargeSince: 0,
  lastSent: null, continuing: false, contSince: 0,

  async enable() {
    if (Live.on) return;
    if (!window.__serverSTT) await loadStatus();   // status may simply not be loaded yet
    if (!window.__serverSTT) {
      micProblem = 'blocked';
      $('#btnMic').title = 'Der Server meldet keine Spracherkennung — Jarvis-Fenster prüfen bzw. neu starten.';
      log('error', 'VOICE', 'Spracherkennung auf dem Server nicht verfügbar');
      updateMicButton();
      return;
    }
    try {
      Live.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
      });
    } catch (e) {
      var denied = /NotAllowed|Permission|denied/i.test(String(e && (e.name + e.message)));
      micProblem = 'blocked';
      $('#btnMic').title = denied
        ? 'Der Browser blockiert das Mikrofon: links in der Adressleiste aufs Schloss-Symbol klicken → Mikrofon „Zulassen“, dann F5.'
        : 'Kein Mikrofon gefunden: ' + String(e).slice(0, 80);
      log('error', 'VOICE', denied ? 'Mikrofon im Browser blockiert — Schloss-Symbol links in der Adressleiste → Mikrofon zulassen, dann F5'
                                   : 'Mikrofon nicht verfügbar: ' + String(e).slice(0, 70));
      updateMicButton();
      return;
    }
    micProblem = null;
    $('#btnMic').title = 'Klicken: Live an/aus · Gedrückt halten: sprechen (Push-to-Talk)';
    Live.ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (Live.ctx.state === 'suspended') {
      // Started from boot without a click: the browser only delivers silence
      // until the first click/key. Unlock then and measure the room again.
      var ctx = Live.ctx;
      ctx.resume();
      micProblem = 'needs-click';
      afterGesture(function () { Live.unlock(); });
    }
    Live.analyser = Live.ctx.createAnalyser();
    Live.analyser.fftSize = 1024;
    Live.data = new Uint8Array(Live.analyser.fftSize);
    Live.ctx.createMediaStreamSource(Live.stream).connect(Live.analyser);

    Live.on = true;
    document.body.classList.add('live');
    $('#btnMic').classList.add('live');
    setVoiceState('CALIBRATING', 'hot');
    await Live.calibrate();
    setVoiceState('LISTENING', 'hot');
    log('ok', 'VOICE', 'live mode on — just talk, it sends when you stop');
    Live.loop();
  },

  /* After a click: let the audio through and re-measure the room (the first
     calibration ran on silence while the browser still held the mic back). */
  async unlock() {
    if (!Live.ctx) return;
    if (Live.ctx.state === 'suspended') { try { await Live.ctx.resume(); } catch (e) {} }
    if (Live.ctx.state !== 'running') return;
    if (micProblem === 'needs-click') {
      micProblem = null;
      await Live.calibrate();
      log('ok', 'VOICE', 'Mikrofon aktiv — einfach sprechen');
    }
    updateMicButton();
  },

  /* Sample the room for a moment and sit above it. */
  calibrate() {
    return new Promise(function (resolve) {
      var samples = [], t0 = performance.now();
      (function tick() {
        if (!Live.on) return resolve();
        samples.push(Live.level());
        if (performance.now() - t0 < 700) return requestAnimationFrame(tick);
        samples.sort(function (a, b) { return a - b; });
        var floor = samples[Math.floor(samples.length / 2)] || 0.005;
        Live.threshold = Live.base = Math.max(floor * 3.2, 0.015);
        Live.ambient = floor;
        log('note', 'VOICE', 'noise floor ' + floor.toFixed(4)
          + ' → threshold ' + Live.threshold.toFixed(4));
        resolve();
      })();
    });
  },

  level() {
    if (!Live.analyser) return 0;
    Live.analyser.getByteTimeDomainData(Live.data);
    var sum = 0;
    for (var i = 0; i < Live.data.length; i++) {
      var v = (Live.data[i] - 128) / 128;
      sum += v * v;
    }
    return Math.sqrt(sum / Live.data.length);
  },

  meter(rms) {
    if (window.JarvisOrb && !Live.speaking) JarvisOrb.setLevel(rms / (Live.threshold * 5));
    var pct = Math.min(100, Math.round((rms / (Live.threshold * 4)) * 100));
    var el = $('#vuFill');
    if (el) {
      el.style.width = pct + '%';
      el.className = Live.armed ? 'vufill hot' : 'vufill';
    }
    var ml = $('#micLvl');
    if (ml) ml.style.width = pct + '%';
    updateMicButton();
  },

  loop() {
    if (!Live.on) return;
    Live.raf = requestAnimationFrame(Live.loop);
    // Push-to-talk: the button decides when the sentence ends, not the silence detector.
    if (Live.ptt) { Live.meter(Live.level()); return; }
    // While JARVIS talks, only listen for a barge-in: sustained, clearly louder
    // speech than the room (echo cancellation removes most of his own voice).
    // This also covers the stretch where he is already talking while Claude is
    // still writing — interrupting then cancels the rest of the answer too.
    if (Live.speaking) {
      if (pendingConfirm) { Live.meter(0); return; }
      var lvl = Live.level();
      Live.meter(lvl);
      var t = performance.now();
      // Scale the quiet-room threshold, not the live one: with TV or music on the
      // live threshold already sits above the background, and ×4 on top of that
      // asked for shouting.
      if (lvl > Math.max(Live.base * BARGE_FACTOR, Live.ambient * BARGE_AMBIENT, BARGE_MIN)) {
        if (!Live.bargeSince) Live.bargeSince = t;
        else if (t - Live.bargeSince > BARGE_MS && interruptSpeech()) {
          Live.bargeSince = 0;
          if (running) cancelRun();
          Wake.open(4);               // talking over him is talking to him — no wake word needed
          Live.start(t - BARGE_MS);   // you are already talking: record right away
        }
      } else {
        Live.bargeSince = 0;
      }
      return;
    }
    Live.bargeSince = 0;
    // While thinking, only listen for "I wasn't finished": if he talks again
    // shortly after a sentence was sent, drop that run and record the rest —
    // send() then glues both parts together. Otherwise stay deaf — except
    // during the startup briefing, see below.
    if (running && !pendingConfirm) {
      var since = Live.lastSent ? performance.now() - Live.lastSent.at : Infinity;
      // Briefing: normal speech (sustained, a bit above the room) cancels it —
      // The user must be able to just start talking. Short noises don't count.
      if (briefingActive && since >= CONTINUE_MS) {
        var bl = Live.level(), bt = performance.now();
        Live.meter(bl);
        if (Live.armed) Live.discard();
        if (bl > Math.max(Live.threshold * BRIEFING_BARGE_FACTOR, 0.012)) {
          if (!Live.bargeSince) Live.bargeSince = bt;
          else if (bt - Live.bargeSince > BARGE_MS) {
            Live.bargeSince = 0;
            briefingActive = false;
            log('note', 'VOICE', 'Briefing abgebrochen — ich höre');
            cancelRun();
            Live.start(bt - BARGE_MS);
          }
        } else Live.bargeSince = 0;
        return;
      }
      if (since < CONTINUE_MS) {
        var lv = Live.level(), tn = performance.now();
        Live.meter(lv);
        if (Live.armed || lv > Live.threshold) {
          if (!Live.armed && !Live.contSince) { Live.contSince = tn; return; }
          if (Live.armed || tn - Live.contSince > CONTINUE_SUSTAIN_MS) {
            Live.contSince = 0;
            Live.continuing = true;
            cancelRun();
            log('note', 'VOICE', 'Sie reden weiter — warte auf den Rest');
            if (!Live.armed) Live.start(tn - CONTINUE_SUSTAIN_MS);
            else setVoiceState('HEARING', 'hot');
          }
        } else Live.contSince = 0;
        return;
      }
      if (Live.armed) Live.discard();
      Live.meter(0);
      return;
    }
    var rms = Live.level();
    Live.ambient += (rms - Live.ambient) * AMBIENT_RATE;
    Live.threshold = Math.max(Live.base, Live.ambient * AMBIENT_FACTOR);
    Live.meter(rms);
    var now = performance.now();
    if (rms > Live.threshold) Live.lastVoice = now;

    if (!Live.armed) {
      if (rms > Live.threshold && now - (Live.spokeAt || 0) > ECHO_TAIL_MS) Live.start(now);
      return;
    }
    // Nobody talks 20 s without a breath — that is music or TV, so drop it.
    if (now - Live.voiceStart > MAX_SPEECH_MS) {
      log('note', 'VOICE', 'Dauergeräusch verworfen (Musik?)');
      return Live.discard();
    }
    if (now - Live.lastVoice > SILENCE_MS) {
      if (now - Live.voiceStart - SILENCE_MS > MIN_SPEECH_MS) Live.finish();
      else Live.discard();
    }
  },

  start(now) {
    try {
      Live.chunks = [];
      Live.recorder = new MediaRecorder(Live.stream);
      // Was this meant for him? Decided in send(): awake when it started, the
      // server heard "Hey Jarvis" meanwhile, or the transcript starts with "Jarvis".
      var rec = { at: now, awake: Wake.awake() || Live.ptt };
      Live.recorder.ondataavailable = function (e) { if (e.data.size) Live.chunks.push(e.data); };
      Live.recorder.onstop = function () {
        var blob = new Blob(Live.chunks, { type: Live.recorder.mimeType || 'audio/webm' });
        if (Live.sending) { Live.sending = false; Live.send(blob, rec); }
      };
      Live.recorder.start();
      Live.armed = true;
      Live.voiceStart = now;
      Live.lastVoice = now;
      setVoiceState('HEARING', 'hot');
    } catch (e) {
      log('error', 'VOICE', 'recorder failed: ' + String(e).slice(0, 60));
      Live.armed = false;
    }
  },

  finish() {
    Live.armed = false;
    Live.sending = true;
    setVoiceState('TRANSCRIBING', 'hot');
    try { Live.recorder.stop(); } catch (e) { Live.sending = false; }
  },

  discard() {
    Live.armed = false;
    Live.sending = false;
    try { if (Live.recorder && Live.recorder.state === 'recording') Live.recorder.stop(); } catch (e) {}
    setVoiceState(Live.on ? 'LISTENING' : 'IDLE', Live.on ? 'hot' : '');
  },

  async send(blob, rec) {
    try {
      var res = await fetch('/api/listen', {
        method: 'POST',
        headers: headers({ 'content-type': blob.type || 'audio/webm' }),
        body: blob
      });
      var data = await res.json();
      var text = (data && data.text || '').trim();
      if (!res.ok || !text) {
        log('note', 'VOICE', 'nothing usable in that clip');
        setVoiceState('LISTENING', 'hot');
        return;
      }
      if (text.replace(/[^\p{L}\p{N}]/gu, '').length < 2 || NOISE_TEXT.test(text)) {   // "." / "[BLANK_AUDIO]" / "(Musik)"
        if (NOISE_TEXT.test(text)) log('note', 'VOICE', 'ignoriert: "' + text.slice(0, 40) + '"');
        setVoiceState('LISTENING', 'hot');
        return;
      }
      log('note', 'VOICE', 'heard: "' + text + '"');
      if (pendingConfirm) {
        // Deliberate words only, so a stray "ja" from the room cannot approve.
        if (/best(ä|ae)tig/i.test(text) && !/nicht|kein/i.test(text)) answerConfirm(true);
        else if (/\b(nein|stopp?|abbrechen|ablehnen|nicht)\b/i.test(text)) answerConfirm(false);
        else log('note', 'FREIGABE', 'sag "bestätigt" oder "nein"');
        setVoiceState('LISTENING', 'hot');
        return;
      }
      // Wake word: room talk, TV and phone calls stay with the room.
      if (Wake.mode() !== 'off' && !Live.partial && !Live.continuing) {
        var heardWake = !!(rec && (rec.awake || Wake.lastHeard >= rec.at - 2500));
        var w = Wake.check(text, heardWake);
        if (!w.ok) {
          log('note', 'VOICE', 'ohne „Jarvis“ — nicht für mich');
          setVoiceState('LISTENING', 'hot');
          return;
        }
        text = w.text;
        if (text.replace(/[^\p{L}\p{N}]/gu, '').length < 2) {   // just "Jarvis?" — he is listening now
          Wake.open(Wake.cfg.window_s || 7);
          wakeChime();
          log('note', 'WAKE', 'ich höre');
          setVoiceState('LISTENING', 'hot');
          return;
        }
      }
      // A held-back unfinished start ("Als Allererstes mal bitte, ähm,") gets the rest glued on.
      if (Live.partial) {
        text = Live.partial + ' ' + text;
        Live.partial = null;
        clearTimeout(Live.partialTimer);
      }
      // Sounds unfinished? Don't send yet — wait a moment for the rest of the sentence.
      // Push-to-talk always sends: there the button said the sentence is over.
      if (!Live.pttSend && looksUnfinished(text)) {
        Live.partial = text;
        log('note', 'VOICE', 'klingt unfertig — warte auf den Rest: "' + text.slice(-40) + '"');
        setVoiceState('LISTENING', 'hot');
        var holdUntil = performance.now() + UNFINISHED_HOLD_MS;
        (function waitForRest() {
          Live.partialTimer = setTimeout(function () {
            if (!Live.partial) return;
            if (Live.armed || Live.sending || performance.now() < holdUntil) return waitForRest();
            var t = Live.partial;
            Live.partial = null;
            Live.lastSent = { text: t, at: performance.now() };
            transmit(t).then(function () { setVoiceState(Live.on ? 'LISTENING' : 'IDLE', Live.on ? 'hot' : ''); });
          }, 200);
        })();
        return;
      }
      Live.pttSend = false;
      // He kept talking after a pause: the earlier half belongs to this sentence.
      if (Live.continuing && Live.lastSent) text = Live.lastSent.text + ' ' + text;
      Live.continuing = false;
      Live.lastSent = { text: text, at: performance.now() };
      await transmit(text);
      setVoiceState(Live.on ? 'LISTENING' : 'IDLE', Live.on ? 'hot' : '');
    } catch (e) {
      log('error', 'VOICE', 'transcription failed: ' + String(e).slice(0, 70));
      setVoiceState('LISTENING', 'hot');
    }
  },

  disable() {
    Live.on = false;
    Live.armed = false;
    Live.ptt = false;
    micProblem = null;
    cancelAnimationFrame(Live.raf);
    try { if (Live.recorder && Live.recorder.state === 'recording') Live.recorder.stop(); } catch (e) {}
    if (Live.stream) Live.stream.getTracks().forEach(function (t) { t.stop(); });
    if (Live.ctx) { try { Live.ctx.close(); } catch (e) {} }
    Live.stream = Live.ctx = Live.analyser = null;
    document.body.classList.remove('live');
    $('#btnMic').classList.remove('live');
    Live.meter(0);
    setVoiceState('IDLE', '');
    log('note', 'VOICE', 'live mode off');
  }
};

/* Live button:
   - short click: Live on/off. If Live is already on but the browser still
     holds the mic back (auto-start without a click), the click unlocks it
     instead of switching it off.
   - hold: push-to-talk. Records while held, sends on release. Works while
     JARVIS talks or thinks too (it cuts him off). */
async function toggleLive() {
  if (!Live.on) {
    micProblem = null;
    localStorage.setItem('jarvis.live', '1');
    await Live.enable();
  } else if (micProblem === 'needs-click' || (Live.ctx && Live.ctx.state === 'suspended')) {
    await Live.unlock();
  } else {
    Live.disable();
    localStorage.setItem('jarvis.live', '0');
  }
  updateMicButton();
}

async function startPTT() {
  if (!Live.on) { await Live.enable(); if (!Live.on) return; }
  await Live.unlock();
  if (Live.speaking || Speech.active) interruptSpeech();
  if (running) cancelRun();
  briefingActive = false;
  Live.ptt = true;
  if (!Live.armed) Live.start(performance.now());
  setVoiceState('HEARING', 'hot');
  log('note', 'VOICE', 'Push-to-Talk — loslassen zum Senden');
}

function stopPTT() {
  if (!Live.ptt) return;
  Live.ptt = false;
  Live.pttSend = true;          // released button = sentence is over, send without waiting
  if (Live.armed) Live.finish();
  updateMicButton();
}

function setupMic() {
  var btn = $('#btnMic'), holdTimer = null, held = false;
  btn.addEventListener('pointerdown', function (e) {
    if (e.button !== 0) return;
    held = false;
    btn.setPointerCapture && btn.setPointerCapture(e.pointerId);
    holdTimer = setTimeout(function () { holdTimer = null; held = true; startPTT(); }, PTT_HOLD_MS);
  });
  var release = function () {
    if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; toggleLive(); }
    else if (held) { held = false; stopPTT(); }
  };
  btn.addEventListener('pointerup', release);
  btn.addEventListener('pointercancel', function () { if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; } stopPTT(); });
  // Keyboard (Enter/Space on the focused button) still toggles.
  btn.addEventListener('click', function (e) { if (e.detail === 0) toggleLive(); });
  btn.addEventListener('contextmenu', function (e) { e.preventDefault(); });
  updateMicButton();
}

/* ── on start ─────────────────────────────────
   Mic on by itself (the browser remembers the permission after the first "allow")
   and a short briefing on start — once per day, so a string of restarts does
   not repeat it. Talking while it is prepared cancels it. */
var BRIEFING_RESET = '7';
var briefingActive = false;
// The server assembles the facts itself (weather/golf, appointments, solar,
// mail — no stock news) and has them phrased in one small call.
var BRIEFING = 'Tagesbriefing zum Start';

async function bootSequence() {
  await loadStatus();
  // A phone or tablet starts quiet: no open microphone in the pocket, no briefing
  // the PC already gave — the mic button (tap = live, hold = push-to-talk) does it.
  if (window.__remote) return;
  if (window.__serverSTT && localStorage.getItem('jarvis.live') !== '0') Live.enable();
  var today = new Date().toLocaleDateString('sv-SE');   // YYYY-MM-DD, local time
  // Bump BRIEFING_RESET to get one extra briefing on the next load (for testing).
  if (localStorage.getItem('jarvis.briefingReset') !== BRIEFING_RESET) {
    localStorage.setItem('jarvis.briefingReset', BRIEFING_RESET);
    localStorage.removeItem('jarvis.briefingDay');
  }
  if (localStorage.getItem('jarvis.briefingDay') === today) {
    log('note', 'BRIEFING', 'übersprungen — heute schon gehabt');
    return;
  }
  localStorage.setItem('jarvis.briefingDay', today);
  briefingActive = true;
  try { await transmit(BRIEFING); } finally { briefingActive = false; }
}

/* ── devices (remote access) ───────────────────
   Only on the PC: a six-digit code for a new iPhone/iPad (typed on its
   /pair.html), and the list of paired devices, each removable. */
var Devices = {
  timer: null,
  api: async function (path, body) {
    var r = await fetch(path, { method: 'POST', headers: headers({ 'Content-Type': 'application/json' }),
                                body: JSON.stringify(body || {}) });
    return r.json();
  },
  open: async function () {
    $('#pairBox').hidden = false;
    $('#pairCode').textContent = '······';
    $('#pairHint').textContent = '';
    await Devices.list();
  },
  close: function () { $('#pairBox').hidden = true; clearInterval(Devices.timer); },
  newCode: async function () {
    var d = await Devices.api('/api/pair/start');
    $('#pairCode').textContent = d.code.slice(0, 3) + ' ' + d.code.slice(3);
    var until = Date.now() + d.ttl * 1000;
    clearInterval(Devices.timer);
    var tick = function () {
      var left = Math.max(0, Math.round((until - Date.now()) / 1000));
      $('#pairHint').textContent = left ? 'Auf dem Gerät eingeben · noch ' + Math.floor(left / 60) + ':'
        + String(left % 60).padStart(2, '0') : 'Abgelaufen — neuen Code holen.';
      if (!left) { $('#pairCode').textContent = '······'; clearInterval(Devices.timer); Devices.list(); }
    };
    tick();
    Devices.timer = setInterval(function () { tick(); }, 1000);
    if (!d.remote) $('#pairHint').textContent = 'Fernzugriff ist aus: JARVIS_REMOTE=1 in .env setzen.';
  },
  list: async function () {
    var d = await Devices.api('/api/devices'), ul = $('#pairList');
    ul.innerHTML = d.devices.length ? '' : '<li class="empty">Noch kein Gerät gekoppelt.</li>';
    d.devices.forEach(function (dev) {
      var li = document.createElement('li');
      li.innerHTML = '<b>' + esc(dev.name) + '</b><em>' + esc(dev.seen ? 'zuletzt ' + dev.seen : 'gekoppelt ' + dev.created)
        + '</em><button title="Gerät entfernen">✕</button>';
      li.querySelector('button').onclick = async function () {
        if (!confirm(dev.name + ' entfernen? Es muss danach neu gekoppelt werden.')) return;
        await Devices.api('/api/devices/revoke', { id: dev.id });
        Devices.list();
      };
      ul.appendChild(li);
    });
  }
};

function setupDevices() {
  if (window.__remote) return;
  $('#btnPair').onclick = Devices.open;
  $('#pairNew').onclick = Devices.newCode;
  $('#pairClose').onclick = Devices.close;
}

/* ── boot ─────────────────────────────────── */
window.addEventListener('DOMContentLoaded', function () {
  graph = new MemoryGraph($('#graph'), {
    onSelect: function (node, trace) { renderInspector(node, trace); }
  });
  window.__jarvisGraph = graph;   // handy from devtools while tuning layout

  // dial ticks
  var ticks = '';
  for (var i = 0; i < 60; i++) {
    var a = (i / 60) * Math.PI * 2, r1 = 92, r2 = i % 5 === 0 ? 82 : 87;
    ticks += '<line x1="' + (100 + Math.cos(a) * r1).toFixed(1) + '" y1="' + (100 + Math.sin(a) * r1).toFixed(1)
      + '" x2="' + (100 + Math.cos(a) * r2).toFixed(1) + '" y2="' + (100 + Math.sin(a) * r2).toFixed(1)
      + '" stroke="rgba(95,228,255,' + (i % 5 === 0 ? '.35' : '.15') + ')" stroke-width="1"/>';
  }
  $('#ticks').innerHTML = ticks;

  loadGraph(false);
  tickClock();
  setInterval(tickClock, 10000);
  loadDashboard();
  setTimeout(loadDashboard, 4000);   // first call only starts the server-side fetches
  setInterval(loadDashboard, 30000);
  var logPanel = $('#pLog');
  logPanel.classList.toggle('closed', localStorage.getItem('jarvis.log') !== 'open');
  logPanel.querySelector('h2').onclick = function () {
    logPanel.classList.toggle('closed');
    localStorage.setItem('jarvis.log', logPanel.classList.contains('closed') ? 'closed' : 'open');
  };
  setupMic();
  setupDevices();
  setVoiceState('IDLE', '');
  connectEvents();
  setInterval(Timers.render, 1000);
  bootSequence();

  // Stop: only lit while he is thinking or talking; cuts both off.
  $('#btnStop').onclick = function () {
    cancelRun();
    briefingActive = false;
    setVoiceState(Live.on ? 'LISTENING' : 'IDLE', Live.on ? 'hot' : '');
    log('note', 'VOICE', 'per Knopf abgebrochen');
  };
  setInterval(function () {
    $('#btnStop').disabled = !(running || stopSpeaking || Speech.active);
  }, 200);

  $('#btnSend').onclick = function () { var v = $('#ask').value; $('#ask').value = ''; transmit(v, { typed: true }); };
  $('#ask').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { var v = $('#ask').value; $('#ask').value = ''; transmit(v, { typed: true }); }
  });
  $('#btnFit').onclick = function () { graph.fit(); };
  $('#btnLabels').onclick = function () {
    graph.showLabels = !graph.showLabels;
    $('#btnLabels').classList.toggle('on', graph.showLabels);
  };
  $('#btnDim').onclick = function () {
    graph.dim = !graph.dim;
    $('#btnDim').classList.toggle('on', graph.dim);
  };
  $('#btnLabels').classList.add('on');

  document.addEventListener('keydown', function (e) {
    if (e.key === ' ' && document.activeElement !== $('#ask') && interruptSpeech()) {
      e.preventDefault();
      return;
    }
    if (e.key === 'Escape') {
      cancelRun();
      graph.setFocus(null);
    }
    if (e.key === '/' && document.activeElement !== $('#ask')) { e.preventDefault(); $('#ask').focus(); }
  });

  setInterval(loadStatus, 20000);
  loadLive();
  setInterval(loadLive, 30000);
  log('ok', 'BOOT', 'HUD online · claude code · local vault');
});
