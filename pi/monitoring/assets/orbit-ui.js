/* ORBIT UI v4.4 – gemeinsame Bausteine für ORBIT MONITOR (Port 8092) und ORBIT ATLAS (Port 8094).
   Klassisches Skript (kein Modul): monitor.js bzw. atlas.js nutzen die Funktionen direkt.
   Keine externen Abhängigkeiten – die Seiten müssen auch ohne Internet und ohne DNS funktionieren.
   Diagramme folgen den dataviz-Regeln: eine Achse, 2px-Linien, Fadenkreuz + Tooltip, Tabellenansicht,
   ein gemeinsamer Zeitraumfilter je Seite, Farben aus der validierten Kategorie-Reihe (--series-1 …). */
'use strict';

const PI = '10.0.0.110';
const MON_URL = `http://${PI}:8092/`, DOC_URL = `http://${PI}:8094/`;

/* ------------------------------------------------------------------ Werkzeuge */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = v => (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) ? null : Number(v);
const fmt = (v, d = 1) => num(v) === null ? '–' : Number(v).toLocaleString('de-DE', { maximumFractionDigits: d });
const fmtBig = v => num(v) === null ? '–' : Math.abs(v) >= 1e6 ? Number(v).toLocaleString('de-DE', { notation: 'compact', maximumFractionDigits: 1 }) : fmt(v, 0);
const pad = n => String(n).padStart(2, '0');
const hhmm = t => { const d = new Date(t); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const dDE = s => { const m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}:\d{2}))?/); return m ? `${m[3]}.${m[2]}.${m[1]}${m[4] ? ' ' + m[4] : ''}` : (s || '–'); };
const dur = s => { s = num(s); if (s === null) return '–'; const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`; };
const ago = iso => { if (!iso) return '–'; const s = (Date.now() - new Date(iso)) / 1000; if (s < 90) return 'gerade eben'; if (s < 3600) return `vor ${Math.round(s / 60)} min`; if (s < 86400) return `vor ${Math.round(s / 3600)} h`; return `vor ${Math.round(s / 86400)} Tagen`; };
const macs = s => (String(s || '').match(/([0-9a-f]{2}:){5}[0-9a-f]{2}/gi) || []).map(m => m.toLowerCase());
const inline = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
const setText = (id, t) => { const e = document.getElementById(id); if (e) e.textContent = t; };
const setHTML = (id, h) => { const e = document.getElementById(id); if (e) e.innerHTML = h; };
const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* privat/gesperrt */ } } };

/* ------------------------------------------------------------------ Symbole (24×24, Strich) */
const ICONS = {
  home: 'M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  dashboard: 'M3 3h8v8H3zM13 3h8v5h-8zM13 10h8v11h-8zM3 13h8v8H3z',
  alert: 'M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01',
  history: 'M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2',
  network: 'M9 2h6v5H9zM3 17h6v5H3zM15 17h6v5h-6zM12 7v5M6 17v-2.5h12V17',
  devices: 'M3 4h13v10H3zM7 18h5M9.5 14v4M17.5 8H21v12h-3.5z',
  globe: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9M12 3c-2.5 2.7-3.8 5.7-3.8 9s1.3 6.3 3.8 9',
  cpu: 'M6 6h12v12H6zM9.5 9.5h5v5h-5zM9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4',
  server: 'M3 4h18v7H3zM3 13h18v7H3zM7 7.5h.01M7 16.5h.01M11 7.5h6M11 16.5h6',
  layers: 'M12 2 2 7l10 5 10-5zM2 17l10 5 10-5M2 12l10 5 10-5',
  activity: 'M22 12h-4l-3 9L9 3l-3 9H2',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10zM9 12l2 2 4-4',
  archive: 'M3 4h18v4H3zM5 8v12h14V8M10 12h4',
  bolt: 'M13 2 3 14h9l-1 8 10-12h-9z',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
  moon: 'M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z',
  battery: 'M3 7h15a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1zM22 11v2M6 10v4M9.5 10v4',
  sparkles: 'M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9zM19 16l.7 1.8 1.8.7-1.8.7L19 21l-.7-1.8-1.8-.7 1.8-.7z',
  mic: 'M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3zM19 10v1a7 7 0 0 1-14 0v-1M12 18v4',
  book: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5zM4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5',
  terminal: 'M3 4h18v16H3zM7 9l3 3-3 3M13 15h4',
  list: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  folder: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  calendar: 'M3 5h18v16H3zM3 10h18M8 3v4M16 3v4',
  timeline: 'M12 2v20M16 6h5M3 12h5M16 18h5M12 6h4M8 12h4M12 18h4',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-4.3-4.3',
  ext: 'M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5',
  right: 'M5 12h14M13 6l6 6-6 6',
  left: 'M19 12H5M11 18l-6-6 6-6',
  chevron: 'M9 6l6 6-6 6',
  check: 'M20 6 9 17l-5-5',
  checkCircle: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM8 12.5l2.8 2.8L16.5 9.5',
  xCircle: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM15 9l-6 6M9 9l6 6',
  alertCircle: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 8v4.5M12 16h.01',
  info: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 11v5M12 8h.01',
  wifi: 'M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M2 9a15 15 0 0 1 20 0M12 20h.01',
  plug: 'M9 2v5M15 2v5M6 7h12v4a6 6 0 0 1-12 0zM12 17v5',
  thermo: 'M14 14.8V4a2 2 0 0 0-4 0v10.8a4 4 0 1 0 4 0zM12 9v7',
  drop: 'M12 2.7S6 9.3 6 14a6 6 0 0 0 12 0c0-4.7-6-11.3-6-11.3z',
  flame: 'M12 22a7 7 0 0 0 7-7c0-4-3-6-4-9-1 3-3 4-4 4 0-2-1-4-2-5-1 4-4 6-4 10a7 7 0 0 0 7 7z',
  bulb: 'M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.3h6c0-1 .4-1.8 1-2.3A7 7 0 0 0 12 2z',
  tv: 'M3 5h18v12H3zM8 21h8M12 17v4',
  phone: 'M7 2h10a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zM11 18h2',
  tablet: 'M5 2h14a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zM11 18h2',
  laptop: 'M4 5h16v11H4zM2 19h20',
  monitor: 'M3 4h18v12H3zM8 20h8M12 16v4',
  router: 'M3 13h18v7H3zM7 16.5h.01M10.5 16.5h.01M15 16.5h3M7 13V8M17 13V8M4.5 5.5A7 7 0 0 1 9 3.5M19.5 5.5A7 7 0 0 0 15 3.5',
  hdd: 'M3 4h18v16H3zM3 12h18M7 8h.01M7 16h.01M11 8h6M11 16h6',
  cloud: 'M7 18a4.5 4.5 0 0 1-.7-8.9 6 6 0 0 1 11.6 1.4A3.75 3.75 0 0 1 17.5 18z',
  lock: 'M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4',
  gamepad: 'M6 12h4M8 10v4M15 13h.01M18 11h.01M17 6H7a5 5 0 0 0-5 5v1.5a3.5 3.5 0 0 0 6.4 2L9.5 13h5l1.1 1.5a3.5 3.5 0 0 0 6.4-2V11a5 5 0 0 0-5-5z',
  printer: 'M6 9V3h12v6M6 18H4a1 1 0 0 1-1-1v-6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v6a1 1 0 0 1-1 1h-2M6 14h12v7H6z',
  fan: 'M12 12a2 2 0 1 0 0 .01M12 10c0-4 .6-7 3.5-7 2.5 0 3 3-.5 5.5M14 12c4 0 7 .6 7 3.5 0 2.5-3 3-5.5-.5M12 14c0 4-.6 7-3.5 7-2.5 0-3-3 .5-5.5M10 12c-4 0-7-.6-7-3.5 0-2.5 3-3 5.5.5',
  speaker: 'M6 3h12v18H6zM12 7h.01M12 15.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z',
  help: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM9.2 9a3 3 0 0 1 5.6 1c0 2-2.8 2.6-2.8 4M12 17h.01',
  database: 'M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
  copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
  download: 'M12 3v12M7 10l5 5 5-5M4 21h16',
  menu: 'M4 6h16M4 12h16M4 18h16',
  x: 'M18 6 6 18M6 6l12 12',
  table: 'M3 4h18v16H3zM3 10h18M3 15h18M9 4v16',
  chart: 'M3 3v18h18M7 14l4-4 3 3 6-7',
  gear: 'M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7zM12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0',
  clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2',
  link: 'M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1',
  tag: 'M3 3h8l10 10-8 8L3 11zM7.5 7.5h.01',
  pin: 'M12 22s7-6.2 7-12a7 7 0 1 0-14 0c0 5.8 7 12 7 12zM12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z',
  grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  package: 'M21 8 12 3 3 8v8l9 5 9-5zM3 8l9 5 9-5M12 13v8',
  wrench: 'M14.7 6.3a4 4 0 0 0 5 5L11 20a2.1 2.1 0 0 1-3-3l8.7-8.7a4 4 0 0 0-2-2zM14.7 6.3 17.5 3.5',
  bell: 'M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0',
  radar: 'M12 3a9 9 0 1 0 9 9M12 7a5 5 0 1 0 5 5M12 12l7-7',
  key: 'M15 7a4 4 0 1 1-3.9 4.9L3 20v-3h3v-3h3l1.1-1.1A4 4 0 0 1 15 7zM16 10h.01',
  bug: 'M8 10a4 4 0 0 1 8 0v4a4 4 0 0 1-8 0zM12 10v8M4 13h4M16 13h4M5 8l3 2M19 8l-3 2M5 19l3-2M19 19l-3-2M10 6L9 3M14 6l1-3',
  play: 'M7 4.5l12 7.5-12 7.5z',
};
const icon = (n, cls = '') => `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[n] || ICONS.help}"/></svg>`;
const svgIcon = (n, x, y, size = 24) => `<g class="ico" transform="translate(${x - size / 2} ${y - size / 2}) scale(${size / 24})"><path d="${ICONS[n] || ICONS.help}"/></g>`;

