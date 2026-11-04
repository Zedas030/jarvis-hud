/* ORBIT MONITOR v4 – Live-Monitoring des Heimnetzes (Port 8092). Bausteine: orbit-ui.js.
   Datenquellen (nur lesend, über Nginx auf diesem Pi):
     api/query, api/query_range → Prometheus · api/ha/summary, api/pihole/summary → orbit-monitoring-api
     api/inventory → Doku :8094 (Geräte & Wissen – gemeinsame Quelle) · data/kb.json → Rückfall, falls die Doku fehlt */
'use strict';
Object.assign(S, { kb: null, ha: null, ph: null, inv: null });

async function loadHA() { try { S.ha = await getJSON('api/ha/summary', 15000); S.ok.ha = true; } catch (e) { S.ok.ha = false; } }
async function loadPH() { try { S.ph = await getJSON('api/pihole/summary', 15000); S.ok.ph = true; } catch (e) { S.ok.ph = false; } }
async function loadKuma() {
  try {
    const [pg, hb] = await Promise.all([getJSON('api/kuma/status', 120000), getJSON('api/kuma/heartbeat', 15000)]);
    const list = (pg.publicGroupList || []).flatMap(g => g.monitorList || []);
    S.kuma = { ok: true, monitors: list.map(m => { const beats = (hb.heartbeatList || {})[m.id] || [], last = beats[beats.length - 1]; return { id: m.id, name: m.name, beats: beats.slice(-24), status: last ? last.status : null, ping: last?.ping ?? null, time: last?.time || '' }; }) };
  } catch (e) { S.kuma = { ok: false, monitors: [] }; }
}
async function loadInv() { try { S.inv = await getJSON('api/inventory', 300000); } catch (e) { S.inv = null; } }
/* Seit ATLAS v3 ist die Doku die Quelle für Geräte und Wissen; kb.json bleibt Rückfall. */
function adoptInventory() {
  const inv = S.inv;
  if (!inv || inv.schema !== 3 || !Array.isArray(inv.devices) || !inv.devices.length || S.kb.source === 'atlas') return false;
  S.kb = Object.assign({}, S.kb, inv.knowledge || {}, { devices: inv.devices.map(d => Object.assign({ id: d.slug }, d)), changes: [], stand: inv.generated || S.kb.stand, source: 'atlas' });
  return true;
}
function haEntity(id) {
  if (!S.ha) return null;
  if (S.ha.entities && S.ha.entities[id]) return S.ha.entities[id];
  for (const k of ['energy', 'climate', 'batteries', 'presence', 'controls']) { const f = (S.ha[k] || []).find(e => e.entity_id === id); if (f) return f; }
  return null;
}
function allChanges() {
  const list = (S.kb.changes || []).map(([d, t, a]) => ({ d, t, a: a || '' }));
  (S.inv?.changes || []).forEach(c => { if (!list.some(x => x.t.slice(0, 40) === c.title.slice(0, 40))) list.push({ d: String(c.date).slice(0, 16).replace('T', ' '), t: c.title, a: c.systems || '' }); });
  return list.sort((a, b) => b.d.localeCompare(a.d));
}

const Q = {
  rx: 'sum(fritz_wan_datarate_bytes{direction="rx"})*8/1e6', tx: 'sum(fritz_wan_datarate_bytes{direction="tx"})*8/1e6',
  cpu: '100-(avg(rate(node_cpu_seconds_total{mode="idle"}[5m]))*100)', ram: '(1-node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes)*100',
  temp: 'max(node_thermal_zone_temp)', disk: '100*(1-node_filesystem_avail_bytes{mountpoint="/"}/node_filesystem_size_bytes{mountpoint="/"})',
  house: 'homeassistant_sensor_power_w{entity="sensor.hausverbrauch"}', pv: 'homeassistant_sensor_power_w{entity="sensor.noah_2000_solar_power"}',
  soc: 'homeassistant_sensor_battery_percent{entity="sensor.0pvp00ed26ut016f_soc"}', chg: 'homeassistant_sensor_power_w{entity="sensor.noah_2000_charging_power"}',
  dis: 'homeassistant_sensor_power_w{entity="sensor.noah_2000_discharge_power"}', out: 'homeassistant_sensor_power_w{entity="sensor.noah_2000_output_power"}',
  wifi: 'sum(fritz_wifi_associations_count)', noah: 'homeassistant_binary_sensor_state{entity="binary_sensor.noah_2000_connectivity"}',
  wincpu: '100-(avg(rate(windows_cpu_time_total{mode="idle"}[5m]))*100)',
};
const QUICK = [
  ['Home Assistant', `http://${PI}:8123/`, 'Smart-Home-Zentrale', 'home'], ['Pi-hole', `http://${PI}:8080/admin/`, 'DNS & Werbefilter', 'shield'],
  ['Grafana', `http://${PI}:3000/`, '9 Dashboards', 'chart'], ['FRITZ!Box', 'http://10.0.0.1/', 'Router, WLAN, DHCP', 'router'],
  ['Synology NAS', 'http://10.0.0.128:5000/', 'DSM', 'hdd'], ['Prometheus', `http://${PI}:9090/targets`, 'Scrape-Ziele', 'activity'],
  ['Solar-Dashboard', `http://${PI}:8093/`, 'PV & Speicher', 'sun'], ['Uptime Kuma', `http://${PI}:3001/`, 'Erreichbarkeit', 'bell'],
  ['J.A.R.V.I.S.', 'http://10.0.0.145:8720/', 'Sprachassistent (PC)', 'sparkles'], ['Pi-Doku (alt)', `http://${PI}:8091/`, 'Hardware & Architektur', 'book'],
  ['Repeater', 'http://10.0.0.2/', 'FRITZ!Repeater 1200 AX', 'wifi'], ['Dokumentation', DOC_URL, 'ORBIT ATLAS · Projekte', 'folder'],
];

/* ------------------------------------------------------------------ Energiefluss (PV → NOAH → Haus; Netz ohne Messpunkt) */
const pvMod = (id, x, y, w, h, cols, rows) => {
  const pad = 4, gap = 2, cw = (w - 2 * pad - (cols - 1) * gap) / cols, chh = (h - 2 * pad - (rows - 1) * gap) / rows;
  const cells = Array.from({ length: cols * rows }, (_, i) => `<rect x="${(x + pad + (i % cols) * (cw + gap)).toFixed(1)}" y="${(y + pad + Math.floor(i / cols) * (chh + gap)).toFixed(1)}" width="${cw.toFixed(1)}" height="${chh.toFixed(1)}" rx="1.5"/>`).join('');
  return `<g class="pv-mod" id="${id}"><rect class="frame" x="${x}" y="${y}" width="${w}" height="${h}" rx="4"/><g class="cells">${cells}</g></g>`;
};
function energyFlow(id) { // quer für breite Karten, hochkant fürs Handy (per CSS umgeschaltet)
  return `<svg class="ef ef-h" id="${id}" viewBox="0 0 660 250" role="img" aria-label="Energiefluss: Solar, Speicher, Haus und Netz">
    <path class="base" d="M90 111 L100 111"/><path class="base" d="M176 111 C 210 111, 215 143, 253 143"/><path class="flow idle" id="${id}-f1" d="M176 111 C 210 111, 215 143, 253 143" style="stroke:var(--series-2)"/>
    <path class="base" d="M395 143 C 455 143, 465 118, 516 118"/><path class="flow idle" id="${id}-f2" d="M395 143 C 455 143, 465 118, 516 118" style="stroke:var(--series-3)"/>
    <path class="unm" d="M560 60 L560 74"/>
    <text class="t2" x="214" y="104" text-anchor="middle" id="${id}-l1"></text><text class="lv" x="456" y="106" text-anchor="middle" id="${id}-l2"></text>
    <text class="t2" x="95" y="44" text-anchor="middle">Solar (PV)</text>
    ${pvMod(id + '-pm1', 14, 56, 76, 110, 3, 6)}${pvMod(id + '-pm2', 100, 56, 76, 110, 3, 6)}
    <text class="pw" x="52" y="186" text-anchor="middle" id="${id}-p1">–</text><text class="pw" x="138" y="186" text-anchor="middle" id="${id}-p2">–</text>
    <text class="tv" x="95" y="214" text-anchor="middle" id="${id}-pv">–</text><text class="t2" x="95" y="238" text-anchor="middle">gesamt</text>
    <rect class="nd" x="255" y="93" width="130" height="100" rx="16"/><rect class="bat" x="387" y="127" width="7" height="32" rx="3"/>
    <text class="tv" x="320" y="146" text-anchor="middle" id="${id}-soc">–</text>
    <rect class="bat-track" x="270" y="162" width="100" height="14" rx="7"/><rect class="bat-fill" id="${id}-bf" data-w="100" x="270" y="162" width="0" height="14" rx="7"/>
    <text class="t1" x="320" y="220" text-anchor="middle">NOAH 2000</text><text class="t2" x="320" y="240" text-anchor="middle" id="${id}-st">–</text>
    <circle class="nd" cx="560" cy="118" r="44"/><circle class="nd-ring" cx="560" cy="118" r="44" style="stroke:var(--series-1)"/>${svgIcon('home', 560, 118, 30)}
    <text class="t2" x="560" y="188" text-anchor="middle">Haus</text><text class="tv" x="560" y="214" text-anchor="middle" id="${id}-house">–</text><text class="t2" x="560" y="238" text-anchor="middle" id="${id}-cov"></text>
    <circle class="dash-node" cx="560" cy="34" r="26"/>${svgIcon('plug', 560, 34, 20)}
    <text class="t2" x="524" y="39" text-anchor="end">Netz · kein Messpunkt</text>
  </svg>
  <svg class="ef ef-v" id="${id}v" viewBox="0 0 340 390" role="img" aria-label="Energiefluss: Solar, Speicher, Haus und Netz">
    <path class="base" d="M67 54 L73 54"/><path class="base" d="M70 94 L70 146"/><path class="flow idle" id="${id}v-f1" d="M70 94 L70 146" style="stroke:var(--series-2)"/>
    <path class="base" d="M70 236 L70 280"/><path class="flow idle" id="${id}v-f2" d="M70 236 L70 280" style="stroke:var(--series-3)"/>
    <text class="t2" x="86" y="128" id="${id}v-l1"></text><text class="lv" x="86" y="264" id="${id}v-l2"></text>
    ${pvMod(id + 'v-pm1', 27, 18, 40, 72, 2, 4)}${pvMod(id + 'v-pm2', 73, 18, 40, 72, 2, 4)}
    <text class="t2" x="126" y="40">Solar (PV)</text><text class="tv" x="126" y="68" id="${id}v-pv">–</text><text class="t2" x="126" y="90" id="${id}v-pp"></text>
    <rect class="bat" x="58" y="142" width="24" height="7" rx="3"/><rect class="nd" x="20" y="148" width="100" height="86" rx="14"/>
    <text class="tv" x="70" y="193" text-anchor="middle" id="${id}v-soc">–</text>
    <rect class="bat-track" x="32" y="208" width="76" height="12" rx="6"/><rect class="bat-fill" id="${id}v-bf" data-w="76" x="32" y="208" width="0" height="12" rx="6"/>
    <text class="t1" x="140" y="186">NOAH 2000</text><text class="t2" x="140" y="208" id="${id}v-st">–</text>
    <circle class="nd" cx="70" cy="320" r="38"/><circle class="nd-ring" cx="70" cy="320" r="38" style="stroke:var(--series-1)"/>${svgIcon('home', 70, 320, 26)}
    <text class="t2" x="126" y="312">Haus</text><text class="tv" x="126" y="340" id="${id}v-house">–</text><text class="t2" x="126" y="362" id="${id}v-cov"></text>
    <circle class="dash-node" cx="290" cy="320" r="22"/>${svgIcon('plug', 290, 320, 18)}
    <text class="t2" x="290" y="362" text-anchor="middle">Netz</text><text class="t2" x="290" y="380" text-anchor="middle">kein Messpunkt</text>
  </svg>`;
}
function updateEnergy(id, v) {
  // Linie wird mit der Leistung schneller und dicker – so sieht man auf einen Blick, wie viel fließt
  const flow = (el, w) => { const e = document.getElementById(el); if (!e) return; const on = (num(w) || 0) > 1; e.classList.toggle('idle', !on); if (on) { e.style.setProperty('--dur', Math.max(.6, 3.2 - w / 300).toFixed(2) + 's'); e.style.strokeWidth = (4 + Math.min(6, w / 120)).toFixed(1); } };
  const dis = (num(v.dis) || 0) > 1, chg = (num(v.chg) || 0) > 1;
  [id, id + 'v'].forEach(id => {
    setText(id + '-pv', `${fmt(v.pv, 0)} W`); setText(id + '-house', `${fmt(v.house, 0)} W`); setText(id + '-soc', `${fmt(v.soc, 0)} %`);
    const half = num(v.pv) === null ? null : v.pv / 2;
    setText(id + '-pp', half === null ? '' : `2 Panels · je ${fmt(half, 0)} W`);
    setText(id + '-p1', `${fmt(half, 0)} W`); setText(id + '-p2', `${fmt(half, 0)} W`);
    ['-pm1', '-pm2'].forEach(s => { const m = document.getElementById(id + s); if (m) m.classList.toggle('on', (half || 0) > 2); });
    setText(id + '-st', chg ? `▲ lädt mit ${fmt(v.chg, 0)} W` : dis ? `▼ entlädt mit ${fmt(v.dis, 0)} W` : 'in Ruhe');
    const st = document.getElementById(id + '-st');
    if (st) st.setAttribute('class', 't2' + (chg ? ' st-chg' : dis ? ' st-dis' : ''));
    setText(id + '-l1', chg ? `Laden ${fmt(v.chg, 0)} W` : '');
    setText(id + '-l2', (num(v.out) || 0) > 1 ? `Abgabe ${fmt(v.out, 0)} W` : 'keine Abgabe');
    const cov = (num(v.out) || 0) > 1 && (num(v.house) || 0) > 1 ? Math.min(100, v.out / v.house * 100) : null;
    setText(id + '-cov', cov === null ? '' : `${fmt(cov, 0)} % vom Speicher`);
    flow(id + '-f1', chg ? v.chg : v.pv);
    flow(id + '-f2', v.out);
    const bf = document.getElementById(id + '-bf');
    if (bf) { const p = Math.max(0, Math.min(100, num(v.soc) || 0)); bf.setAttribute('width', (p / 100 * +bf.dataset.w).toFixed(1)); bf.style.fill = p < 10 ? 'var(--bad)' : p < 20 ? 'var(--warn)' : ''; }
  });
}