/* ------------------------------------------------------------------ Live-Daten (Prometheus über den Nginx-Leseproxy der jeweiligen Seite) */
const S = { hosts: new Map(), hostsLoaded: false, ok: {} };
const _cache = new Map();
function getJSON(url, ttl = 12000) {
  const c = _cache.get(url);
  if (c && Date.now() - c.t < ttl) return c.p;
  const p = fetch(url, { cache: 'no-store' }).then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
  _cache.set(url, { t: Date.now(), p });
  p.catch(() => _cache.delete(url));
  return p;
}
const clearCache = () => _cache.clear();
async function prom(q) { try { const j = await getJSON('api/query?query=' + encodeURIComponent(q)); S.ok.prom = true; return j.data.result; } catch (e) { S.ok.prom = false; return []; } }
async function val(q) { const r = await prom(q); return r.length ? num(r[0].value[1]) : null; }
async function byLabel(q, label = 'entity') { const r = await prom(q); const m = {}; r.forEach(x => { m[x.metric[label]] = num(x.value[1]); }); return m; }
async function range(q, hours) {
  const step = Math.max(30, Math.round(hours * 3600 / 240)), end = Math.floor(Date.now() / 1000 / step) * step;
  try {
    const j = await getJSON(`api/query_range?query=${encodeURIComponent(q)}&start=${end - hours * 3600}&end=${end}&step=${step}`, 25000);
    return j.data.result.map(s => ({ metric: s.metric, pts: s.values.map(([t, v]) => [t * 1000, num(v)]) }));
  } catch (e) { return []; }
}
async function loadHosts() {
  const r = await prom('gateway_host_active');
  if (!r.length) return false;
  const m = new Map();
  r.forEach(x => { const l = x.metric; if (l.macaddress) m.set(l.macaddress.toLowerCase(), { active: x.value[1] === '1', ip: l.ipaddress, iface: l.interfacetype || '', name: l.hostname }); });
  S.hosts = m; S.hostsLoaded = true;
  return true;
}
function devState(d) { for (const m of macs(d.mac)) { const h = S.hosts.get(m); if (h) return h; } return null; }

/* ------------------------------------------------------------------ Bausteine (liefern HTML-Text) */
const pill = (text, tone = '', ico = '') => `<span class="pill ${tone}">${ico ? icon(ico) : '<i class="d"></i>'}<span>${esc(text)}</span></span>`;
const tag = (text, cls = '') => `<span class="tag ${cls}">${esc(text)}</span>`;
const monoChip = t => `<span class="mono-chip">${esc(t)}</span>`;
function btn(label, href, { kind = '', ico = '', same = false, attrs = '' } = {}) {
  const ext = /^https?:/.test(href) && !same;
  return `<a class="btn ${kind}" href="${esc(href)}"${ext ? ' target="_blank" rel="noopener"' : ''} ${attrs}>${ico ? icon(ico) : ''}<span>${esc(label)}</span>${ext ? icon('ext', 'trail') : ''}</a>`;
}
const card = (title, body, { ico = '', aside = '', cls = '', id = '', bodyCls = '' } = {}) =>
  `<div class="card ${cls}"${id ? ` id="${id}"` : ''}>${title ? `<div class="card-h">${ico ? `<span class="ico">${icon(ico)}</span>` : ''}<h3>${title}</h3>${aside ? `<div class="aside">${aside}</div>` : ''}</div>` : ''}<div class="card-b ${bodyCls}">${body}</div></div>`;
const kv = rows => `<dl class="kv">${rows.filter(r => r && r[1] !== undefined && r[1] !== null && r[1] !== '').map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('')}</dl>`;
const head = ({ kicker = '', title = '', text = '', actions = '', ico = '', back = null }) =>
  `${back ? `<a class="back" href="${esc(back[0])}">${icon('left')}<span>${esc(back[1])}</span></a>` : ''}<header class="page-head"><div class="ph-main">${ico ? `<span class="ph-ico">${icon(ico)}</span>` : ''}<div>${kicker ? `<div class="kicker">${kicker}</div>` : ''}<h1>${title}</h1>${text ? `<p>${text}</p>` : ''}</div></div>${actions ? `<div class="actions">${actions}</div>` : ''}</header>`;
const section = (title, body, { sub = '', right = '', id = '', cls = '' } = {}) =>
  `<section class="sec ${cls}"${id ? ` id="${id}"` : ''}><div class="sec-h"><h2>${title}</h2>${sub ? `<span class="sub">${sub}</span>` : ''}${right ? `<div class="right">${right}</div>` : ''}</div>${body}</section>`;
const note = (html, tone = '', ico = '') => `<div class="note ${tone}">${icon(ico || (tone === 'bad' ? 'alertCircle' : tone === 'warn' ? 'alert' : 'info'))}<div>${html}</div></div>`;
const emptyMsg = text => `<div class="empty">${icon('info')}<span>${esc(text)}</span></div>`;
const bullets = items => items && items.length ? `<ul class="bullets">${items.map(x => `<li>${inline(x)}</li>`).join('')}</ul>` : '<p class="muted small">–</p>';
const cmd = text => `<div class="cmd"><code>${esc(text)}</code><button class="copy" type="button" data-copy="${esc(text)}" aria-label="Befehl kopieren" title="Kopieren">${icon('copy')}</button></div>`;
const isCmd = s => /^(sudo |systemctl |journalctl |ls |mountpoint |ping |nslookup |docker |bash |python3? |cat |tail )/.test(s);
function stepHTML(s) { // „Beschriftung: befehl“ und „befehl (Anmerkung)“ / „befehl – Anmerkung“ trennen, damit nur der Befehl kopiert wird
  let label = '', note = '', m = s.match(/^([^:]{2,40}):\s+(.+)$/);
  if (m && isCmd(m[2])) { label = m[1]; s = m[2]; }
  if (!isCmd(s)) return inline(label ? `${label}: ${s}` : s);
  if ((m = s.match(/^(.+?)\s+(\([^()]+\)|– .+)$/))) { s = m[1]; note = m[2].replace(/^– /, ''); }
  return (label ? `<div class="step-l">${inline(label)}</div>` : '') + cmd(s) + (note ? `<div class="step-n">${inline(note)}</div>` : '');
}
const steps = items => `<ol class="steps">${items.map(s => `<li>${stepHTML(s)}</li>`).join('')}</ol>`;
const searchField = (id, placeholder, value = '') => `<label class="field-wrap">${icon('search')}<input class="field" id="${id}" type="search" placeholder="${esc(placeholder)}" value="${esc(value)}" autocomplete="off" aria-label="${esc(placeholder)}"></label>`;
const segCtl = (id, opts, cur) => `<div class="seg" id="${id}" role="group">${opts.map(([v, l, ic]) => `<button type="button" data-v="${esc(v)}" class="${v === cur ? 'on' : ''}" aria-pressed="${v === cur}">${ic ? icon(ic) : ''}${esc(l)}</button>`).join('')}</div>`;
const chipRow = (id, opts, cur) => `<div class="chips-row" id="${id}" style="display:flex;flex-wrap:wrap;gap:6px">${opts.map(([v, l, n, ic]) => `<button type="button" class="chip ${v === cur ? 'on' : ''}" data-v="${esc(v)}" aria-pressed="${v === cur}">${ic ? icon(ic) : ''}${esc(l)}${n !== undefined ? `<span class="n">${n}</span>` : ''}</button>`).join('')}</div>`;
function wireSel(id, onPick) { // Auswahl in segCtl/chipRow verdrahten
  const box = document.getElementById(id); if (!box) return;
  box.addEventListener('click', e => {
    const b = e.target.closest('[data-v]'); if (!b) return;
    $$('[data-v]', box).forEach(x => { const on = x === b; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on); });
    onPick(b.dataset.v);
  });
}

/* Kennzahl-Kachel: Beschriftung, Wert (proportionale Ziffern), optional Messbalken oder Sparkline */
function tile(id, { label, ico = '', unit = '', sub = '', meter = false, spark = false, href = '' }) {
  const t = href ? 'a' : 'div';
  return `<${t} class="card tile${href ? ' link' : ''}" id="t-${id}"${href ? ` href="${esc(href)}"` : ''}>
    <div class="tile-top">${ico ? icon(ico) : ''}<span class="tile-l">${esc(label)}</span><span class="tile-flag" id="tf-${id}"></span></div>
    <div class="tile-row"><div class="tile-val"><span class="v" id="tv-${id}">–</span>${unit ? `<span class="u">${esc(unit)}</span>` : ''}</div>${spark ? `<svg class="spark" id="ts-${id}" aria-hidden="true"></svg>` : ''}</div>
    ${meter ? `<div class="meter" id="tm-${id}" role="meter" aria-valuemin="0" aria-valuemax="100"><i></i></div>` : ''}
    <div class="tile-sub" id="tsub-${id}">${sub}</div></${t}>`;
}
function setTile(id, o = {}) {
  if (o.v !== undefined) setText('tv-' + id, o.v);
  if (o.sub !== undefined) setHTML('tsub-' + id, o.sub);
  if (o.flag !== undefined) setHTML('tf-' + id, o.flag ? pill(o.flag[0], o.flag[1]) : '');
  if (o.meter !== undefined) setMeter('tm-' + id, o.meter, o.tone || '');
}
function setMeter(id, pct, tone = '') {
  const e = typeof id === 'string' ? document.getElementById(id) : id; if (!e) return;
  const p = Math.max(0, Math.min(100, num(pct) || 0));
  e.className = 'meter ' + tone;
  e.setAttribute('aria-valuenow', Math.round(p));
  e.firstElementChild.style.transform = `translateX(${(p - 100).toFixed(2)}%)`;
}
const meterHTML = (pct, tone = '') => `<div class="meter ${tone}" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(num(pct) || 0)}"><i style="transform:translateX(${(Math.max(0, Math.min(100, num(pct) || 0)) - 100).toFixed(2)}%)"></i></div>`;
const toneFor = (v, warn, bad) => v === null ? '' : v >= bad ? 'bad' : v >= warn ? 'warn' : '';