/* Solarmodule: der NOAH misst nur die Summe beider Panels, je Panel = Summe ÷ 2 */
const PANEL_CELLS = Array.from({ length: 24 }, (_, i) => `<rect x="${6 + (i % 6) * 18}" y="${6 + Math.floor(i / 6) * 16}" width="16" height="14" rx="1.5"/>`).join('');
const pvPanels = () => `<div class="pv-panels">${[1, 2].map(n => `<div class="pv-panel">
    <svg class="pv-mod" id="pm${n}" viewBox="0 0 120 76" aria-hidden="true"><rect class="frame" x="1" y="1" width="118" height="74" rx="5"/><g class="cells">${PANEL_CELLS}</g></svg>
    <div><div class="tile-l">Panel ${n}</div><div class="room-t" style="margin:4px 0"><span class="v" id="pp${n}-w">–</span><span class="u">W</span></div>
    <div class="small muted" id="pp${n}-d">–</div></div></div>`).join('')}</div>`;
function updatePanels(pv, today, total) {
  const half = v => num(v) === null ? null : v / 2;
  [1, 2].forEach(n => {
    setText(`pp${n}-w`, fmt(half(pv), 0));
    setText(`pp${n}-d`, `heute ${fmt(half(today), 2)} kWh · gesamt ${fmt(half(total), 1)} kWh`);
    const m = document.getElementById('pm' + n); if (m) m.classList.toggle('on', (half(pv) || 0) > 2);
  });
}