function downsample(P, n) {
  if (P.length <= n) return P;
  const out = [], step = P.length / n;
  for (let i = 0; i < n; i++) {
    const seg = P.slice(Math.floor(i * step), Math.max(Math.floor(i * step) + 1, Math.floor((i + 1) * step)));
    out.push([seg[seg.length - 1][0], seg.reduce((s, p) => s + p[1], 0) / seg.length]);
  }
  return out;
}
/* Sparkline: Verlauf im Zurücknahme-Ton, aktueller Punkt in der Akzentfarbe */
function spark(id, pts) {
  const el = document.getElementById(id); if (!el) return;
  const P = downsample((pts || []).filter(p => p[1] !== null), 30);
  if (P.length < 2) { el.innerHTML = ''; return; }
  const W = el.clientWidth || 96, H = 34, m = 4;
  let lo = Math.min(...P.map(p => p[1])), hi = Math.max(...P.map(p => p[1]));
  if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
  const t0 = P[0][0], t1 = P[P.length - 1][0];
  const x = t => m + (t - t0) / Math.max(1, t1 - t0) * (W - 2 * m), y = v => m + (1 - (v - lo) / (hi - lo)) * (H - 2 * m);
  const d = P.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}`).join('');
  const L = P[P.length - 1];
  el.setAttribute('viewBox', `0 0 ${W} ${H}`);
  el.innerHTML = `<path class="sp-a" d="${d}L${x(L[0]).toFixed(1)} ${H}L${x(t0).toFixed(1)} ${H}Z"/><path class="sp-l" d="${d}"/><circle class="sp-p" cx="${x(L[0]).toFixed(1)}" cy="${y(L[1]).toFixed(1)}" r="3.5"/>`;
}

/* ------------------------------------------------------------------ Diagramme: Linie/Fläche mit Fadenkreuz, Tooltip, Tastatur und Tabellenansicht */
const CH = { reg: new Map(), hours: [1, 6, 24, 168].includes(+store.get('orbit-range')) ? +store.get('orbit-range') : 24 };
const RANGES = [[1, '1 h'], [6, '6 h'], [24, '24 h'], [168, '7 Tage']];
const rangeSeg = () => `<div class="seg" role="group" aria-label="Zeitraum">${RANGES.map(([h, l]) => `<button type="button" data-range="${h}" class="${h === CH.hours ? 'on' : ''}" aria-pressed="${h === CH.hours}">${l}</button>`).join('')}</div>`;
const rangeBar = (label = 'Zeitraum aller Verläufe auf dieser Seite') => `<div class="filters">${icon('clock')}<span class="f-label">${esc(label)}</span>${rangeSeg()}</div>`;

function chartCard(key, o) {
  CH.reg.set(key, Object.assign({ unit: '', digits: 1, yMin: 0, height: 220, data: null }, o));
  return `<div class="card chart-card">
    <div class="card-h">${o.ico ? `<span class="ico">${icon(o.ico)}</span>` : ''}<h3>${esc(o.title)}</h3>
      <div class="aside"><span class="now" id="cn-${key}"></span><button type="button" class="btn sm ghost" data-tview="${key}" aria-pressed="false" title="Werte als Tabelle anzeigen">${icon('table')}<span>Tabelle</span></button></div></div>
    <div class="card-b"><div class="chart" id="ch-${key}" tabindex="0" role="img" aria-label="${esc(o.title)} – mit den Pfeiltasten durch die Messpunkte gehen"><div class="chart-msg">Lade Verlauf …</div></div>
      <div class="legend" id="lg-${key}"></div><div class="table-wrap" id="tb-${key}" hidden></div>${o.foot ? `<p class="chart-foot">${o.foot}</p>` : ''}</div></div>`;
}
async function loadCharts() {
  await Promise.all([...CH.reg.entries()].map(async ([key, c]) => {
    const el = document.getElementById('ch-' + key); if (!el) return;
    if (c.static) { c.data = c.static; renderChart(key); return; } // feste Reihen (z. B. Verlauf aus einem Bericht)
    if (c.data) el.classList.add('loading'); // Neu laden behält das alte Bild (gedimmt) – kein Flackern
    const res = await Promise.all(c.series.map(s => range(s.q, CH.hours)));
    const out = [];
    res.forEach((r, i) => r.forEach((s, j) => out.push({
      name: c.series[i].label ? (s.metric[c.series[i].label] || `Reihe ${j + 1}`) : (c.series[i].name || `Reihe ${j + 1}`),
      slot: c.series[i].slot || out.length + 1, pts: s.pts,
    })));
    if (CH.reg.get(key) !== c) return; // Seite inzwischen gewechselt
    c.data = out;
    el.classList.remove('loading');
    renderChart(key);
  }));
}
function niceStep(span, n) { const raw = span / n, mag = 10 ** Math.floor(Math.log10(raw)), f = raw / mag; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * mag; }
const tAxis = (t, long) => long ? new Date(t).toLocaleDateString('de-DE', { weekday: 'short', day: 'numeric', month: 'numeric' }) : hhmm(t);
const tTip = (t, long) => long ? new Date(t).toLocaleString('de-DE', { weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : `${new Date(t).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' })} · ${hhmm(t)}`;
function valueAt(pts, t) {
  if (!pts.length) return null;
  let lo = 0, hi = pts.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (pts[mid][0] < t) lo = mid; else hi = mid; }
  const p = Math.abs(pts[lo][0] - t) <= Math.abs(pts[hi][0] - t) ? pts[lo] : pts[hi];
  return p ? p[1] : null;
}
const _ro = typeof ResizeObserver === 'function' ? new ResizeObserver(entries => {
  for (const e of entries) {
    const key = e.target.id.slice(3), w = Math.round(e.contentRect.width);
    if (CH.reg.has(key) && CH.reg.get(key).data && Math.abs((e.target._w || 0) - w) > 6) requestAnimationFrame(() => renderChart(key));
  }
}) : null;

function renderChart(key) {
  const c = CH.reg.get(key), el = document.getElementById('ch-' + key); if (!c || !el) return;
  const lg = document.getElementById('lg-' + key), now = document.getElementById('cn-' + key), tb = document.getElementById('tb-' + key);
  const series = (c.data || []).filter(s => s.pts.some(p => p[1] !== null));
  if (!series.length) { el.innerHTML = '<div class="chart-msg">Keine Messwerte im gewählten Zeitraum.</div>'; if (lg) lg.innerHTML = ''; if (now) now.textContent = ''; if (tb) tb.innerHTML = ''; el._g = null; return; }
  const W = Math.max(260, Math.round(el.clientWidth || 600)), H = c.height, L = 48, R = 14, T = 12, B = 28;
  el._w = W;
  const all = series.flatMap(s => s.pts.filter(p => p[1] !== null));
  const t0 = Math.min(...all.map(p => p[0])), t1 = Math.max(...all.map(p => p[0]));
  const vmax = Math.max(...all.map(p => p[1])), vmin = Math.min(...all.map(p => p[1]));
  let y0, y1, step;
  if (c.yMin === null) {
    step = niceStep(Math.max(vmax - vmin, Math.abs(vmax) * .02, 1e-6), 4);
    y0 = Math.floor(vmin / step) * step; y1 = Math.ceil(vmax / step) * step;
    if (y1 - y0 < step * 2) y1 = y0 + step * 2;
  } else {
    y0 = Math.min(c.yMin, vmin);
    step = niceStep(Math.max(vmax - y0, 1e-6), 4);
    y1 = y0 + Math.max(1, Math.ceil((vmax - y0) / step)) * step;
  }
  const tickDigits = step < .1 ? 2 : step < 1 ? 1 : 0;
  const x = t => L + (t1 === t0 ? .5 : (t - t0) / (t1 - t0)) * (W - L - R);
  const y = v => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
  const long = t1 - t0 > 36 * 3600e3;
  let g = '';
  for (let v = y0, i = 0; v <= y1 + step / 2 && i < 12; v += step, i++) {
    const yy = Math.round(y(v)) + .5;
    g += `<line class="${i === 0 ? 'bl' : 'gl'}" x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}"/><text x="${L - 8}" y="${yy + 4}" text-anchor="end">${fmt(v, tickDigits)}</text>`;
  }
  const nx = W < 440 ? 3 : 5;
  for (let i = 0; i < nx; i++) { const t = t0 + (t1 - t0) * i / (nx - 1); g += `<text x="${x(t).toFixed(1)}" y="${H - 8}" text-anchor="${i === 0 ? 'start' : i === nx - 1 ? 'end' : 'middle'}">${tAxis(t, long)}</text>`; }
  const single = series.length === 1;
  const paths = series.map(s => {
    const col = `var(--series-${s.slot})`, segs = []; let cur = [];
    s.pts.forEach(p => { if (p[1] === null) { if (cur.length) segs.push(cur); cur = []; } else cur.push(p); });
    if (cur.length) segs.push(cur);
    const line = segs.map(sg => sg.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}`).join('')).join('');
    const area = single ? segs.map(sg => sg.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}`).join('') + `L${x(sg[sg.length - 1][0]).toFixed(1)} ${y(y0).toFixed(1)}L${x(sg[0][0]).toFixed(1)} ${y(y0).toFixed(1)}Z`).join('') : '';
    return `${area ? `<path class="ar" d="${area}" style="fill:${col}"/>` : ''}<path class="ln" d="${line}" style="stroke:${col}"/>`;
  }).join('');
  const base = series.reduce((a, b) => b.pts.length > a.pts.length ? b : a);
  el.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">${g}${paths}<g class="xh-g" style="display:none"><line class="xh" y1="${T}" y2="${H - B}"/>${series.map(s => `<circle class="pt" r="4" style="fill:var(--series-${s.slot})"/>`).join('')}</g></svg><div class="tip" hidden></div>`;
  el._g = { x, y, base, series, W, T, L, R, long, unit: c.unit, digits: c.digits, idx: el._g ? el._g.idx : base.pts.length - 1 };
  if (el._g.idx >= base.pts.length) el._g.idx = base.pts.length - 1;
  wireChart(el);
  if (_ro && !el._obs) { _ro.observe(el); el._obs = true; }
  const lastOf = s => [...s.pts].reverse().find(p => p[1] !== null);
  if (lg) lg.innerHTML = single ? '' : series.map(s => { const lv = lastOf(s); return `<span class="lg-i"><i class="key" style="background:var(--series-${s.slot})"></i><span>${esc(s.name)}</span><b>${lv ? fmt(lv[1], c.digits) : '–'}</b>${c.unit ? `<span class="muted">${esc(c.unit)}</span>` : ''}</span>`; }).join('');
  if (now) { const lv = lastOf(series[0]); now.textContent = single && lv ? `aktuell ${fmt(lv[1], c.digits)}${c.unit ? ' ' + c.unit : ''}` : ''; }
  if (tb) tb.innerHTML = chartTable(series, c, long);
}
function wireChart(el) {
  if (el._wired) return;
  el._wired = true;
  const show = idx => {
    const g = el._g; if (!g) return;
    const P = g.base.pts; idx = Math.max(0, Math.min(P.length - 1, idx)); g.idx = idx;
    const t = P[idx][0], xx = g.x(t), grp = el.querySelector('.xh-g'), tip = el.querySelector('.tip');
    if (!grp || !tip) return;
    grp.style.display = '';
    const ln = grp.querySelector('.xh'); ln.setAttribute('x1', xx); ln.setAttribute('x2', xx);
    const dots = grp.querySelectorAll('.pt');
    tip.replaceChildren();
    const hd = document.createElement('div'); hd.className = 'tt'; hd.textContent = tTip(t, g.long); tip.append(hd);
    g.series.forEach((s, i) => {
      const v = valueAt(s.pts, t), dot = dots[i];
      if (v === null) dot.style.display = 'none'; else { dot.style.display = ''; dot.setAttribute('cx', xx); dot.setAttribute('cy', g.y(v)); }
      const row = document.createElement('div'); row.className = 'tr';
      const k = document.createElement('i'); k.className = 'key'; k.style.background = `var(--series-${s.slot})`;
      const b = document.createElement('b'); b.textContent = v === null ? '–' : `${fmt(v, g.digits)}${g.unit ? ' ' + g.unit : ''}`;
      const n = document.createElement('span'); n.className = 'nm'; n.textContent = s.name;
      row.append(k, b, n); tip.append(row);
    });
    tip.hidden = false;
    const tw = tip.offsetWidth; let left = xx + 14;
    if (left + tw > g.W - 2) left = xx - tw - 14;
    tip.style.left = Math.max(0, left) + 'px'; tip.style.top = g.T + 'px';
  };
  const hide = () => { const grp = el.querySelector('.xh-g'), tip = el.querySelector('.tip'); if (grp) grp.style.display = 'none'; if (tip) tip.hidden = true; };
  el.addEventListener('pointermove', e => {
    const g = el._g; if (!g) return;
    const px = e.clientX - el.getBoundingClientRect().left;
    if (px < g.L - 8 || px > g.W - g.R + 8) { hide(); return; }
    const P = g.base.pts; let lo = 0, hi = P.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (g.x(P[mid][0]) < px) lo = mid; else hi = mid; }
    show(Math.abs(g.x(P[lo][0]) - px) <= Math.abs(g.x(P[hi][0]) - px) ? lo : hi);
  });
  el.addEventListener('pointerleave', hide);
  el.addEventListener('focus', () => show(el._g ? el._g.idx : 0));
  el.addEventListener('blur', hide);
  el.addEventListener('keydown', e => {
    if (!el._g) return;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); show(el._g.idx + (e.key === 'ArrowLeft' ? -1 : 1) * (e.shiftKey ? 10 : 1)); }
    else if (e.key === 'Escape') hide();
  });
}
function chartTable(series, c, long) {
  const base = series.reduce((a, b) => b.pts.length > a.pts.length ? b : a), n = base.pts.length, every = Math.max(1, Math.ceil(n / 24)), rows = [];
  for (let i = n - 1; i >= 0; i -= every) rows.push(base.pts[i][0]);
  return `<table class="t"><thead><tr><th>Zeitpunkt</th>${series.map(s => `<th class="num">${esc(s.name)}${c.unit ? ` (${esc(c.unit)})` : ''}</th>`).join('')}</tr></thead><tbody>${rows.map(t => `<tr><td class="nowrap">${tTip(t, long)}</td>${series.map(s => `<td class="num">${fmt(valueAt(s.pts, t), c.digits)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

/* Balkenliste (eine Reihe = eine Farbe) und gestapelter 100-%-Balken für Anteile */
function bars(items, { unit = '', digits = 0, slot = 1, max = null } = {}) {
  const m = max ?? (Math.max(0, ...items.map(i => num(i.value) || 0)) || 1);
  return `<div class="bars">${items.map(it => {
    const v = num(it.value), label = `${it.label}: ${v === null ? 'kein Messwert' : fmt(v, digits) + (unit ? ' ' + unit : '')}`;
    return `<div class="bar-row" tabindex="0" data-tip="${esc(label)}"><div class="lbl"><div class="nm">${esc(it.label)}</div>${it.sub ? `<div class="sub">${esc(it.sub)}</div>` : ''}</div><div class="trk">${v === null ? '' : `<span class="fill" style="--p:${Math.max(0, v) / m};background:var(--series-${it.slot || slot})"></span>`}<span class="val${v === null ? ' off' : ''}">${v === null ? '–' : fmt(v, digits) + (unit ? ' ' + esc(unit) : '')}</span></div></div>`;
  }).join('')}</div>`;
}
function stackBar(parts, { digits = 1 } = {}) {
  const tot = parts.reduce((a, p) => a + (num(p.value) || 0), 0) || 1;
  const pct = p => (num(p.value) || 0) / tot * 100;
  return `<div class="stackbar" role="img" aria-label="${esc(parts.map(p => `${p.label} ${fmt(pct(p), digits)} %`).join(', '))}">${parts.filter(p => pct(p) > 0).map((p, i) => `<span style="flex-basis:${pct(p)}%;flex-grow:0;background:var(--series-${p.slot || i + 1})" tabindex="0" data-tip="${esc(`${p.label}: ${fmt(pct(p), digits)} % (${fmt(p.value, 0)})`)}"></span>`).join('')}</div>
    <div class="legend">${parts.map((p, i) => `<span class="lg-i"><i class="sw" style="background:var(--series-${p.slot || i + 1})"></i><span>${esc(p.label)}</span><b>${fmt(pct(p), digits)} %</b></span>`).join('')}</div>`;
}

/* ------------------------------------------------------------------ Geräte (gemeinsam für beide Seiten) */
const GROUP_ICON = { 'Infrastruktur': 'server', 'Computer': 'monitor', 'Energie': 'battery', 'Steckdosen': 'plug', 'Klima & Sensoren': 'thermo', 'Licht': 'bulb', 'Medien': 'tv', 'Mobil': 'phone', 'Zuordnen': 'help' };
const DEVICE_ICON = { router: 'router', repeater: 'wifi', raspi: 'cpu', synology: 'hdd', pc: 'monitor', growatt: 'battery', inverter: 'sun', tv: 'tv', bambu: 'printer', 'echo-dot': 'speaker', levoit: 'fan', vpn: 'lock', iphone: 'phone', galaxy: 'phone', ipad: 'tablet', macbook: 'laptop', lap05: 'laptop', lap08: 'laptop', 'laptop-01': 'laptop', 'geraet-l': 'laptop', 'smart-hub': 'radar', 'bt-sz': 'flame', epson: 'printer', garmin: 'clock' };
const devId = d => d.id || d.slug;
const devIcon = d => DEVICE_ICON[devId(d)] || GROUP_ICON[d.group] || 'devices';
const firstIp = d => { const h = devState(d); if (h?.ip) return h.ip; const m = String(d.ip || '').match(/192\.168\.\d{1,3}\.\d+/); return m ? m[0] : (d.ip || ''); };
function connLabel(d) {
  const h = devState(d);
  if (h?.iface === 'Ethernet') return 'LAN';
  if (h?.iface === '802.11') return 'WLAN';
  return { funk: 'Funk (Hub)', lan: 'LAN', vpn: 'VPN', virtuell: 'virtuell', wlan: 'WLAN' }[d.conn] || 'WLAN';
}
function statusOf(d) {
  const h = devState(d);
  if (h) return h.active ? { tone: 'ok', label: 'online' } : { tone: '', label: 'offline' };
  if (d.conn === 'funk') return { tone: 'accent', label: 'über Hub' };
  if (d.status === 'ausser-betrieb') return { tone: 'bad', label: 'außer Betrieb' };
  if (d.conn === 'virtuell') return { tone: 'accent', label: 'virtuell' };
  if (!S.hostsLoaded && d.scan !== undefined) return d.scan ? { tone: 'ok', label: 'im Nachtscan' } : { tone: '', label: 'nicht im Scan' };
  return { tone: '', label: 'kein Live-Status' };
}
const statusPill = d => { const s = statusOf(d); return pill(s.label, s.tone); };
const ipNum = s => { const m = String(s || '').match(/192\.168\.\d{1,3}\.(\d+)/); return m ? +m[1] : 999; };
function devCard(d, href) {
  const ip = firstIp(d), issues = (d.issues || []).length;
  return `<a class="card dev link" href="${href}"><span class="dev-i">${icon(devIcon(d))}</span><div class="dev-m"><div class="dev-n">${esc(d.name)}</div><div class="dev-r">${esc(d.role || d.model || d.group)}</div>
    <div class="dev-tags"><span data-st="${esc(devId(d))}">${statusPill(d)}</span>${ip ? monoChip(ip) : ''}${d.room ? tag(d.room) : ''}${issues ? `<span class="pill warn" title="${issues} Hinweis(e)">${icon('alert')}<span>${issues}</span></span>` : ''}</div></div></a>`;
}
function devTable(list, hrefFn) {
  return `<div class="table-wrap"><table class="t"><thead><tr><th>Gerät</th><th>IP</th><th>MAC</th><th>Raum</th><th>Verbindung</th><th>Status</th></tr></thead><tbody>${list.map(d => `<tr class="click" data-href="${esc(hrefFn(d))}" tabindex="0"><td class="wide"><div class="cell-ico"><span class="ci">${icon(devIcon(d))}</span><div><div class="nm">${esc(d.name)}${(d.issues || []).length ? ` <span class="pill warn" title="Hinweise">${icon('alert')}<span>${d.issues.length}</span></span>` : ''}</div><div class="ds">${esc(d.role || d.model || '')}</div></div></div></td><td class="mono nowrap">${esc(firstIp(d) || '–')}</td><td class="mono nowrap small">${esc((macs(d.mac)[0] || '–').toUpperCase())}</td><td class="nowrap">${esc(d.room || '–')}</td><td class="nowrap small">${connLabel(d)}</td><td>${statusPill(d)}</td></tr>`).join('')}</tbody></table></div>`;
}
function devFacts(d) {
  const h = devState(d);
  return kv([['Hersteller', esc(d.maker || '–')], ['Modell', esc(d.model || '–')], ['Firmware / OS', esc(d.fw || '–')], ['IP-Adresse', `<span class="mono">${esc(d.ip || '–')}</span>`],
    ['Live (FRITZ!Box)', h ? `${statusPill(d)} <span class="mono small">${esc(h.ip || '')}</span> · ${h.iface === 'Ethernet' ? 'LAN' : h.iface === '802.11' ? 'WLAN' : 'Schnittstelle unbekannt'} · „${esc(h.name)}“` : `<span class="muted">nicht in der Hostliste</span>`],
    ['Hostname', `<span class="mono">${esc(d.host || '–')}</span>`], ['MAC', `<span class="mono">${esc(d.mac || '–')}</span>`], ['Raum', esc(d.room || '–')],
    ['Anbindung', esc({ lan: 'Ethernet', wlan: 'WLAN', funk: 'Funk über Tapo-Hub KH100', vpn: 'VPN', virtuell: 'virtuell (Software)' }[d.conn] || d.conn || '–')], ['Stromversorgung', esc(d.power || '')]]);
}
/* Geräte-Browser: Gruppen, Suche, Status, Ansicht – Zustand bleibt beim Seitenwechsel erhalten */
const DEVUI = { grp: 'Alle', q: '', st: 'alle', view: store.get('orbit-devview') || 'karten' };
function devBrowserHTML(devices) {
  const groups = ['Alle', ...new Set(devices.map(d => d.group))];
  return `<div class="filters">${chipRow('dv-g', groups.map(g => [g, g, g === 'Alle' ? devices.length : devices.filter(d => d.group === g).length, g === 'Alle' ? 'grid' : GROUP_ICON[g]]), DEVUI.grp)}</div>
    <div class="filters">${searchField('dv-q', 'Name, IP, MAC, Raum, Hersteller …', DEVUI.q)}<span class="f-sep"></span>${segCtl('dv-st', [['alle', 'alle'], ['online', 'online'], ['offline', 'offline']], DEVUI.st)}<span class="count-note" id="dv-n"></span>
      <div class="f-right">${segCtl('dv-v', [['karten', 'Karten', 'grid'], ['tabelle', 'Tabelle', 'list']], DEVUI.view)}</div></div><div id="dv-out"></div>`;
}
function devBrowserMount(devices, hrefFn) {
  const draw = () => {
    const q = DEVUI.q.toLowerCase().trim();
    let L = devices.filter(d => (DEVUI.grp === 'Alle' || d.group === DEVUI.grp) && (!q || [d.name, d.ip, d.mac, d.host, d.room, d.maker, d.model, d.role, d.group].join(' ').toLowerCase().includes(q)));
    if (DEVUI.st !== 'alle') L = L.filter(d => { const h = devState(d); return h && (DEVUI.st === 'online' ? h.active : !h.active); });
    L.sort((a, b) => ipNum(a.ip) - ipNum(b.ip) || a.name.localeCompare(b.name, 'de'));
    setText('dv-n', `${L.length} von ${devices.length}`);
    setHTML('dv-out', L.length ? (DEVUI.view === 'tabelle' ? `<div class="card">${devTable(L, hrefFn)}</div>` : `<div class="dev-grid">${L.map(d => devCard(d, hrefFn(d))).join('')}</div>`) : `<div class="card">${emptyMsg('Kein Gerät passt zu diesem Filter.')}</div>`);
  };
  wireSel('dv-g', v => { DEVUI.grp = v; draw(); });
  wireSel('dv-st', v => { DEVUI.st = v; draw(); });
  wireSel('dv-v', v => { DEVUI.view = v; store.set('orbit-devview', v); draw(); });
  const q = document.getElementById('dv-q'); if (q) q.addEventListener('input', () => { DEVUI.q = q.value; draw(); });
  draw();
  return draw;
}

/* ------------------------------------------------------------------ Befunde (gemeinsam) */
const SEV_ICON = { hoch: 'alertCircle', mittel: 'alert', niedrig: 'info' };
const findingCard = f => `<div class="card finding sev-${esc(f.sev)}"><span class="fi">${icon(SEV_ICON[f.sev] || 'info')}</span><div><h3>${esc(f.title)}</h3><div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">${pill('Priorität ' + f.sev, f.sev === 'hoch' ? 'bad' : f.sev === 'mittel' ? 'warn' : '')}${tag(f.area)}</div><p class="fd">${inline(f.detail)}</p><div class="fx">${icon('bulb')}<div><b>Vorschlag:</b> ${inline(f.fix)}</div></div><div class="ev">Beleg: ${esc(f.evidence)}</div></div></div>`;

/* ------------------------------------------------------------------ Befehlspalette (Suche) */
function initPalette(buildIndex) {
  const wrap = document.createElement('div');
  wrap.className = 'palette';
  wrap.innerHTML = `<div class="pal" role="dialog" aria-modal="true" aria-label="Suche"><div class="pal-in">${icon('search')}<input type="text" placeholder="Gerät, IP, MAC, Projekt, Dienst, Port, Bereich …" aria-label="Suchbegriff" autocomplete="off" spellcheck="false"><kbd>Esc</kbd></div><div class="pal-list" role="listbox"></div><div class="pal-foot"><span><kbd>↑</kbd> <kbd>↓</kbd> auswählen</span><span><kbd>↵</kbd> öffnen</span><span><kbd>/</kbd> oder <kbd>Strg</kbd>+<kbd>K</kbd> öffnet die Suche</span></div></div>`;
  document.body.append(wrap);
  const inp = $('input', wrap), listEl = $('.pal-list', wrap);
  let items = [], hits = [], sel = 0, last = null;
  const render = () => {
    let html = '', grp = '';
    hits.forEach((h, i) => {
      if (h.type !== grp) { html += `<div class="pal-grp">${esc(h.type)}</div>`; grp = h.type; }
      html += `<a class="pal-item${i === sel ? ' sel' : ''}" href="${esc(h.href)}" data-i="${i}" role="option" aria-selected="${i === sel}"><span class="pi">${icon(h.ico || 'right')}</span><span class="pt"><span class="pn">${esc(h.title)}</span>${h.sub ? `<span class="ps">${esc(h.sub)}</span>` : ''}</span>${/^https?:/.test(h.href) ? icon('ext', 'pe') : ''}</a>`;
    });
    listEl.innerHTML = html || '<div class="pal-empty">Nichts gefunden.</div>';
    const s = $('.pal-item.sel', listEl); if (s) s.scrollIntoView({ block: 'nearest' });
  };
  const filter = () => {
    const terms = inp.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    hits = (terms.length ? items.filter(it => terms.every(w => it.k.includes(w))) : items.filter(it => it.type === 'Bereiche')).slice(0, 40);
    sel = 0; render();
  };
  const open = () => { last = document.activeElement; items = buildIndex().map(it => Object.assign(it, { k: (it.k || '').toLowerCase() + ' ' + it.title.toLowerCase() })); wrap.classList.add('open'); inp.value = ''; filter(); setTimeout(() => inp.focus(), 0); };
  const close = () => { wrap.classList.remove('open'); if (last && last.focus) last.focus(); };
  const goTo = h => { close(); if (/^https?:/.test(h.href)) window.open(h.href, '_blank', 'noopener'); else location.hash = h.href.replace(/^#/, ''); };
  inp.addEventListener('input', filter);
  inp.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { sel = Math.min(sel + 1, hits.length - 1); render(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { sel = Math.max(sel - 1, 0); render(); e.preventDefault(); }
    else if (e.key === 'Enter') { if (hits[sel]) goTo(hits[sel]); e.preventDefault(); }
    else if (e.key === 'Escape') { close(); e.preventDefault(); }
  });
  listEl.addEventListener('click', e => { const a = e.target.closest('.pal-item'); if (!a) return; e.preventDefault(); const h = hits[+a.dataset.i]; if (h) goTo(h); });
  wrap.addEventListener('pointerdown', e => { if (e.target === wrap) close(); });
  document.addEventListener('keydown', e => {
    const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || '');
    if ((e.key.toLowerCase() === 'k' && (e.ctrlKey || e.metaKey)) || (e.key === '/' && !typing && !wrap.classList.contains('open'))) { e.preventDefault(); open(); }
  });
  $$('[data-open-search]').forEach(b => b.addEventListener('click', open));
}

/* ------------------------------------------------------------------ Kopieren (funktioniert auch ohne HTTPS) */
async function copyText(t) {
  try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(t); return true; } } catch (e) { /* Rückfall unten */ }
  try {
    const ta = document.createElement('textarea'); ta.value = t; ta.setAttribute('readonly', ''); ta.style.cssText = 'position:fixed;left:-9999px;opacity:0';
    document.body.append(ta); ta.select(); const ok = document.execCommand('copy'); ta.remove(); return ok;
  } catch (e) { return false; }
}

/* ------------------------------------------------------------------ Schwebende Hinweise für Balken & Segmente ([data-tip]) */
function initTips() {
  const tip = document.createElement('div'); tip.className = 'gtip'; tip.hidden = true; tip.setAttribute('role', 'tooltip'); document.body.append(tip);
  const at = (el, x, y) => {
    tip.textContent = el.getAttribute('data-tip'); tip.hidden = false;
    const w = tip.offsetWidth, h = tip.offsetHeight; let l = x + 14, t = y - h - 10;
    if (l + w > innerWidth - 8) l = x - w - 14; if (t < 8) t = y + 18;
    tip.style.left = Math.max(8, l) + 'px'; tip.style.top = t + 'px';
  };
  document.addEventListener('pointermove', e => { const el = e.target.closest && e.target.closest('[data-tip]'); if (el) at(el, e.clientX, e.clientY); else tip.hidden = true; });
  document.addEventListener('focusin', e => { const el = e.target.closest && e.target.closest('[data-tip]'); if (el) { const r = el.getBoundingClientRect(); at(el, r.left + Math.min(r.width / 2, 120), r.top); } });
  document.addEventListener('focusout', () => { tip.hidden = true; });
  window.addEventListener('scroll', () => { tip.hidden = true; }, { passive: true });
}

/* ------------------------------------------------------------------ Rahmen & Navigation */
const APP = { pages: {}, nav: [], cur: null, key: '', siteName: '', footer: null };
function setThemeIcon() {
  const dark = document.documentElement.dataset.theme === 'dark';
  const m = document.querySelector('meta[name="theme-color"]'); if (m) m.content = dark ? '#0a0d13' : '#f3f5f9'; // Statusleiste am Handy folgt dem Design
  const b = $('#theme'); if (!b) return; b.innerHTML = icon(dark ? 'sun' : 'moon'); b.title = dark ? 'Helles Design' : 'Dunkles Design';
}
function toggleSide(open) { $('#side').classList.toggle('open', open); $('#scrim').classList.toggle('open', open); $('#menu')?.setAttribute('aria-expanded', open); }
function initShell({ siteName, nav, pages, index, footer }) {
  Object.assign(APP, { siteName, nav, pages, footer });
  setThemeIcon();
  $('#theme').addEventListener('click', () => { const n = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = n; store.set('orbit-theme', n); setThemeIcon(); });
  matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', e => { if (!store.get('orbit-theme')) { document.documentElement.dataset.theme = e.matches ? 'dark' : 'light'; setThemeIcon(); } });
  $('#menu').innerHTML = icon('menu');
  $('#menu').addEventListener('click', () => toggleSide(!$('#side').classList.contains('open')));
  $('#scrim').addEventListener('click', () => toggleSide(false));
  document.addEventListener('keydown', e => { if (e.key === 'Escape') toggleSide(false); });
  document.addEventListener('click', e => {
    const r = e.target.closest('[data-range]');
    if (r) {
      CH.hours = +r.dataset.range; store.set('orbit-range', CH.hours);
      $$('[data-range]').forEach(b => { const on = +b.dataset.range === CH.hours; b.classList.toggle('on', on); b.setAttribute('aria-pressed', on); });
      loadCharts(); return;
    }
    const tv = e.target.closest('[data-tview]');
    if (tv) {
      const k = tv.dataset.tview, on = tv.getAttribute('aria-pressed') !== 'true';
      tv.setAttribute('aria-pressed', on); tv.querySelector('span').textContent = on ? 'Diagramm' : 'Tabelle';
      ['ch-', 'lg-'].forEach(p => { const x = document.getElementById(p + k); if (x) x.hidden = on; });
      const t = document.getElementById('tb-' + k); if (t) t.hidden = !on;
      if (!on) renderChart(k);
      return;
    }
    const cp = e.target.closest('[data-copy]');
    if (cp) { copyText(cp.dataset.copy).then(ok => { cp.classList.add(ok ? 'done' : 'fail'); cp.innerHTML = icon(ok ? 'check' : 'x'); setTimeout(() => { cp.classList.remove('done', 'fail'); cp.innerHTML = icon('copy'); }, 1500); }); return; }
    const row = e.target.closest('tr[data-href]');
    if (row && !e.target.closest('a')) { location.hash = row.dataset.href.replace(/^#/, ''); }
  });
  document.addEventListener('keydown', e => { const row = e.target.closest && e.target.closest('tr[data-href]'); if (row && e.key === 'Enter') location.hash = row.dataset.href.replace(/^#/, ''); });
  initPalette(index);
  initTips();
  window.addEventListener('hashchange', route);
}
function drawNav(active) {
  $('#nav').innerHTML = APP.nav.map(([g, items]) => `<div class="nav-group">${esc(g)}</div>` + items.map(([r, label, ic, count]) => {
    const c = typeof count === 'function' ? count() : count, on = active === r;
    const href = /^https?:/.test(r) ? r : '#/' + r;
    return `<a href="${href}" class="${on ? 'on' : ''}${/^https?:/.test(r) ? ' cross-nav' : ''}"${on ? ' aria-current="page"' : ''}>${icon(ic)}<span>${esc(label)}</span>${c ? `<span class="count${c.alert ? ' alert' : ''}">${esc(c.n ?? c)}</span>` : ''}</a>`;
  }).join('')).join('');
}
function route() {
  const raw = location.hash.replace(/^#\/?/, ''), [path, qs] = raw.split('?'), [r, ...rest] = path.split('/');
  const arg = rest.length ? decodeURIComponent(rest.join('/')) : undefined, params = Object.fromEntries(new URLSearchParams(qs || ''));
  const key = r in APP.pages ? r : '', page = APP.pages[key];
  APP.cur = page; APP.key = key; CH.reg.clear();
  drawNav(page.navKey || key);
  $('#crumb').innerHTML = `<span class="site">${esc(APP.siteName)}</span>${icon('chevron')}<b>${esc(page.title)}</b>`;
  document.title = `${page.title} · ${APP.siteName}`;
  const main = $('#main');
  try { main.innerHTML = page.render(arg, params) + (APP.footer ? APP.footer() : ''); }
  catch (e) { console.error(e); main.innerHTML = note('Diese Ansicht konnte nicht aufgebaut werden. Details stehen in der Browser-Konsole.', 'bad'); }
  toggleSide(false);
  window.scrollTo(0, 0);
  try { page.mount && page.mount(arg, params); } catch (e) { console.error(e); }
  if (CH.reg.size) loadCharts();
  if (page.live) Promise.resolve().then(() => page.live(arg, params)).catch(console.error);
}