/* ------------------------------------------------------------------ Netzwerk-Topologie (SVG, auf dem Handy als Liste) */
const TOPO = [
  { id: 'wlan', x: 150, y: 52, ico: 'wifi', t1: 'WLAN-Geräte', t2: '2,4 + 5 GHz', href: '#/geraete' },
  { id: 'inet', x: 500, y: 52, ico: 'cloud', t1: 'Internet', t2: 'Kabel 1.150 / 86', href: '#/netz' },
  { id: 'mesh', x: 850, y: 52, ico: 'devices', t1: 'Mesh-Clients', t2: 'über den Repeater', href: '#/geraete' },
  { id: 'router', x: 500, y: 172, ico: 'router', t1: 'FRITZ!Box 6660', t2: '10.0.0.1', href: '#/geraet/router', core: 1, mac: '02:00:00:00:00:04' },
  { id: 'rep', x: 850, y: 172, ico: 'wifi', t1: 'Repeater 1200 AX', t2: '10.0.0.2', href: '#/geraet/repeater', mac: '02:00:00:00:00:05' },
  { id: 'pi', x: 130, y: 330, ico: 'cpu', t1: 'Raspberry Pi 4', t2: '.110 · DNS, HA, MQTT', href: '#/pi', core: 1, mac: '02:00:00:00:00:06' },
  { id: 'nas', x: 360, y: 330, ico: 'hdd', t1: 'Synology NAS', t2: '.128 · Backups, Doku', href: '#/geraet/synology', mac: '02:00:00:00:00:07' },
  { id: 'pc', x: 590, y: 330, ico: 'monitor', t1: 'Windows-PC', t2: '.145 · Jarvis', href: '#/geraet/pc', mac: '02:00:00:00:00:01' },
  { id: 'tv', x: 820, y: 330, ico: 'tv', t1: 'Fernseher TCL', t2: '.166 · Fire OS', href: '#/geraet/tv', mac: '02:00:00:00:00:08' },
  { id: 'ts', x: 590, y: 455, ico: 'lock', t1: 'Tailscale', t2: 'PC · iPhone · iPad', href: '#/jarvis' },
];
const LINKS = [
  { id: 'inet', d: 'M500 82 V142', live: 'inet' }, { id: 'wl', d: 'M410 172 H150 V82', cls: 'wl', live: 'wlan' },
  { id: 'mesh', d: 'M590 172 H760', cls: 'wl', live: 'rep' }, { id: 'meshc', d: 'M850 142 V82', cls: 'wl' },
  { id: 'bus', d: 'M500 202 V250 M130 250 H820' },
  { id: 'lpi', d: 'M130 250 V300', live: 'pi' }, { id: 'lnas', d: 'M360 250 V300', live: 'nas' }, { id: 'lpc', d: 'M590 250 V300', live: 'pc' }, { id: 'ltv', d: 'M820 250 V300', live: 'tv' },
  { id: 'vpn', d: 'M590 360 V425', cls: 'vpn' },
];
function topology() {
  const node = n => `<g class="node${n.core ? ' core' : ''}" tabindex="0" role="link" aria-label="${esc(n.t1)}" data-href="${n.href}">
    <rect x="${n.x - 92}" y="${n.y - 30}" width="184" height="60" rx="14"/><circle class="nic" cx="${n.x - 62}" cy="${n.y}" r="18"/>${svgIcon(n.ico, n.x - 62, n.y, 20)}
    <text class="t1" x="${n.x - 36}" y="${n.y - 3}">${esc(n.t1)}</text><text class="t2" x="${n.x - 36}" y="${n.y + 14}" id="tp-${n.id}">${esc(n.t2)}</text>
    <circle class="st" id="tst-${n.id}" cx="${n.x + 76}" cy="${n.y - 16}" r="5"/></g>`;
  return `<svg class="topo" viewBox="0 0 1000 500" role="group" aria-label="Netzwerktopologie">
    ${LINKS.map(l => `<path class="lk ${l.cls || ''}" d="${l.d}"/>`).join('')}
    ${LINKS.filter(l => l.live).map(l => `<path class="lk-live" id="tl-${l.id}" d="${l.d}" style="display:none"/>`).join('')}
    <text class="lbl" x="512" y="118">Kabel (DOCSIS)</text><text class="lbl" x="250" y="164">WLAN</text>
    <text class="lbl" x="606" y="164" id="tl-meshrate">Mesh 5 GHz</text><text class="lbl" x="140" y="242">LAN (Ethernet)</text><text class="lbl" x="602" y="400">VPN-Overlay</text>
    ${TOPO.map(node).join('')}</svg>
    <ul class="rows topo-list">${TOPO.map(n => `<li><span class="dev-i" style="width:36px;height:36px">${icon(n.ico)}</span><div class="grow"><a href="${n.href}"><b>${esc(n.t1)}</b></a><div class="small muted" id="tpl-${n.id}">${esc(n.t2)}</div></div><i class="sd" id="tsl-${n.id}"></i></li>`).join('')}</ul>`;
}
function wireTopology() {
  $$('.topo .node[data-href]').forEach(g => {
    g.addEventListener('click', () => { location.hash = g.dataset.href.replace(/^#/, ''); });
    g.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); location.hash = g.dataset.href.replace(/^#/, ''); } });
  });
}
function updateTopology(o) {
  const st = {}, on = id => st[id] === 'ok';
  TOPO.forEach(n => { if (n.mac) { const h = S.hosts.get(n.mac); st[n.id] = h ? (h.active ? 'ok' : 'off') : ''; } });
  st.inet = o.fritz === 1 ? 'ok' : o.fritz === 0 ? 'bad' : '';
  st.router = o.fritz === 1 ? 'ok' : st.router;
  const wl = [...S.hosts.values()].filter(h => h.active && h.iface === '802.11').length;
  st.wlan = wl ? 'ok' : '';
  st.ts = st.pc;
  setText('tp-wlan', `${wl} aktiv per WLAN`); setText('tpl-wlan', `${wl} aktiv per WLAN`);
  if (o.mesh) setText('tl-meshrate', `Mesh 5 GHz · ${o.mesh}`);
  TOPO.forEach(n => {
    const s = st[n.id], cls = s === 'ok' ? 'ok' : s === 'bad' ? 'bad' : '';
    const c = document.getElementById('tst-' + n.id); if (c) c.setAttribute('class', 'st ' + cls);
    const l = document.getElementById('tsl-' + n.id); if (l) l.className = 'sd ' + cls;
  });
  LINKS.filter(l => l.live).forEach(l => { const e = document.getElementById('tl-' + l.id); if (e) e.style.display = on(l.live) && on('router') ? '' : 'none'; });
}

/* ------------------------------------------------------------------ Uptime Kuma (Übersicht) */
const KUMA_ST = { 0: ['down', 'bad', 'Ausfall'], 1: ['up', 'ok', 'erreichbar'], 2: ['pend', 'warn', 'wartet'], 3: ['maint', '', 'Wartung'] };
const kumaTime = t => t ? new Date(t.replace(' ', 'T') + 'Z') : null; // Kuma speichert UTC ohne Zeitzone
const kumaCard = () => `<div class="card lage ov-4 ov-tall" id="ov-kuma"><div class="card-h"><span class="ico">${icon('bell')}</span><h3>Uptime Kuma</h3><div class="aside" id="kuma-flag"></div></div>
  <div class="card-b"><div id="kuma-body"><div class="kuma-empty">Lade Statusseite …</div></div>
  <div class="lage-foot"><span id="kuma-foot"></span><a href="http://${PI}:3001/status/jarvis" target="_blank" rel="noopener">Statusseite ${icon('right')}</a></div></div></div>`;
function renderKuma() {
  const k = S.kuma; if (!k || !document.getElementById('kuma-body')) return;
  if (!k.ok) { setHTML('kuma-body', note('Uptime Kuma antwortet nicht (Port 3001).', 'bad')); setHTML('kuma-flag', pill('Störung', 'bad')); setText('kuma-foot', ''); return; }
  const ms = k.monitors, up = ms.filter(m => m.status === 1).length, down = ms.filter(m => m.status === 0).length;
  if (!ms.length) { setHTML('kuma-body', '<div class="kuma-empty">Die Statusseite „jarvis“ enthält keine Dienste.</div>'); return; }
  setHTML('kuma-flag', down ? pill(`${down} Ausfall${down > 1 ? 'e' : ''}`, 'bad', 'alertCircle') : pill('alles erreichbar', 'ok', 'check'));
  setHTML('kuma-body', `<div class="kuma-sum"><span class="v">${up}/${ms.length}</span><span class="l">Dienste erreichbar</span></div>
    <ul class="kuma">${ms.map(m => { const st = KUMA_ST[m.status] || ['', '', 'unbekannt']; return `<li class="km" title="${esc(m.name)}: ${st[2]}"><i class="sd ${st[1]}"></i><span class="kn">${esc(m.name)}${m.ping !== null ? `<small>${fmt(m.ping, 0)} ms</small>` : ''}</span><span class="hb" aria-label="letzte ${m.beats.length} Prüfungen">${m.beats.map(b => `<i class="${(KUMA_ST[b.status] || [''])[0]}"></i>`).join('')}</span></li>`; }).join('')}</ul>`);
  const last = ms.map(m => m.time).filter(Boolean).sort().pop();
  setText('kuma-foot', last ? `letzter Herzschlag ${ago(kumaTime(last))} · jede Minute` : '');
}

/* ------------------------------------------------------------------ Systemprüfung (Hero) */
async function healthChecks(rx, tx) {
  const [nasOk, fritz, noah, win, targets] = await Promise.all([getJSON('api/inventory', 60000).then(() => true, () => false), val('fritz_device_reachable'), val(Q.noah), val('up{job="windows"}'), prom('up')]);
  const up = targets.filter(t => t.value[1] === '1').length;
  return {
    fritz, list: [
      { name: 'Internet', state: fritz === 1 ? 'ok' : 'bad', text: fritz === 1 ? `${fmt(rx)} ↓ · ${fmt(tx)} ↑ Mbit/s` : 'Router nicht erreichbar', href: '#/netz', crit: true },
      { name: 'DNS', state: S.ph?.queries ? 'ok' : 'bad', text: S.ph?.queries ? `${fmt(S.ph.queries.percent_blocked)} % geblockt` : 'Pi-hole antwortet nicht', href: '#/dns', crit: true },
      { name: 'Home Assistant', state: S.ha ? 'ok' : 'bad', text: S.ha ? `${(S.ha.automations || []).filter(a => a.enabled).length}/${(S.ha.automations || []).length} Automationen` : 'antwortet nicht', href: '#/smarthome', crit: true },
      { name: 'Messwerte', state: targets.length && up === targets.length ? 'ok' : targets.length ? 'warn' : 'bad', text: `${up}/${targets.length} Ziele`, href: '#/monitoring', crit: true },
      { name: 'NAS', state: nasOk ? 'ok' : 'bad', text: nasOk ? 'Freigabe lesbar' : 'Freigabe fehlt', href: '#/backups', crit: true },
      { name: 'NOAH-Speicher', state: noah === 1 ? 'ok' : 'warn', text: noah === 1 ? 'verbunden' : noah === 0 ? 'getrennt' : 'kein Messwert', href: '#/energie', crit: true },
      { name: 'Windows-PC', state: win === 1 ? 'ok' : '', text: win === 1 ? 'online' : 'aus', href: '#/jarvis', crit: false },
    ],
  };
}
function renderHero(checks) {
  const crit = checks.filter(c => c.crit), okN = crit.filter(c => c.state === 'ok').length;
  const bad = crit.filter(c => c.state === 'bad'), warn = crit.filter(c => c.state === 'warn'), st = bad.length ? 'bad' : warn.length ? 'warn' : 'ok';
  const hero = document.getElementById('hero'); if (!hero) return;
  hero.dataset.state = st;
  setHTML('hero-ring', icon(st === 'ok' ? 'checkCircle' : st === 'warn' ? 'alert' : 'xCircle'));
  setText('hero-t', st === 'ok' ? 'Alles läuft.' : st === 'warn' ? 'Fast alles läuft.' : 'Störung im Heimnetz.');
  const names = [...bad, ...warn].map(c => c.name).join(', ');
  setText('hero-p', st === 'ok' ? `Alle ${crit.length} Kernsysteme antworten · geprüft um ${hhmm(Date.now())} Uhr, Aktualisierung alle 20 Sekunden.` : `${names} ${bad.length + warn.length === 1 ? 'braucht' : 'brauchen'} Aufmerksamkeit · geprüft um ${hhmm(Date.now())} Uhr.`);
  setText('hero-n', `${okN}/${crit.length}`);
  setHTML('hero-checks', checks.map(c => `<a class="check" href="${c.href}"><i class="sd ${c.state}${c.state && c.state !== 'ok' ? ' pulse' : ''}"></i>${esc(c.name)}<span class="ct">${esc(c.text)}</span></a>`).join(''));
}
const heroOrbit = () => `<svg class="hero-orbit" viewBox="0 0 380 380" aria-hidden="true"><g fill="none" stroke="currentColor"><g class="o1"><ellipse cx="190" cy="190" rx="172" ry="68" stroke-opacity=".28" transform="rotate(-18 190 190)"/><circle cx="350" cy="136" r="5" fill="currentColor" stroke="none" opacity=".7"/></g><g class="o2"><circle cx="190" cy="190" r="122" stroke-opacity=".16"/><circle cx="190" cy="68" r="3.5" fill="currentColor" stroke="none" opacity=".55"/></g><circle cx="190" cy="190" r="62" stroke-opacity=".12"/></g></svg>`;
const changeTL = list => `<ul class="tl">${list.map(c => `<li class="chg"><div class="when">${dDE(c.d)}${c.a ? tag(c.a.split(',')[0].slice(0, 28)) : ''}</div><div class="what">${inline(c.t)}</div></li>`).join('')}</ul>`;
const findingMini = f => `<a class="card finding link sev-${esc(f.sev)}" href="#/befunde" style="display:grid"><span class="fi">${icon(SEV_ICON[f.sev] || 'info')}</span><div><h3>${esc(f.title)}</h3><p class="fd" style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${esc(f.detail)}</p></div></a>`;

/* ------------------------------------------------------------------ Seiten */
const PAGES = {};
PAGES[''] = {
  title: 'Übersicht',
  render() {
    const open = S.kb.findings.filter(f => f.sev !== 'niedrig');
    return `<section class="hero" id="hero">${heroOrbit()}
        <div class="hero-main"><span class="hero-ring" id="hero-ring">${icon('activity')}</span><div><div class="kicker">Heimnetz 10.0.0.0/24 · Live</div><h1 id="hero-t">Prüfe Systeme …</h1><p id="hero-p">Die Kernsysteme werden gerade abgefragt.</p></div></div>
        <div class="hero-fig"><span class="hf" id="hero-n">–</span><span class="hl">Kernsysteme erreichbar</span></div>
        <div class="hero-checks" id="hero-checks"></div></section>
      <div class="ov-grid">
        <div id="sec-ov">${secOverview()}</div>
        ${kumaCard()}
        ${card('Energie-Kreislauf', `<div class="ef-wrap">${energyFlow('ov')}</div>`, { ico: 'bolt', cls: 'ov-8', aside: '<a href="#/energie">Energie &amp; Speicher</a>' })}
        ${card('Netzwerk jetzt', '<ul class="rows" id="ov-net"><li class="muted">Lade …</li></ul>', { ico: 'network', cls: 'ov-4', aside: '<a href="#/netz">Netzwerkkarte</a>' })}
        <div class="ov-8 kpis">
        ${tile('wan', { label: 'Internet · Download', ico: 'globe', unit: 'Mbit/s', spark: true, href: '#/netz' })}
        ${tile('dns', { label: 'DNS-Anfragen (24 h)', ico: 'shield', href: '#/dns' })}
        ${tile('dev', { label: 'Geräte online', ico: 'devices', meter: true, href: '#/geraete' })}
        ${tile('house', { label: 'Hausverbrauch', ico: 'home', unit: 'W', spark: true, href: '#/energie' })}
        ${tile('cpu', { label: 'Pi · CPU', ico: 'cpu', unit: '%', spark: true, href: '#/pi' })}
        ${tile('ram', { label: 'Pi · Arbeitsspeicher', ico: 'database', unit: '%', meter: true, href: '#/pi' })}
        ${tile('temp', { label: 'Pi · Temperatur', ico: 'thermo', unit: '°C', meter: true, href: '#/pi' })}
        ${tile('soc', { label: 'Speicher NOAH', ico: 'battery', unit: '%', meter: true, href: '#/energie' })}
        </div>
      </div>
      ${section('Offene Befunde', `<div class="grid cols-2">${open.slice(0, 4).map(findingMini).join('')}</div>`, { sub: `${open.length} mit Priorität hoch oder mittel`, right: btn('Alle Befunde', '#/befunde', { kind: 'sm', ico: 'right' }) })}
      ${section('Schnellzugriff', `<div class="grid cols-auto">${QUICK.map(([n, u, s, ic]) => `<a class="card dev link" href="${u}"${u === DOC_URL ? '' : ' target="_blank" rel="noopener"'}><span class="dev-i">${icon(ic)}</span><div class="dev-m"><div class="dev-n">${esc(n)}</div><div class="dev-r">${esc(s)}</div><div class="dev-tags">${monoChip(u.replace('http://', '').replace(/\/$/, ''))}</div></div></a>`).join('')}</div>`)}
      ${section('Letzte Änderungen', card('', changeTL(allChanges().slice(0, 6))), { right: btn('Alle Änderungen', '#/aenderungen', { kind: 'sm', ico: 'right' }) })}`;
  },
  async live() {
    renderKuma();
    const [rx, tx, cpu, ram, temp, house, soc, pv, chg, dis, out, wifi, mesh, cu, sRx, sHouse, sCpu] = await Promise.all([
      val(Q.rx), val(Q.tx), val(Q.cpu), val(Q.ram), val(Q.temp), val(Q.house), val(Q.soc), val(Q.pv), val(Q.chg), val(Q.dis), val(Q.out), val(Q.wifi),
      prom('fritz_mesh_link_current_data_rate_kbps'), val('gateway_connection_uptime_seconds'), range(Q.rx, 1), range(Q.house, 1), range(Q.cpu, 1)]);
    setTile('wan', { v: fmt(rx), sub: `Upload ${fmt(tx)} Mbit/s · Leitung 1.150 / 86` }); spark('ts-wan', sRx[0]?.pts);
    if (S.ph?.queries) setTile('dns', { v: fmt(S.ph.queries.total, 0), sub: `${fmt(S.ph.queries.percent_blocked)} % geblockt · ${S.ph.clients.active} Clients`, flag: null });
    else setTile('dns', { v: 'aus', sub: 'Pi-hole antwortet nicht', flag: ['Störung', 'bad'] });
    const hs = [...S.hosts.values()], onl = hs.filter(h => h.active);
    setTile('dev', { v: String(onl.length), sub: `von ${hs.length} bekannten · ${onl.filter(h => h.iface === 'Ethernet').length} per LAN`, meter: hs.length ? onl.length / hs.length * 100 : 0 });
    setTile('house', { v: fmt(house, 0), sub: `Solar gerade ${fmt(pv, 0)} W` }); spark('ts-house', sHouse[0]?.pts);
    setTile('cpu', { v: fmt(cpu), sub: 'Mittel über 5 Minuten', flag: cpu > 85 ? ['hoch', 'bad'] : cpu > 60 ? ['erhöht', 'warn'] : null }); spark('ts-cpu', sCpu[0]?.pts);
    setTile('ram', { v: fmt(ram), sub: '8 GB verbaut', meter: ram, tone: toneFor(ram, 80, 92) });
    setTile('temp', { v: fmt(temp), sub: temp > 70 ? 'warm – Kühlung prüfen' : 'unkritisch · Drosselung ab 80 °C', meter: (temp || 0) / 85 * 100, tone: toneFor(temp, 70, 80) });
    setTile('soc', { v: fmt(soc, 0), sub: (chg || 0) > 1 ? `lädt mit ${fmt(chg, 0)} W` : (dis || 0) > 1 ? `entlädt mit ${fmt(dis, 0)} W` : '2 Module · in Ruhe', meter: soc, tone: soc !== null && soc < 10 ? 'warn' : '' });
    updateEnergy('ov', { pv, house, soc, chg, dis, out });
    const m5 = mesh.filter(x => (x.metric.interface || '').includes('5G')).map(x => num(x.value[1]) / 1000).sort((a, b) => b - a)[0];
    setHTML('ov-net', [
      ['globe', 'Internet', `${fmt(rx)} ↓ · ${fmt(tx)} ↑ Mbit/s`],
      ['wifi', 'WLAN-Verbindungen', fmt(wifi, 0)],
      ['devices', 'Geräte online', `${onl.filter(h => h.iface === 'Ethernet').length} per LAN · ${onl.filter(h => h.iface === '802.11').length} per WLAN`],
      ['router', 'Mesh zum Repeater', m5 ? `${fmt(m5, 0)} Mbit/s Linkrate` : '–'],
      ['clock', 'Internet verbunden seit', dur(cu)],
    ].map(([ic, l, v]) => `<li><span class="dev-i" style="width:34px;height:34px">${icon(ic)}</span><span class="grow">${esc(l)}</span><b class="nowrap">${esc(v)}</b></li>`).join(''));
    const hc = await healthChecks(rx, tx);
    renderHero(hc.list);
  },
};

PAGES.befunde = {
  title: 'Befunde & To-dos', sev: 'alle',
  render() {
    const F = S.kb.findings, n = s => F.filter(f => f.sev === s).length;
    return head({ kicker: 'Prüfergebnis der Bestandsaufnahme', title: 'Befunde & To-dos', ico: 'alert', text: 'Alles, was bei der Prüfung aufgefallen ist – mit Beleg und konkretem Lösungsvorschlag. Erledigte Punkte wandern ins Änderungsprotokoll.' }) +
      `<div class="filters">${chipRow('fs', [['alle', 'Alle', F.length], ['hoch', 'Hoch', n('hoch'), 'alertCircle'], ['mittel', 'Mittel', n('mittel'), 'alert'], ['niedrig', 'Niedrig', n('niedrig'), 'info']], this.sev)}</div><div class="grid" id="fl"></div>`;
  },
  mount() { const draw = () => setHTML('fl', S.kb.findings.filter(f => this.sev === 'alle' || f.sev === this.sev).map(findingCard).join('')); wireSel('fs', v => { this.sev = v; draw(); }); draw(); },
};

PAGES.aenderungen = {
  title: 'Änderungen', q: '',
  render() {
    return head({ kicker: 'Änderungsprotokoll', title: 'Was sich verändert hat', ico: 'history', text: 'Das zentrale Änderungsprotokoll der Netzwerk-Doku – manuelle Einträge und die automatischen Meldungen des nächtlichen Netzscans.', actions: btn('In der Doku', DOC_URL + '#/aenderungen', { kind: 'cross', ico: 'book', same: true }) }) +
      `<div class="filters">${searchField('cq', 'Protokoll durchsuchen …', this.q)}<span class="count-note" id="cn"></span></div><div class="card"><div class="card-b" id="cl"></div></div>`;
  },
  mount() {
    const draw = () => {
      const q = this.q.toLowerCase(), L = allChanges().filter(c => !q || (c.t + ' ' + c.a).toLowerCase().includes(q));
      setText('cn', `${L.length} Einträge`);
      let html = '', day = '';
      L.forEach(c => {
        const d = c.d.slice(0, 10);
        if (d !== day) { if (day) html += '</ul>'; html += `<div class="month">${new Date(d + 'T12:00').toLocaleDateString('de-DE', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}</div><ul class="tl">`; day = d; }
        html += `<li class="chg"><div class="when">${esc(c.d.slice(11) || 'ganztägig')}${c.a ? tag(c.a.split(',')[0].slice(0, 30)) : ''}</div><div class="what">${inline(c.t)}</div></li>`;
      });
      setHTML('cl', html ? html + '</ul>' : emptyMsg('Kein Eintrag passt zur Suche.'));
    };
    const i = document.getElementById('cq'); if (i) i.addEventListener('input', () => { this.q = i.value; draw(); });
    draw();
  },
};

PAGES.netz = {
  title: 'Netzwerk',
  render() {
    const legend = `<span class="lg-i"><svg width="22" height="6"><path d="M1 3H21" stroke="var(--line-2)" stroke-width="2"/></svg>Kabel</span><span class="lg-i"><svg width="22" height="6"><path d="M1 3H21" stroke="var(--line-2)" stroke-width="2" stroke-dasharray="5 4"/></svg>Funk</span><span class="lg-i"><svg width="22" height="6"><path d="M1 3H21" stroke="var(--accent-2)" stroke-width="2" stroke-dasharray="2 4" stroke-linecap="round"/></svg>VPN</span><span class="lg-i"><svg width="22" height="6"><path d="M1 3H21" stroke="var(--accent)" stroke-width="2.5" stroke-dasharray=".5 6" stroke-linecap="round"/></svg>aktive Verbindung</span>`;
    return head({ kicker: 'Topologie & Adressierung', title: 'Netzwerk', ico: 'network', text: 'Aufbau, Internetleitung, WLAN, Mesh und Adressplan. Ein Klick auf ein Gerät öffnet seinen Steckbrief; die Punkte zeigen den Live-Status aus der FRITZ!Box.', actions: btn('FRITZ!Box öffnen', 'http://10.0.0.1/', { ico: 'router' }) }) +
      card('Netzwerkkarte', topology(), { ico: 'network', aside: `<div class="legend" style="margin:0">${legend}</div>` }) +
      `<div class="grid cols-4" style="margin-top:16px">${tile('rx', { label: 'Download', ico: 'download', unit: 'Mbit/s', sub: 'Leitung max. 1.150' })}${tile('tx', { label: 'Upload', ico: 'right', unit: 'Mbit/s', sub: 'Leitung max. 86,25' })}${tile('wifi', { label: 'WLAN-Verbindungen', ico: 'wifi', sub: 'alle Bänder' })}${tile('wup', { label: 'Internet verbunden seit', ico: 'clock' })}</div>` +
      section('Verlauf', rangeBar() + `<div class="grid cols-2">${chartCard('wan', { title: 'Internet-Datenrate', ico: 'globe', unit: 'Mbit/s', digits: 2, series: [{ name: 'Download', q: Q.rx, slot: 1 }, { name: 'Upload', q: Q.tx, slot: 2 }] })}${chartCard('wifi', { title: 'WLAN-Verbindungen', ico: 'wifi', unit: '', digits: 0, series: [{ name: 'WLAN-Verbindungen', q: Q.wifi }] })}</div>`) +
      section('Internet & Router', `<div class="grid cols-2">${card('Anschluss', kv([['Typ', 'Kabel (DOCSIS), FRITZ!Box 6660 Cable'], ['Leitung', '1.150 Mbit/s ↓ · 86,25 Mbit/s ↑'], ['Firmware', 'FRITZ!OS 8.25'], ['Router-CPU', '<span id="r-cpu">…</span>'], ['Router läuft seit', '<span id="r-up">…</span>'], ['Update verfügbar', '<span id="r-upd">…</span>'], ['Bekannte Geräte', '<span id="r-known">…</span>']]), { ico: 'router' })}
        ${card('WLAN & Mesh', kv([['WLAN-Name', 'Heimnetz-WLAN (Wi-Fi 6)'], ['Kanäle', '2,4 GHz: 7 · 5 GHz: 36'], ['Gastzugang', 'aus'], ['Mesh', 'FRITZ!Repeater 1200 AX per 5 GHz'], ['Mesh-Linkrate', '<span id="r-mesh">…</span>']]) + `<p class="small muted" style="margin-top:10px">Die Linkrate ist die ausgehandelte Funkrate, nicht der tatsächliche Durchsatz.</p>`, { ico: 'wifi' })}</div>`) +
      section('Adressplan', card('', `<div class="table-wrap"><table class="t"><thead><tr><th>Bereich</th><th>Verwendung</th><th>Hinweis</th></tr></thead><tbody>${[
        ['.1', 'FRITZ!Box (Gateway)', 'auch IPv6-Rückfall-DNS'], ['.2', 'FRITZ!Repeater 1200 AX', ''], ['.100 – .199', 'DHCP-Geräte', 'Server fest zugewiesen: Pi .110, NAS .128, PC .145'],
        ['.110', 'Raspberry Pi', 'DNS-Server für alle Clients'], ['.113 – .157', 'Tapo-Steckdosen, Hub, Levoit, NOAH', 'überwiegend WLAN'], ['.166', 'Fernseher TCL (LAN)', 'ADB-Port 5555 nur bei Bedarf'],
        ['.241 / .242', 'VPN-Benutzer der FRITZ!Box', '„admin“, „nutzer-zugang“'], ['172.17.0.0/16', 'Docker-Bridge auf dem Pi', 'intern'], ['172.18.0.0/16', 'Docker-Netz energy-stack_default', 'nur noah-mqtt'],
        ['100.64.0.0/10', 'Tailscale-Tailnet', 'PC, iPhone, iPad'], ['IPv6', '2001:db8::/64 · fd00::/64', 'global / ULA']].map(([a, b, c]) => `<tr><td class="mono nowrap">${a}</td><td>${b}</td><td class="muted">${c}</td></tr>`).join('')}</tbody></table></div>`, { bodyCls: 'flush' }), { sub: 'IPv4 10.0.0.0/24 · DHCP: FRITZ!Box · DNS: Pi-hole' }) +
      section('So läuft eine DNS-Anfrage', card('', steps(['Ein Gerät fragt `10.0.0.110:53` (Pi-hole) – die FRITZ!Box verteilt den Pi als DNS-Server.', 'Pi-hole prüft gegen rund 3,1 Mio. Domains in den Blocklisten. Treffer werden blockiert.', 'Sonst Antwort aus dem Cache oder Weiterleitung an Google DNS (8.8.8.8 / 8.8.4.4).', 'Lokale Namen (`*.fritz.box`) beantwortet die FRITZ!Box. Der Fernseher nutzt AdGuard-DNS und umgeht Pi-hole.']) + `<div style="margin-top:14px">${note('Fällt der Pi aus, gibt es keine DNS-Reserve – siehe <a href="#/befunde">Befunde</a>.', 'warn')}</div>`));
  },
  mount() { wireTopology(); },
  async live() {
    const [rx, tx, wifi, cu, ut, cpuT, cpuU, upd, known, mesh, fritz] = await Promise.all([val(Q.rx), val(Q.tx), val(Q.wifi), val('gateway_connection_uptime_seconds'), val('fritz_uptime_seconds_total'), val('gateway_data_ecostat_cputemp'), val('gateway_data_ecostat_cpuutil'), val('max(fritz_update_available)'), val('fritz_known_devices_count'), prom('fritz_mesh_link_current_data_rate_kbps'), val('fritz_device_reachable')]);
    setTile('rx', { v: fmt(rx) }); setTile('tx', { v: fmt(tx) }); setTile('wifi', { v: fmt(wifi, 0) });
    setTile('wup', { v: dur(cu), sub: cu ? 'seit ' + new Date(Date.now() - cu * 1000).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' }) : '' });
    setHTML('r-cpu', `${fmt(cpuU, 0)} % Last · ${fmt(cpuT, 0)} °C`); setText('r-up', dur(ut)); setHTML('r-upd', upd ? pill('ja', 'warn') : pill('nein', 'ok')); setText('r-known', `${fmt(known, 0)} (laut Exporter)`);
    const m = mesh.filter(x => (x.metric.interface || '').includes('5G')).map(x => `${x.metric.direction || ''} ${fmt(num(x.value[1]) / 1000, 0)}`.trim());
    setText('r-mesh', m.length ? m.join(' · ') + ' Mbit/s' : '–');
    const m5 = mesh.filter(x => (x.metric.interface || '').includes('5G')).map(x => num(x.value[1]) / 1000).sort((a, b) => b - a)[0];
    updateTopology({ fritz, mesh: m5 ? `${fmt(m5, 0)} Mbit/s` : '' });
  },
};

PAGES.geraete = {
  title: 'Geräte',
  render() {
    return head({ kicker: `Inventar · ${S.kb.devices.length} Einträge`, title: 'Geräte', ico: 'devices', text: 'Jedes bekannte Gerät mit IP, MAC, Raum und Rolle. Der Online-Status kommt live aus der Hostliste der FRITZ!Box; Funkgeräte am Tapo-Hub haben keine eigene IP.', actions: btn('Inventar in der Doku', DOC_URL + '#/geraete', { kind: 'cross', ico: 'book', same: true }) }) +
      devBrowserHTML(S.kb.devices) + section('Hosts ohne Zuordnung', '<div class="card" id="unk"></div>', { sub: 'kennt die FRITZ!Box, aber kein Inventareintrag' });
  },
  mount() { this.redraw = devBrowserMount(S.kb.devices, d => '#/geraet/' + devId(d)); },
  live() {
    if (this.redraw) this.redraw();
    const known = new Set(S.kb.devices.flatMap(d => macs(d.mac))), unk = [...S.hosts.entries()].filter(([m]) => !known.has(m));
    setHTML('unk', unk.length ? `<div class="card-b">${note('Meist alte, zufällige (private) WLAN-Adressen von Handys und Tablets. Neue, unbekannte Geräte erscheinen nach dem Nachtscan in der Doku in der Gruppe „Zuordnen“.')}</div><div class="table-wrap"><table class="t"><thead><tr><th>Name in der FRITZ!Box</th><th>IP</th><th>MAC</th><th>Status</th></tr></thead><tbody>${unk.map(([mc, h]) => `<tr><td class="nm">${esc(h.name)}</td><td class="mono">${esc(h.ip || '–')}</td><td class="mono small">${mc.toUpperCase()}</td><td>${h.active ? pill('online', 'ok') : pill('offline')}</td></tr>`).join('')}</tbody></table></div>` : `<div class="card-b">${emptyMsg('Alle Hosts der FRITZ!Box sind einem Gerät zugeordnet.')}</div>`);
  },
};

PAGES.geraet = {
  title: 'Gerät', navKey: 'geraete',
  render(id) {
    const d = S.kb.devices.find(x => devId(x) === id) || S.kb.devices.find(x => (x.old_slugs || []).includes(id));
    this.d = d;
    if (!d) return head({ back: ['#/geraete', 'Alle Geräte'], title: 'Gerät nicht gefunden', text: `Zu „${esc(id)}“ gibt es keinen Eintrag.` });
    const ext = d.url && /^https?:/.test(d.url) ? btn('Oberfläche öffnen', d.url, { kind: 'primary', ico: 'ext' }) : '';
    return head({ back: ['#/geraete', 'Alle Geräte'], kicker: `${esc(d.group)}${d.room ? ' · ' + esc(d.room) : ''}`, title: esc(d.name), text: esc(d.role || ''), ico: devIcon(d),
      actions: ext + (devId(d) === 'raspi' ? btn('Pi-Details', '#/pi', { ico: 'cpu' }) : '') + btn('In der Doku', DOC_URL + '#/geraet/' + devId(d), { kind: 'cross', ico: 'book', same: true }) }) +
      `<div class="grid cols-2"><div class="stack-v">${card('Steckbrief', `<div id="dfacts">${devFacts(d)}</div>`, { ico: 'info', aside: `<span id="dstate">${statusPill(d)}</span>` })}
        ${(d.notes || []).length ? card('Notizen', bullets(d.notes), { ico: 'book' }) : ''}</div>
      <div class="stack-v">${(d.issues || []).length ? card('Hinweise', bullets(d.issues), { ico: 'alert', cls: 'edge-warn' }) : ''}
        ${secDeviceCard(d)}
        ${(d.ports || []).length ? card('Dienste & offene Ports', `<div style="display:flex;flex-wrap:wrap;gap:6px">${d.ports.map(p => monoChip(p)).join('')}</div>`, { ico: 'layers' }) : ''}
        ${(d.ha || []).length ? card('Home Assistant', `<ul class="rows">${d.ha.map(e => `<li><code>${esc(e)}</code><span class="grow"></span><b class="small" id="ha-${esc(e.split(' ')[0]).replace(/[^a-z0-9_]/gi, '_')}"></b></li>`).join('')}</ul>`, { ico: 'home' }) : ''}</div></div>`;
  },
  live() {
    const d = this.d; if (!d) return;
    setHTML('dfacts', devFacts(d)); setHTML('dstate', statusPill(d));
    (d.ha || []).forEach(e => { const id = e.split(' ')[0], x = haEntity(id); if (x) setText('ha-' + id.replace(/[^a-z0-9_]/gi, '_'), `${x.state}${x.unit ? ' ' + x.unit : ''}`); });
  },
};

PAGES.dns = {
  title: 'DNS & Pi-hole',
  render() {
    const p = S.kb.pihole;
    return head({ kicker: 'Namensauflösung & Filter', title: 'DNS & Pi-hole', ico: 'shield', text: 'Pi-hole v6 auf dem Raspberry Pi beantwortet alle DNS-Anfragen im Heimnetz und filtert Werbung und Tracking. Angezeigt werden nur zusammengefasste Zahlen, keine einzelnen Abfragen.', actions: btn('Pi-hole öffnen', `http://${PI}:8080/admin/`, { ico: 'shield' }) }) +
      `<div class="grid cols-4">${tile('q', { label: 'Anfragen (24 h)', ico: 'activity' })}${tile('bl', { label: 'Geblockt', ico: 'shield', unit: '%', meter: true })}${tile('cl', { label: 'Aktive Clients', ico: 'devices' })}${tile('gr', { label: 'Domains in Blocklisten', ico: 'list' })}</div>
      <div class="grid cols-2" style="margin-top:16px">${card('So wurden Anfragen beantwortet', '<div id="dns-status"></div>', { ico: 'chart' })}${card('Abfragetypen', '<div id="dns-types"></div>', { ico: 'list' })}</div>` +
      section('Konfiguration', `<div class="grid cols-2">${card('Pi-hole', kv([['Version', 'Core 6.5 · Web 6.6.1 · FTL 6.8'], ['Upstream-DNS', p.upstreams.map(u => `<code>${esc(u)}</code>`).join(' ') + ' (Google)'], ['DNSSEC', p.dnssec ? 'an' : 'aus'], ['Schnittstelle', `${esc(p.interface)}, Modus ${esc(p.listening)}`], ['DHCP', esc(p.dhcp)], ['NTP-Server', esc(p.ntp)], ['Blocklisten-Update', esc(p.gravity) + ' (cron)'], ['Konfiguration', '<code>/etc/pihole/pihole.toml</code>'], ['Weboberfläche', '<code>:8080</code> http · <code>:443</code> https']]), { ico: 'gear' })}
        ${card('Risiken', bullets(['**Keine Ausfallreserve:** Ist der Pi aus, löst das Heimnetz keine Namen mehr auf.', '**Behoben am 26.10.:** Die Vollsicherung stoppt Pi-hole nicht mehr (Ausfall am 25.10. von 14:42 bis 15:26).', 'Upstream Google ohne DNSSEC – funktional, aber nicht datenschutzfreundlich.', 'Der Fernseher nutzt AdGuard-DNS und taucht in diesen Zahlen nicht auf.']) + `<p class="small" style="margin-top:12px"><a href="#/befunde">Lösungsvorschläge unter Befunde →</a></p>`, { ico: 'alert', cls: 'edge-bad' })}</div>`);
  },
  live() {
    const p = S.ph;
    if (!p?.queries) { setTile('q', { v: 'aus', flag: ['Störung', 'bad'], sub: 'Pi-hole antwortet nicht' }); setHTML('dns-status', note('Pi-hole antwortet nicht.', 'bad')); return; }
    const q = p.queries;
    setTile('q', { v: fmt(q.total, 0), sub: `${fmt(q.frequency, 1)} pro Sekunde · ${fmt(q.unique_domains, 0)} Domains` });
    setTile('bl', { v: fmt(q.percent_blocked), sub: `${fmt(q.blocked, 0)} Anfragen blockiert`, meter: q.percent_blocked });
    setTile('cl', { v: String(p.clients.active), sub: `von ${p.clients.total} bekannten` });
    setTile('gr', { v: fmtBig(p.gravity.domains_being_blocked), sub: 'Stand ' + new Date(p.gravity.last_update * 1000).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' }) });
    const st = q.status;
    setHTML('dns-status', stackBar([
      { label: 'Cache', value: (st.CACHE || 0) + (st.CACHE_STALE || 0) }, { label: 'Weitergeleitet', value: st.FORWARDED || 0 },
      { label: 'Geblockt', value: (st.GRAVITY || 0) + (st.GRAVITY_CNAME || 0) + (st.REGEX || 0) + (st.DENYLIST || 0) + (st.EXTERNAL_BLOCKED_NULL || 0) },
      { label: 'Spezialdomain', value: st.SPECIAL_DOMAIN || 0 }, { label: 'In Bearbeitung', value: st.IN_PROGRESS || 0 }]) +
      `<p class="small muted" style="margin-top:12px">Anteil an allen ${fmt(q.total, 0)} Anfragen der letzten 24 Stunden.</p>`);
    const ty = Object.entries(q.types).filter(e => e[1] > 0).sort((a, b) => b[1] - a[1]).slice(0, 7);
    setHTML('dns-types', bars(ty.map(([n, v]) => ({ label: n, value: v })), { digits: 0 }));
  },
};

PAGES.pi = {
  title: 'Raspberry Pi',
  render() {
    const p = S.kb.pi;
    const containers = [['homeassistant', 'ghcr.io/home-assistant/home-assistant:stable', 'Host-Netz · 8123', 'unless-stopped', 'Smart Home'], ['uni-meter', 'sdeigm/uni-meter:latest', 'Host-Netz · 80, 8181, UDP 1010', 'unless-stopped', 'EcoTracker-Emulation'],
      ['noah-mqtt', 'ghcr.io/mtrossbach/noah-mqtt (0.0.29)', 'energy-stack_default', 'Watchdog-Unit noah-mqtt.service', 'Growatt → MQTT'], ['uptime-kuma', 'louislam/uptime-kuma:1', '0.0.0.0:3001', 'unless-stopped', 'Erreichbarkeit (0 Monitore)'],
      ['fritz_exporter', 'pdreker/fritz_exporter:latest', '0.0.0.0:9787', 'unless-stopped', 'FRITZ!Box-Metriken'], ['fritzbox-gateway-exporter', 'ghcr.io/sberk42/fritzbox_exporter', '127.0.0.1:9042', 'unless-stopped', 'Gateway, Hostliste'],
      ['fritzbox-smarthome-exporter', 'jaymedh/fritzbox_smarthome_exporter', '127.0.0.1:9103', 'unless-stopped', 'FRITZ!-Smart-Home'], ['influxdb-dashboard', 'influxdb:1.8', '127.0.0.1:8087, UDP 25826', 'unless-stopped', 'Legacy-Panels (seit 25.10.)'],
      ['influxdb', 'influxdb:1.8', '–', 'no', 'beendet seit 8 Monaten – Altlast']];
    return head({ kicker: 'Zentraler Server · 10.0.0.110', title: 'Raspberry Pi', ico: 'cpu', text: 'Auf diesem Pi laufen DNS, Home Assistant, MQTT, Energie-Steuerung, Monitoring und alle Websites. Fällt er aus, ist praktisch das ganze Smart Home betroffen.', actions: btn('Pi in der Doku', DOC_URL + '#/pi', { kind: 'cross', ico: 'book', same: true }) }) +
      `<div class="grid cols-4">${tile('cpu', { label: 'CPU', ico: 'cpu', unit: '%', meter: true, sub: 'Mittel über 5 Minuten' })}${tile('ram', { label: 'Arbeitsspeicher', ico: 'database', unit: '%', meter: true })}${tile('disk', { label: 'SD-Karte', ico: 'hdd', unit: '%', meter: true, sub: '128 GB microSD · ext4' })}${tile('temp', { label: 'Temperatur', ico: 'thermo', unit: '°C', meter: true, sub: 'Drosselung ab 80 °C' })}
        ${tile('up', { label: 'Laufzeit', ico: 'clock' })}${tile('load', { label: 'Systemlast (1 min)', ico: 'activity', sub: '4 Kerne' })}${tile('nas', { label: 'NAS-Freigabe', ico: 'folder', sub: '9,1 TB, ca. 3,0 TB belegt' })}${tile('pw', { label: 'Stromaufnahme', ico: 'plug', unit: 'W', sub: 'gemessen an der Steckdose „Raspi“' })}</div>` +
      section('Verlauf', rangeBar() + `<div class="grid cols-2">${chartCard('picpu', { title: 'CPU und Arbeitsspeicher', ico: 'cpu', unit: '%', series: [{ name: 'CPU', q: Q.cpu, slot: 1 }, { name: 'Arbeitsspeicher', q: Q.ram, slot: 2 }] })}${chartCard('pitemp', { title: 'CPU-Temperatur', ico: 'thermo', unit: '°C', yMin: null, series: [{ name: 'Temperatur', q: Q.temp }] })}</div>`) +
      section('Hardware & System', `<div class="grid cols-2">${card('Hardware', kv([['Modell', esc(p.model)], ['CPU', esc(p.cpu)], ['RAM', esc(p.ram)], ['Swap', esc(p.swap)], ['Speicher', esc(p.storage)], ['Strom', 'Tapo P110 „Raspi“ – schaltbar, Vorsicht!']]), { ico: 'cpu' })}
        ${card('Software', kv([['Betriebssystem', esc(p.os)], ['Kernel', esc(p.kernel)], ['Desktop', esc(p.desktop)], ['Laufzeiten', esc(p.runtimes)], ['Zeit', esc(p.tz)], ['Benutzer', '<code>pi</code> (sudo, docker) · SSH mit Schlüssel']]), { ico: 'package' })}
        ${card('Netzwerk', kv(p.net.map(([a, b]) => [a, esc(b)]).concat([['DNS', esc(p.dns)], ['NAS-Mount', `<code>${esc(p.mount)}</code>`]])), { ico: 'network' })}
        ${card('Speicherbelegung', `<div id="pi-fs">${emptyMsg('Lade …')}</div><div style="margin-top:12px">${note('Alles läuft von der microSD-Karte. Datenbanken (Home Assistant, Prometheus 3 GB) schreiben ständig – SD-Karten verschleißen. Backups sind deshalb besonders wichtig.', 'warn')}</div>`, { ico: 'hdd' })}</div>`) +
      section('Docker-Container', card('', `<div class="table-wrap"><table class="t"><thead><tr><th>Container</th><th>Image</th><th>Netz / Ports</th><th>Neustart</th><th>Status</th></tr></thead><tbody>${containers.map(r => `<tr><td class="wide"><div class="nm">${r[0]}</div><div class="ds">${r[4]}</div></td><td class="mono small">${r[1]}</td><td class="small">${r[2]}</td><td class="small">${r[3]}</td><td id="ct-${r[0]}"></td></tr>`).join('')}</tbody></table></div>`, { bodyCls: 'flush' })) +
      section('Zeitgesteuerte Aufgaben', card('', `<div class="table-wrap"><table class="t"><thead><tr><th>Aufgabe</th><th>Wann</th><th>Was</th></tr></thead><tbody>${S.kb.timers.map(t => `<tr><td class="wide nm">${esc(t.name)}</td><td class="nowrap">${esc(t.when)}</td><td class="muted">${esc(t.what)}</td></tr>`).join('')}</tbody></table></div>`, { bodyCls: 'flush' }));
  },
  async live() {
    const [cpu, ram, disk, temp, up, load, nas, pw, fs, fsa, noah, tg] = await Promise.all([val(Q.cpu), val(Q.ram), val(Q.disk), val(Q.temp), val('time()-node_boot_time_seconds'), val('node_load1'),
      getJSON('api/inventory', 60000).then(() => true, () => false), val('homeassistant_sensor_power_w{entity="sensor.1_derzeitiger_verbrauch"}'),
      prom('node_filesystem_size_bytes{fstype=~"ext4|vfat"}'), prom('node_filesystem_avail_bytes{fstype=~"ext4|vfat"}'), val(Q.noah), prom('up')]);
    setTile('cpu', { v: fmt(cpu), meter: cpu, tone: toneFor(cpu, 60, 85) }); setTile('ram', { v: fmt(ram), meter: ram, tone: toneFor(ram, 80, 92) });
    setTile('disk', { v: fmt(disk), meter: disk, tone: toneFor(disk, 75, 90) }); setTile('temp', { v: fmt(temp), meter: (temp || 0) / 85 * 100, tone: toneFor(temp, 70, 80) });
    setTile('up', { v: dur(up), sub: up ? 'seit ' + new Date(Date.now() - up * 1000).toLocaleDateString('de-DE') : '' }); setTile('load', { v: fmt(load, 2) });
    setTile('nas', { v: nas ? 'lesbar' : 'Fehler', flag: nas ? null : ['Störung', 'bad'] }); setTile('pw', { v: fmt(pw) });
    const free = m => num(fsa.find(x => x.metric.mountpoint === m)?.value[1]);
    setHTML('pi-fs', `<div class="table-wrap"><table class="t"><thead><tr><th>Einhängepunkt</th><th>Typ</th><th class="num">Größe</th><th class="num">frei</th></tr></thead><tbody>${fs.map(f => `<tr><td class="mono">${esc(f.metric.mountpoint)}</td><td>${esc(f.metric.fstype)}</td><td class="num">${fmt(num(f.value[1]) / 1e9, 1)} GB</td><td class="num">${fmt(free(f.metric.mountpoint) / 1e9, 1)} GB</td></tr>`).join('')}<tr><td class="mono">/mnt/nas-pi-share</td><td>cifs 3.0</td><td class="num">9,1 TB</td><td class="num">≈ 6,1 TB</td></tr><tr><td class="mono">[SWAP]</td><td>zram</td><td class="num">2 GB</td><td class="num"></td></tr></tbody></table></div>`);
    setHTML('ct-noah-mqtt', noah === 1 ? pill('Daten fließen', 'ok') : pill('prüfen', 'warn'));
    const j = Object.fromEntries(tg.map(t => [t.metric.job, t.value[1] === '1']));
    [['fritz_exporter', 'fritz_exporter'], ['fritzbox-gateway-exporter', 'fritzbox_gateway_exporter'], ['fritzbox-smarthome-exporter', 'fritzbox_smarthome_exporter'], ['homeassistant', 'homeassistant']]
      .forEach(([c, job]) => setHTML('ct-' + c, j[job] === undefined ? '' : j[job] ? pill('läuft', 'ok') : pill('nicht erreichbar', 'bad')));
    setHTML('ct-influxdb', pill('beendet'));
  },
};

PAGES.dienste = {
  title: 'Dienste & Ports',
  render() {
    const sv = S.kb.services, cats = [...new Set(sv.map(s => s.cat))];
    const lan = sv.filter(s => /alle|0\.0\.0\.0/.test(s.bind)).length, loc = sv.filter(s => s.bind.startsWith('127')).length, dock = sv.filter(s => /Docker/.test(s.kind)).length;
    const CI = { Netzwerk: 'shield', 'Smart Home': 'home', Energie: 'bolt', Monitoring: 'activity', Websites: 'globe', System: 'gear' };
    return head({ kicker: 'Was auf dem Pi läuft', title: 'Dienste & Ports', ico: 'layers', text: 'Vollständiger Katalog aller Dienste auf dem Raspberry Pi mit Port, Erreichbarkeit, Version, Konfiguration und Datenpfad. „im LAN“ heißt: jedes Gerät im Heimnetz erreicht den Dienst – es gibt keine Firewall.' }) +
      `<div class="grid cols-4">${tile('n', { label: 'Dienste', ico: 'layers', sub: `${cats.length} Bereiche` })}${tile('lan', { label: 'Im ganzen LAN offen', ico: 'globe', sub: 'ohne Firewall' })}${tile('loc', { label: 'Nur lokal', ico: 'lock', sub: 'an 127.0.0.1 gebunden' })}${tile('dock', { label: 'Davon in Docker', ico: 'package', sub: '9 Container' })}</div>` +
      cats.map(c => section(esc(c), card('', `<div class="table-wrap"><table class="t"><thead><tr><th>Dienst</th><th>Port</th><th>Erreichbar</th><th>Betrieb</th><th>Version</th><th>Konfiguration / Daten</th></tr></thead><tbody>${sv.filter(s => s.cat === c).map(s => `<tr><td class="wide"><div class="cell-ico"><span class="ci">${icon(CI[c] || 'layers')}</span><div><div class="nm">${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a>` : esc(s.name)}</div><div class="ds">${esc(s.purpose)}</div></div></div></td><td class="mono small" style="min-width:140px">${esc(s.port)}</td><td>${s.bind.startsWith('127') ? pill('nur lokal', 'ok') : s.bind === '–' ? '–' : /alle|0\.0\.0\.0/.test(s.bind) ? pill('im LAN', 'warn') : pill(s.bind)}</td><td class="small">${esc(s.kind)}</td><td class="mono small">${esc(s.version || '–')}</td><td class="path">${esc(s.config)}${s.data ? `<br><span class="faint">${esc(s.data)}</span>` : ''}</td></tr>`).join('')}</tbody></table></div>`, { bodyCls: 'flush' }))).join('');
  },
  mount() {
    const sv = S.kb.services;
    setTile('n', { v: String(sv.length) }); setTile('lan', { v: String(sv.filter(s => /alle|0\.0\.0\.0/.test(s.bind)).length) });
    setTile('loc', { v: String(sv.filter(s => s.bind.startsWith('127')).length) }); setTile('dock', { v: String(sv.filter(s => /Docker/.test(s.kind)).length) });
  },
};

PAGES.monitoring = {
  title: 'Monitoring',
  render() {
    return head({ kicker: 'Prometheus · Grafana · Uptime Kuma', title: 'Monitoring', ico: 'activity', text: 'Wie Messwerte entstehen, wo sie gespeichert werden und was davon überwacht wird.', actions: btn('Prometheus-Ziele', `http://${PI}:9090/targets`, { ico: 'activity' }) + btn('Grafana', `http://${PI}:3000/`, { ico: 'chart' }) }) +
      `<div class="grid cols-4">${tile('tg', { label: 'Scrape-Ziele erreichbar', ico: 'activity' })}${tile('ser', { label: 'Aktive Zeitreihen', ico: 'chart', sub: 'TSDB-Kopf' })}${tile('ret', { label: 'Aufbewahrung', ico: 'database', sub: '/var/lib/prometheus · 3,0 GB' })}${tile('alr', { label: 'Alarmierung', ico: 'bell' })}</div>` +
      section('Scrape-Ziele', `<div class="grid cols-2">${card('Status je Ziel', '<div id="tgt"></div>', { ico: 'list', bodyCls: 'flush' })}${card('Scrape-Dauer je Ziel', '<div id="scr"></div>', { ico: 'clock', aside: 'letzter Abruf' })}</div>`, { sub: 'global alle 15 s · Timeout 10 s' }) +
      section('Verlauf', rangeBar() + `<div class="grid cols-2">${chartCard('winc', { title: 'Windows-PC · CPU', ico: 'monitor', unit: '%', series: [{ name: 'CPU', q: Q.wincpu }] })}${chartCard('series', { title: 'Aktive Zeitreihen in Prometheus', ico: 'chart', unit: '', digits: 0, yMin: null, series: [{ name: 'Zeitreihen', q: 'prometheus_tsdb_head_series' }] })}</div>`) +
      section('Bestandteile', `<div class="grid cols-2">${card('Prometheus 2.53.3', kv([['Konfiguration', '<code>/etc/prometheus/prometheus.yml</code>'], ['Start-Parameter', '<code>--storage.tsdb.retention.time=35d</code>'], ['Daten', '<code>/var/lib/prometheus</code> (3,0 GB)'], ['Alertmanager', pill('konfiguriert, läuft nicht', 'warn') + ' <span class="small muted">localhost:9093</span>'], ['Regeln', 'keine Regeldateien'], ['Label', '<code>monitor: example</code> (Standardwert)']]), { ico: 'database' })}
        ${card('Grafana 13.2.4', bars([['Fritz!Exporter', 31], ['FRITZ!Box Status', 20], ['Netzwerk (Fritzbox)', 18], ['Fritz!Exporter Dash', 16], ['Solar & Energie', 15], ['FRITZ!Box Router Status', 14], ['FRITZ!Box Smarthome', 11], ['System (Windows-PC)', 10], ['System (Pi)', 6]].map(([l, v]) => ({ label: l, value: v })), { unit: 'Panels' }) + `<p class="small muted" style="margin-top:12px">Datenquellen: Prometheus und InfluxDB (collectd, Legacy). Zusätzlich installiert: Paket grafana-enterprise 12.3.2 (Altlast).</p>`, { ico: 'chart', aside: '9 Dashboards' })}
        ${card('Uptime Kuma', `<p>Läuft als Container auf <code>:3001</code>, Daten im Volume <code>uptime-kuma-data</code>.</p>${note('Es sind <b>keine Monitore</b> und keine Benachrichtigungen eingerichtet. Ausfälle meldet deshalb niemand.', 'warn')}`, { ico: 'bell' })}
        ${card('InfluxDB & collectd (Legacy)', bullets(['Nativ InfluxDB 1.6.7 auf `:8086` – nur Datenbank `_internal`.', 'Container `influxdb-dashboard` (1.8) auf `127.0.0.1:8087` – liest alte Daten aus `/home/pi/influxdb`.', 'Container `influxdb` seit 8 Monaten beendet.', 'collectd 5.12 schreibt RRD-Dateien ohne Abnehmer.']), { ico: 'database' })}</div>`);
  },
  async live() {
    const [up, dr, smp, ser] = await Promise.all([prom('up'), prom('scrape_duration_seconds'), prom('scrape_samples_scraped'), val('prometheus_tsdb_head_series')]);
    const g = (a, t) => num(a.find(x => x.metric.job === t.metric.job && x.metric.instance === t.metric.instance)?.value[1]);
    const ok = up.filter(t => t.value[1] === '1').length;
    setTile('tg', { v: `${ok}/${up.length}`, flag: ok === up.length ? ['alle', 'ok'] : ['Ausfall', 'bad'] });
    setTile('ser', { v: fmt(ser, 0) }); setTile('ret', { v: '35 Tage' }); setTile('alr', { v: 'keine', sub: 'kein Alertmanager, 0 Kuma-Monitore', flag: ['Lücke', 'warn'] });
    const iv = Object.fromEntries((S.kb.prom_jobs || []).map(j => [j.job, j.interval]));
    setHTML('tgt', `<div class="table-wrap"><table class="t"><thead><tr><th>Status</th><th>Job · Ziel</th><th class="num">Messwerte</th></tr></thead><tbody>${up.map(t => `<tr><td>${t.value[1] === '1' ? pill('erreichbar', 'ok') : pill('down', 'bad')}</td><td><div class="nm">${esc(t.metric.job)}</div><div class="ds"><span class="mono">${esc(t.metric.instance)}</span> · alle ${esc(iv[t.metric.job] || '15 s')}</div></td><td class="num">${fmt(g(smp, t), 0)}</td></tr>`).join('')}</tbody></table></div>`);
    setHTML('scr', bars(up.map(t => ({ label: t.metric.job, value: g(dr, t) })).sort((a, b) => (b.value || 0) - (a.value || 0)), { unit: 's', digits: 3 }));
  },
};

PAGES.backups = {
  title: 'Backups',
  render() {
    const B = S.kb.backups, st = b => b.state === 'ok' ? ['gesichert', 'ok'] : b.state === 'warn' ? ['Lücke', 'warn'] : ['unbekannt', 'bad'];
    return head({ kicker: 'Datensicherung', title: 'Backups', ico: 'archive', text: 'Was wohin gesichert wird, wie oft, wie lange es aufbewahrt wird – und wo Lücken sind. Ziel aller Sicherungen ist die Synology-NAS (Freigabe pi-share).' }) +
      `<div class="grid cols-3">${B.map(b => `<div class="card" style="padding:18px;display:flex;flex-direction:column;gap:12px"><div style="display:flex;align-items:center;gap:10px"><span class="dev-i" style="width:38px;height:38px">${icon(b.state === 'ok' ? 'shield' : 'alert')}</span><h3 style="font-size:15px;flex:1">${esc(b.what)}</h3>${pill(...st(b))}</div>${kv([['Wohin', `<span class="mono small">${esc(b.where)}</span>`], ['Wann', esc(b.when)], ['Aufbewahrung', esc(b.keep)], ['Letzte', esc(b.last)]])}${b.note ? `<p class="small muted" style="margin:0">${esc(b.note)}</p>` : ''}</div>`).join('')}</div>` +
      section('raspiBackup im Detail', `<div class="grid cols-2">${card('Konfiguration', kv([['Version', 'raspiBackup 0.7.4 (Config 0.1.11)'], ['Ziel', '<code>/mnt/nas-pi-share/system-backups/raspi</code>'], ['Typ', 'tar, gzip-komprimiert'], ['Generationen', '8'], ['Zeitplan', 'systemd-Timer So 06:00; prüft den NAS-Mount, wartet 180 s'], ['Ausschlüsse', '<code>/var/log/pihole/*</code>, <code>/etc/pihole/pihole-FTL.db*</code>'], ['tar-Warnung RC 1', 'wird toleriert (Dateien ändern sich beim Lesen)'], ['Benachrichtigung', 'keine (Telegram/Pushover/Mail aus)']]), { ico: 'gear' })}
        ${card('Was während der Sicherung gestoppt wird', steps(['noah-mqtt (Watchdog-Unit)', 'Grafana, Prometheus, InfluxDB, Mosquitto', 'Docker + containerd (Home Assistant, uni-meter, Exporter, Kuma)']) + `<div style="margin-top:14px">${note('Während der Sicherung laufen keine Smart-Home-Automationen und keine Nulleinspeisungs-Regelung. <b>DNS (Pi-hole) läuft seit 26.10. weiter</b> – vorher fiel dabei das Internet im ganzen Netz aus.', 'warn')}</div>`, { ico: 'list' })}</div>`) +
      section('Wiederherstellung', card('Pi aus der Vollsicherung zurückspielen', steps((S.kb.runbooks.find(r => r.title.startsWith('Pi aus')) || { steps: [] }).steps) + `<div style="margin-top:14px">${note('Eine Wiederherstellung wurde noch nie getestet. Empfehlung: einmal auf eine Ersatz-SD-Karte zurückspielen und booten.', 'warn')}</div>`, { ico: 'terminal' }));
  },
};

const BAT_NAMES = { nutzer: 'iPhone', ipad: 'iPad', gaming_zimmer: 'Thermostat Gaming Zimmer', wohnzimmer_links: 'Thermostat Wohnzimmer links', wohnzimmer_rechts: 'Thermostat Wohnzimmer rechts', schlafzimmer_links: 'Thermostat Schlafzimmer links', schlafzimmer_rechts: 'Thermostat Schlafzimmer rechts' };
const batName = e => { const k = e.replace('sensor.', '').replace(/_batterie$|_battery_level$/, ''); return BAT_NAMES[k] || k.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase()); };
const ROOMS = [
  { n: 'Wohnzimmer', t: 'sensor.temperature_humidity_sensor_temperatur', h: 'sensor.temperature_humidity_sensor_luftfeuchtigkeit', c: ['climate.wohnzimmer_links', 'climate.wohnzimmer_rechts'] },
  { n: 'Schlafzimmer', t: 'sensor.temperature_humidity_sensor_temperatur_2', h: 'sensor.temperature_humidity_sensor_luftfeuchtigkeit_2', c: ['climate.schlafzimmer', 'climate.schlafzimmer_links', 'climate.schlafzimmer_rechts'] },
  { n: 'Gaming Zimmer / Zelt', t: 'sensor.temperatur_und_luftfeuchtigkeitssensor_temperatur', h: 'sensor.temperatur_und_luftfeuchtigkeitssensor_luftfeuchtigkeit', c: ['climate.gaming_zimmer'] },
];
const PLUGS = [['Raspi', 'switch.1', 'sensor.1_derzeitiger_verbrauch', 'Wohnzimmer'], ['PC', 'switch.pc', 'sensor.pc_derzeitiger_verbrauch', 'Wohnzimmer'], ['TV', 'switch.tv', 'sensor.tv_derzeitiger_verbrauch', 'Wohnzimmer'],
  ['Router-Licht', 'switch.router', 'sensor.router_derzeitiger_verbrauch', 'Wohnzimmer'], ['3D-Drucker', 'switch.drucker_3d', 'sensor.drucker_3d_derzeitiger_verbrauch', 'Gaming Zimmer'], ['Zelt', 'switch.zelt', 'sensor.zelt_derzeitiger_verbrauch', 'Gaming Zimmer'],
  ['Kühlschrank', 'switch.kuhlschrank', 'sensor.kuhlschrank_derzeitiger_verbrauch', 'Küche'], ['Spülmaschine', 'switch.spulmaschine', 'sensor.spulmaschine_derzeitiger_verbrauch', 'Küche'], ['Küche Boiler', 'switch.kuche_boiler', 'sensor.kuche_boiler_derzeitiger_verbrauch', 'Küche'],
  ['iPad-Station', 'switch.ipad_station', 'sensor.ipad_station_derzeitiger_verbrauch', 'Schlafzimmer'], ['Luftreiniger', 'switch.luftreiniger', 'sensor.luftreiniger_derzeitiger_verbrauch', 'Schlafzimmer'], ['Bett', 'switch.steckdose_bett', 'sensor.steckdose_bett_derzeitiger_verbrauch', 'Schlafzimmer']];
PAGES.smarthome = {
  title: 'Smart Home',
  render() {
    const h = S.kb.ha;
    return head({ kicker: `Home Assistant ${esc(h.version)}`, title: 'Smart Home', ico: 'home', text: `${h.counts.geraete} Geräte, ${h.counts.integrationen} Integrationen, ${h.counts.sensoren} Sensoren und ${h.counts.schalter} Schalter in den Bereichen ${h.areas.join(', ')}.`, actions: btn('Home Assistant öffnen', `http://${PI}:8123/`, { kind: 'primary', ico: 'home' }) }) +
      `<div class="grid cols-3">${ROOMS.map((r, i) => `<div class="card room"><div class="room-h"><span class="dev-i" style="width:34px;height:34px">${icon('home')}</span><h3>${r.n}</h3><span style="margin-left:auto" id="rh-${i}"></span></div><div class="room-t"><span class="v" id="rt-${i}">–</span><span class="u">°C</span></div><div class="small muted">Raumtemperatur (Tapo T310)</div><div id="rc-${i}" style="margin-top:14px"></div></div>`).join('')}</div>
      <p class="small muted" style="margin-top:10px">5 Thermostate Tapo KE100 am Hub KH100. Das Schlafzimmer regelt zusätzlich <b>Better Thermostat</b> (<code>climate.schlafzimmer</code>); Wohnzimmer und Gaming Zimmer werden direkt über ihre Thermostate gesteuert.</p>` +
      `<div class="grid cols-2" style="margin-top:20px">${card('Steckdosen · Leistung jetzt', '<div id="plugs"></div>', { ico: 'plug', aside: '<span id="plug-sum"></span>' })}<div class="stack-v">${card('Automationen', '<ul class="rows" id="autos"></ul>', { ico: 'bolt' })}${card('Batterien', '<div id="bats"></div>', { ico: 'battery', aside: 'Sensoren & Thermostate' })}</div></div>` +
      section('Integrationen', card('', `<ul class="rows">${h.integrations.map(([n, d]) => `<li><span class="grow"><b>${esc(n)}</b></span><span class="small muted" style="text-align:right">${esc(d)}</span></li>`).join('')}</ul>`), { sub: `${h.counts.integrationen} Integrationen` }) +
      section('Betrieb', card('', kv([['Installation', esc(h.install)], ['Datenbank', '<code>home-assistant_v2.db</code> auf der SD-Karte'], ['Backups', 'täglich automatisch, nur lokal (siehe <a href="#/backups">Backups</a>)'], ['Erweiterungen (HACS)', 'alexa_media, bambu_lab, better_thermostat, ha_mcp_tools'], ['Prometheus-Export', '<code>/api/prometheus</code> (alle Entitäten)'], ['MQTT', 'Mosquitto auf dem Pi, anonym']])));
  },
  async live() {
    const [cur, tgt, act, temp, hum, pw, sw, bat] = await Promise.all([byLabel('homeassistant_climate_current_temperature_celsius'), byLabel('homeassistant_climate_target_temperature_celsius'), prom('homeassistant_climate_action==1'),
      byLabel('homeassistant_sensor_temperature_celsius'), byLabel('homeassistant_sensor_humidity_percent'), byLabel('homeassistant_sensor_power_w'), byLabel('homeassistant_switch_state'), byLabel('homeassistant_sensor_battery_percent')]);
    const action = Object.fromEntries(act.map(a => [a.metric.entity, a.metric.action]));
    ROOMS.forEach((r, i) => {
      setText('rt-' + i, temp[r.t] !== undefined ? fmt(temp[r.t]) : '–');
      setHTML('rh-' + i, hum[r.h] !== undefined ? pill(`${fmt(hum[r.h], 0)} % Feuchte`, '', 'drop') : '');
      setHTML('rc-' + i, r.c.map(c => { const a = action[c]; return `<div class="therm"><span class="tn">${esc(c.replace('climate.', '').replace(/_/g, ' '))}</span><span class="tv"><b>${fmt(cur[c])} °C</b> · Soll ${fmt(tgt[c])} °C</span>${a === 'heating' ? pill('heizt', 'accent', 'flame') : a === 'idle' ? pill('wartet') : pill(a === 'off' ? 'aus' : (a || '–'))}</div>`; }).join(''));
    });
    const items = PLUGS.map(([n, s, p, room]) => ({ label: n, sub: `${room} · ${sw[s] === undefined ? 'Zustand unbekannt' : sw[s] ? 'an' : 'aus'}`, value: pw[p] === undefined ? null : pw[p] })).sort((a, b) => (b.value ?? -1) - (a.value ?? -1));
    setHTML('plugs', bars(items, { unit: 'W', digits: 1 }));
    setText('plug-sum', `Summe ${fmt(items.reduce((a, i) => a + (i.value || 0), 0), 0)} W`);
    const au = S.ha?.automations || [];
    setHTML('autos', S.kb.ha.automations.map(a => { const l = au.find(x => x.entity_id === a.id); return `<li><div class="grow"><b>${esc(a.name)}</b><div class="small muted">${esc(a.desc)}</div></div><div style="text-align:right;flex:none">${!l ? pill('?') : l.enabled ? pill('aktiv', 'ok') : pill('deaktiviert', 'warn')}<div class="small muted" style="margin-top:4px">${l ? ago(l.last_triggered) : ''}</div></div></li>`; }).join(''));
    const B = Object.entries(bat).filter(([e]) => !e.includes('noah') && !e.includes('0pvp')).sort((a, b) => a[1] - b[1]);
    setHTML('bats', B.length ? `<div class="bars">${B.map(([e, v]) => `<div class="bar-row" tabindex="0" data-tip="${esc(e)}: ${fmt(v, 0)} %"><div class="lbl"><div class="nm">${esc(batName(e))}</div></div><div style="display:grid;grid-template-columns:1fr 52px;align-items:center;gap:10px">${meterHTML(v, v <= 20 ? 'bad' : v <= 35 ? 'warn' : '')}<b class="small" style="text-align:right">${fmt(v, 0)} %</b></div></div>`).join('')}</div>` : emptyMsg('Keine Batteriewerte.'));
  },
};

PAGES.energie = {
  title: 'Energie & Speicher',
  render() {
    return head({ kicker: 'Growatt NOAH 2000 · Nulleinspeisung', title: 'Energie & Speicher', ico: 'bolt', text: 'Der Balkonspeicher speist nur so viel ein, wie im Haushalt gerade verbraucht wird. Dafür misst Home Assistant den Hausverbrauch und spielt ihn dem NOAH als Stromzähler vor.', actions: btn('Solar-Dashboard', `http://${PI}:8093/`, { ico: 'sun' }) }) +
      `<div class="grid cols-4">${tile('pv', { label: 'Solar (PV)', ico: 'sun', unit: 'W', spark: true })}${tile('house', { label: 'Hausverbrauch', ico: 'home', unit: 'W', spark: true })}${tile('chg', { label: 'Speicher', ico: 'battery', unit: 'W' })}${tile('soc', { label: 'Ladestand', ico: 'battery', unit: '%', meter: true, sub: 'gesamt, 2 Module' })}</div>` +
      `<div style="margin-top:16px">${card('Energiefluss jetzt', `<div class="ef-wrap">${energyFlow('en')}</div>` + `<div style="margin-top:6px">${note('Ein echter Netzzähler am Hausanschluss ist nicht angebunden. Netzbezug und Einspeisung werden deshalb nicht berechnet – die Linien zeigen nur, was gemessen wird.')}</div>`, { ico: 'bolt' })}</div>` +
      section('Solarmodule', card('Was jedes Panel gerade erzeugt', pvPanels() + `<div style="margin-top:12px">${note('Der NOAH misst nur die Summe beider Panels. Die Werte je Panel sind diese Summe geteilt durch zwei.')}</div>`, { ico: 'sun', aside: '2 Panels' }) +
        `<div class="grid cols-2" style="margin-top:16px">${chartCard('epanel', { title: 'Leistung je Panel', ico: 'sun', unit: 'W', digits: 0, series: [{ name: 'Panel 1', q: `${Q.pv} / 2`, slot: 2 }, { name: 'Panel 2', q: `${Q.pv} / 2`, slot: 4 }], foot: 'Beide Linien sind gleich: gemessen wird nur die Summe.' })}${chartCard('eyieldp', { title: 'Tagesertrag je Panel', ico: 'chart', unit: 'kWh', digits: 2, series: [{ name: 'je Panel', q: 'homeassistant_sensor_energy_kwh{entity="sensor.noah_2000_generation_today"} / 2', slot: 2 }], foot: 'Zählt über den Tag hoch und beginnt um Mitternacht bei null.' })}</div>`) +
      section('So funktioniert die Regelung', `<div class="flowsteps">${S.kb.energy_flow.map(([t, m, p]) => `<div class="fstep"><h3>${esc(t)}</h3><div class="m">${esc(m)}</div><p>${esc(p)}</p></div>`).join('')}</div><div style="margin-top:14px">${note('„Hausverbrauch“ ist ein Template-Sensor in Home Assistant. Fallen MQTT, uni-meter oder die Automation aus, regelt der NOAH nach dem letzten bekannten Wert.', 'warn')}</div>`) +
      section('Verlauf', rangeBar() + `<div class="grid cols-2">${chartCard('ehh', { title: 'Hausverbrauch und Solarleistung', ico: 'bolt', unit: 'W', digits: 0, series: [{ name: 'Hausverbrauch', q: Q.house, slot: 1 }, { name: 'Solar (PV)', q: Q.pv, slot: 2 }] })}${chartCard('esoc', { title: 'Ladestand des Speichers', ico: 'battery', unit: '%', digits: 0, series: [{ name: 'Ladestand', q: Q.soc, slot: 3 }] })}</div>`) +
      section('Speicher', `<div class="grid cols-3">${card('Batteriemodul 1 (BAT0)', '<div id="bat0"></div>', { ico: 'battery' })}${card('Batteriemodul 2 (BAT1)', '<div id="bat1"></div>', { ico: 'battery' })}${card('Erträge & Verbrauch', '<div id="eyield"></div>', { ico: 'chart' })}</div>`) +
      section('Technik', `<div class="grid cols-2">${card('noah-mqtt', kv([['Image', 'ghcr.io/mtrossbach/noah-mqtt 0.0.29'], ['Modus', 'Growatt-API web+app'], ['Abfrage', 'alle 60 s, Batteriedetails/Parameter alle 300 s'], ['Broker', '10.0.0.110:1883'], ['Absicherung', '<code>noah-mqtt.service</code> startet den Container nach einem Absturz nach 6 min neu'], ['Bekanntes Problem', 'Absturz bei Growatt-Login-Fehler 507 (zuletzt 26.10. 07:40)']]), { ico: 'package' })}
        ${card('uni-meter', kv([['Image', 'sdeigm/uni-meter:latest (Java), Host-Netz'], ['Eingang', 'MQTT <code>home/hausverbrauch</code>, JSON <code>$.power</code>, einphasig'], ['Ausgang', 'EcoTracker-Emulation, UDP 1010, Abfrage alle 3 s'], ['HTTP', ':8181 und :80 (Shelly-kompatibel)'], ['Konfiguration', '<code>/home/pi/docker/energy-stack/uni-meter/uni-meter.conf</code>']]), { ico: 'gear' })}</div>`);
  },
  async live() {
    const [p, b, t, e, sPv, sHouse] = await Promise.all([byLabel('homeassistant_sensor_power_w'), byLabel('homeassistant_sensor_battery_percent'), byLabel('homeassistant_sensor_temperature_celsius'), byLabel('homeassistant_sensor_energy_kwh'), range(Q.pv, 6), range(Q.house, 6)]);
    const pv = p['sensor.noah_2000_solar_power'], house = p['sensor.hausverbrauch'], chg = p['sensor.noah_2000_charging_power'], dis = p['sensor.noah_2000_discharge_power'], out = p['sensor.noah_2000_output_power'], soc = b['sensor.0pvp00ed26ut016f_soc'];
    setTile('pv', { v: fmt(pv, 0), sub: `heute ${fmt(e['sensor.noah_2000_generation_today'], 2)} kWh` }); spark('ts-pv', sPv[0]?.pts);
    setTile('house', { v: fmt(house, 0), sub: `Speicher-Ausgang ${fmt(out, 0)} W` }); spark('ts-house', sHouse[0]?.pts);
    setTile('chg', { v: fmt((chg || 0) > 1 ? chg : dis, 0), sub: (chg || 0) > 1 ? 'lädt' : (dis || 0) > 1 ? 'entlädt' : 'in Ruhe' });
    setTile('soc', { v: fmt(soc, 0), meter: soc });
    updateEnergy('en', { pv, house, soc, chg, dis, out });
    updatePanels(pv, e['sensor.noah_2000_generation_today'], e['sensor.noah_2000_generation_total']);
    const mod = (s, tmp) => `<div class="room-t" style="margin-top:0"><span class="v">${fmt(b[s], 0)}</span><span class="u">%</span></div>${meterHTML(b[s])}<p class="small muted" style="margin:10px 0 0">${icon('thermo')} Temperatur ${fmt(t[tmp])} °C</p>`;
    setHTML('bat0', mod('sensor.noah_2000_bat0_soc', 'sensor.noah_2000_bat0_temperature'));
    setHTML('bat1', mod('sensor.0pvp00ed26ut016f_bat1_soc', 'sensor.0pvp00ed26ut016f_bat1_temperature'));
    setHTML('eyield', kv([['PV heute', fmt(e['sensor.noah_2000_generation_today'], 2) + ' kWh'], ['PV gesamt', fmt(e['sensor.noah_2000_generation_total'], 1) + ' kWh'], ['Haus heute', fmt(e['sensor.hausverbrauch_heute'], 2) + ' kWh'], ['Haus diesen Monat', fmt(e['sensor.hausverbrauch_diesen_monat'], 1) + ' kWh'], ['Haus dieses Jahr', fmt(e['sensor.hausverbrauch_dieses_jahr'], 0) + ' kWh']]));
  },
};

PAGES.jarvis = {
  title: 'J.A.R.V.I.S.',
  render() {
    const j = S.kb.jarvis;
    return head({ kicker: 'Sprachassistent auf dem Windows-PC', title: 'J.A.R.V.I.S. im Netz', ico: 'sparkles', text: 'Jarvis läuft auf dem Windows-PC und nutzt die Dienste im Heimnetz. Hier steht, wie er eingebunden ist; die Projektgeschichte steht in der Doku.', actions: btn('HUD öffnen', 'http://10.0.0.145:8720/', { kind: 'primary', ico: 'sparkles' }) + btn('Projektakte', DOC_URL + '#/projekt/jarvis', { kind: 'cross', ico: 'book', same: true }) }) +
      `<div class="grid cols-2">${card('Einbindung', kv([['Host', esc(j.host)], ['Port', `<code>${j.port}</code> (HUD, auch für iPad/iPhone)`], ['Status', '<span id="jv">…</span>'], ['Aktivierungswort', esc(j.wake)], ['Sprachausgabe', esc(j.tts)], ['Unterwegs', esc(j.remote)]]), { ico: 'link' })}
        ${card('Nutzt im Heimnetz', bullets(j.uses), { ico: 'network' })}
        ${card('Tailscale-Tailnet', `<div class="table-wrap"><table class="t"><tbody>${S.kb.tailscale.map(([n, ip]) => `<tr><td>${esc(n)}</td><td class="mono num">${esc(ip)}</td></tr>`).join('')}</tbody></table></div><p class="small muted" style="margin-top:10px">Der Raspberry Pi ist nicht im Tailnet; unterwegs ist er nur über den PC oder die FRITZ!Box-VPN erreichbar.</p>`, { ico: 'lock' })}
        ${card('Heizung (config/home.json)', `<div class="table-wrap"><table class="t"><tbody><tr><td>Schlafzimmer</td><td class="mono small">climate.schlafzimmer (Better Thermostat)</td></tr><tr><td>Wohnzimmer</td><td class="mono small">climate.wohnzimmer_links + _rechts</td></tr><tr><td>Gaming</td><td class="mono small">climate.gaming_zimmer</td></tr></tbody></table></div><p class="small muted" style="margin-top:10px">Am 26.10. korrigiert – vorher zeigten Wohnzimmer und Gaming auf nicht existierende Entitäten. Ein Befehl schaltet alle Entitäten des Raums.</p>`, { ico: 'flame' })}</div>`;
  },
  async live() { const w = await val('up{job="windows"}'); setHTML('jv', w === 1 ? pill('PC online', 'ok') : pill('PC aus oder nicht erreichbar')); },
};

PAGES.runbooks = {
  title: 'Runbooks',
  render() {
    return head({ kicker: 'Wenn etwas nicht geht', title: 'Runbooks', ico: 'terminal', text: 'Schritt-für-Schritt-Anleitungen für typische Störungen. Befehle auf dem Pi über <code>ssh pi@10.0.0.110</code> ausführen; jeder Befehl lässt sich mit einem Klick kopieren.' }) +
      `<div class="grid">${S.kb.runbooks.map((r, i) => `<details class="card acc"${i === 0 ? ' open' : ''}><summary><span class="ico">${icon('terminal')}</span><span>${esc(r.title)}</span>${icon('chevron', 'chev')}</summary><div class="acc-b">${steps(r.steps)}</div></details>`).join('')}</div>`;
  },
};

PAGES.websites = {
  title: 'Websites',
  render() {
    return head({ kicker: 'Nginx auf dem Pi', title: 'Websites', ico: 'globe', text: 'Alle Websites laufen über Nginx auf dem Raspberry Pi, jede auf einem eigenen Port. Die Lese-APIs geben nur zusammengefasste Werte heraus; Zugangsdaten bleiben auf dem Server.' }) +
      `<div class="grid cols-3">${S.kb.websites.map(w => `<a class="card proj link" href="http://${PI}:${w.port}/"${w.port === 8092 ? '' : ' target="_blank" rel="noopener"'}><div class="proj-top"><span class="p-ico">${icon(w.port === 8094 ? 'book' : w.port === 8093 ? 'sun' : w.port === 8092 ? 'activity' : w.port === 8091 ? 'cpu' : 'server')}</span>${monoChip(':' + w.port)}<span style="margin-left:auto" id="ws-${w.port}"></span></div><h3>${esc(w.name)}</h3><p>${esc(w.note)}</p><div class="proj-foot">${icon('folder')}<span class="mono">${esc(w.root)}</span></div></a>`).join('')}</div>` +
      section('Lese-APIs dieser Seite', card('', `<div class="table-wrap"><table class="t"><thead><tr><th>Pfad</th><th>Ziel</th><th>Inhalt</th></tr></thead><tbody>${[['/api/query, /api/query_range', '127.0.0.1:9090', 'Prometheus-Abfragen (nur GET)'], ['/api/ha/summary', '127.0.0.1:18095', 'Home-Assistant-Zusammenfassung (Allowlist + Kategorien)'], ['/api/pihole/summary', '127.0.0.1:18095', 'Pi-hole-Statistik ohne Einzelabfragen'], ['/api/inventory', '127.0.0.1:8094/atlas-data.json', 'Geräte, Wissen und Änderungen aus der Doku (gemeinsame Quelle)'], ['/data/kb.json', 'statisch', 'Rückfall, falls die Doku nicht erreichbar ist']].map(([a, b, c]) => `<tr><td class="mono nowrap">${a}</td><td class="mono small">${b}</td><td>${c}</td></tr>`).join('')}</tbody></table></div>`, { bodyCls: 'flush' })) +
      section('Pflege', card('Diese Seite aktualisieren', steps(['Geräte und Wissen pflegst du in der Doku (NAS: `network-documentation/source/inventory/`). Diese Seite übernimmt sie automatisch.', 'Oberfläche liegt in `/var/www/monitoring` (index.html, assets/). Releases liegen auf der NAS unter `network-documentation/releases/orbit-monitor/`.', 'sudo cp -a /var/www/monitoring /var/www/monitoring.previous-$(date +%Y%m%d-%H%M%S)', 'Neue Dateien nach `/var/www/monitoring` kopieren – Nginx muss nicht neu geladen werden.']), { ico: 'wrench' }));
  },
  async live() {
    for (const w of S.kb.websites) {
      try { await fetch(`http://${PI}:${w.port}/`, { mode: 'no-cors', cache: 'no-store' }); setHTML('ws-' + w.port, pill('erreichbar', 'ok')); }
      catch (e) { setHTML('ws-' + w.port, pill('nicht erreichbar', 'bad')); }
    }
  },
};

Object.assign(PAGES, SEC_PAGES); // Sicherheitszentrale & Netzwerkprüfung (security.js)

/* ------------------------------------------------------------------ Navigation, Suche, Start */
const NAV = [
  ['Überblick', [['', 'Übersicht', 'dashboard'], ['befunde', 'Befunde & To-dos', 'alert', () => ({ n: S.kb.findings.filter(f => f.sev === 'hoch').length, alert: true })], ['aenderungen', 'Änderungen', 'history']]],
  SEC_NAV,
  ['Netz & Geräte', [['netz', 'Netzwerk', 'network'], ['geraete', 'Geräte', 'devices', () => S.kb.devices.length], ['dns', 'DNS & Pi-hole', 'shield']]],
  ['Server & Dienste', [['pi', 'Raspberry Pi', 'cpu'], ['dienste', 'Dienste & Ports', 'layers'], ['monitoring', 'Monitoring', 'activity'], ['backups', 'Backups', 'archive']]],
  ['Zuhause', [['smarthome', 'Smart Home', 'home'], ['energie', 'Energie & Speicher', 'bolt'], ['jarvis', 'J.A.R.V.I.S.', 'sparkles']]],
  ['Betrieb', [['runbooks', 'Runbooks', 'terminal'], ['websites', 'Websites', 'globe']]],
  ['Andere Seite', [[DOC_URL, 'Dokumentation :8094', 'book']]],
];
function searchIndex() {
  const L = [];
  NAV.flatMap(g => g[1]).filter(([r]) => !/^https?:/.test(r)).forEach(([r, n, ic]) => L.push({ type: 'Bereiche', title: n, href: '#/' + r, ico: ic, k: n }));
  L.push({ type: 'Bereiche', title: 'Dokumentation (ATLAS)', sub: 'Projekte, Zeitleiste, Inventar · Port 8094', href: DOC_URL, ico: 'book', k: 'doku dokumentation atlas projekte zeitleiste' });
  S.kb.devices.forEach(d => L.push({ type: 'Geräte', title: d.name, sub: [firstIp(d), d.room, d.group].filter(Boolean).join(' · '), href: '#/geraet/' + devId(d), ico: devIcon(d), k: [d.name, d.ip, d.mac, d.host, d.room, d.model, d.maker, d.role, ...(d.ha || [])].join(' ') }));
  S.kb.services.forEach(s => L.push({ type: 'Dienste', title: s.name, sub: 'Port ' + s.port, href: '#/dienste', ico: 'layers', k: [s.name, s.port, s.purpose, s.config].join(' ') }));
  S.kb.findings.forEach(f => L.push({ type: 'Befunde', title: f.title, sub: 'Priorität ' + f.sev, href: '#/befunde', ico: SEV_ICON[f.sev], k: f.title + ' ' + f.detail }));
  (S.sec?.findings || []).forEach(f => L.push({ type: 'Sicherheit', title: f.title, sub: `${SEC_SEV[f.sev]?.label || f.sev} · ${f.cat}`, href: '#/sicherheit', ico: 'shield', k: [f.title, f.target, f.cat, ...(f.evidence || [])].join(' ') }));
  S.kb.runbooks.forEach(r => L.push({ type: 'Runbooks', title: r.title, href: '#/runbooks', ico: 'terminal', k: r.title + ' ' + r.steps.join(' ') }));
  QUICK.forEach(([n, u, s, ic]) => L.push({ type: 'Weboberflächen', title: n, sub: u.replace('http://', ''), href: u, ico: ic, k: `${n} ${s} ${u}` }));
  return L;
}
const footer = () => `<footer class="foot"><span>ORBIT MONITOR v4.4</span><span>Wissensbasis: ${S.kb.source === 'atlas' ? 'Doku :8094' : 'lokal'} · ${dDE(String(S.kb.stand).slice(0, 16).replace('T', ' '))}</span><span>Live: Prometheus, Home Assistant, Pi-hole, Uptime Kuma · alle 20 s</span><a href="${DOC_URL}">Dokumentation :8094</a></footer>`;
function setLive() {
  const p = S.ok.prom !== false, h = S.ok.ha !== false, d = S.ok.ph !== false, all = p && h && d;
  setHTML('live', `<i class="sd ${all ? 'ok pulse' : p ? 'warn' : 'bad'}"></i><span class="lt">${all ? 'Live' : p ? 'Teilweise live' : 'Keine Live-Daten'} · ${hhmm(Date.now())}</span>`);
  const lv = document.getElementById('live'); if (lv) lv.title = `Prometheus: ${p ? 'ok' : 'Fehler'} · Home Assistant: ${h ? 'ok' : 'Fehler'} · Pi-hole: ${d ? 'ok' : 'Fehler'}`;
  setHTML('sidefoot', [['Prometheus', p], ['Home Assistant', h], ['Pi-hole', d]].map(([n, ok]) => `<div class="sf-row"><i class="sd ${ok ? 'ok' : 'bad'}"></i>${n}</div>`).join('') + '<div class="sf-row faint">Raspberry Pi · Port 8092</div>');
}
let tick = 0;
async function refresh() {
  await Promise.all([loadHosts(), loadHA(), loadPH(), APP.key === '' ? loadKuma() : null]);
  setLive();
  if (APP.cur && APP.cur.live) { try { await APP.cur.live(); } catch (e) { console.error(e); } }
  if (++tick % 3 === 0 && CH.reg.size) loadCharts();
  if (tick % 3 === 0 && APP.key === '') { await loadSec(true); setHTML('sec-ov', secOverview()); }
}
async function boot() {
  try { S.kb = await (await fetch('data/kb.json', { cache: 'no-cache' })).json(); }
  catch (e) { $('#main').innerHTML = note('Die Wissensbasis (data/kb.json) konnte nicht geladen werden.', 'bad'); return; }
  initShell({ siteName: 'ORBIT Monitor', nav: NAV, pages: PAGES, index: searchIndex, footer });
  await Promise.race([Promise.all([loadInv(), loadHosts(), loadHA(), loadPH(), loadSec(), loadKuma()]), new Promise(r => setTimeout(r, 2500))]);
  adoptInventory();
  setLive();
  route();
  if (S.kb.source !== 'atlas') loadInv().then(() => { if (adoptInventory()) route(); });
  setInterval(refresh, 20000);
}
boot();
