/* ORBIT MONITOR – Sicherheitszentrale & Netzwerkprüfung.
   Datenquelle: Prüfdienst orbit-security auf dem Pi (/opt/orbit-security/scan.py)
     api/security          → Bericht (Befunde mit Handlungsschritten, Geräte, Virenscan, Audit, Verlauf)
     api/security/status   → Live-Fortschritt einer laufenden Prüfung
     api/security/run/<m>  → POST: Prüfung anfordern (quick | full | malware), Sperrzeit regelt der Pi
     api/security/accepted → bewusst akzeptierte Befunde; POST api/security/accept | unaccept setzt bzw. beendet eine Ausnahme
   Wird vor monitor.js geladen; monitor.js übernimmt SEC_PAGES und SEC_NAV. */
'use strict';
Object.assign(S, { sec: null, secSt: null, secPoll: null });

const SEC_SEV = { hoch: { tone: 'bad', label: 'Hoch' }, mittel: { tone: 'warn', label: 'Mittel' }, niedrig: { tone: '', label: 'Niedrig' }, info: { tone: '', label: 'Info' } };
const SEC_MODE = { quick: ['Schnellprüfung', 'Pi, Dienste, DNS-Wächter, Updates, Geräteerkennung, PC-Virenschutz, stündlich der Router · ca. 15 s'], full: ['Vollprüfung', 'zusätzlich alle Geräte: 1.000 Ports, Dienste, TLS, SSH, SMB · 10–20 min'], malware: ['Virenscan & Audit', 'ClamAV über Eingangs- und Webordner + Lynis-Härtungsaudit · ca. 15–30 min'] };
const SEC_SRC = { quick: 'Schnellprüfung', full: 'Vollprüfung', malware: 'Virenscan', defender: 'PC-Meldung', router: 'Router-Prüfung' };
const SEC_CAT_ICON = { Zugang: 'key', Netz: 'network', Updates: 'download', Angriffe: 'alertCircle', Container: 'package', DNS: 'shield', Sicherung: 'archive', Virenschutz: 'bug', 'Härtung': 'wrench', Geräte: 'devices', 'Verschlüsselung': 'lock', Router: 'router' };
const SEC_EV = { neu: ['Neuer Befund', 'alert', 'befund'], behoben: ['Behoben', 'checkCircle', 'befund'], 'port-auf': ['Port geöffnet', 'network', 'port'], 'port-zu': ['Port geschlossen', 'xCircle', 'port'], 'geraet-neu': ['Neues Gerät', 'devices', 'geraet'], akzeptiert: ['Akzeptiert', 'check', 'ausnahme'], aufgenommen: ['Wieder aufgenommen', 'history', 'ausnahme'] };
const SEC_DAYS = [[90, '90 Tage'], [365, '1 Jahr'], [30, '30 Tage'], [0, 'unbefristet']];

async function loadSec(force) {
  if (force) clearCache();
  try { S.sec = await getJSON('api/security', 8000); S.ok.sec = true; } catch (e) { S.sec = S.sec || null; S.ok.sec = false; }
  if (!S.sec) return;
  let acc = null;
  try { const r = await fetch('api/security/accepted', { cache: 'no-store' }); acc = r.ok ? await r.json() : r.status === 404 ? {} : null; } catch (e) { /* Pi nicht erreichbar: Stand des Berichts gilt */ }
  secApplyAcc(S.sec, acc);
}
/* Ausnahmen sofort berücksichtigen – der Prüfdienst rechnet sie erst beim nächsten Lauf ein. Gleiche Formel wie scan.py. */
function secApplyAcc(r, acc) {
  const all = [...(r.findings || []), ...(r.accepted || [])], ord = { hoch: 0, mittel: 1, niedrig: 2, info: 3 };
  if (acc) all.forEach(f => { const a = acc[f.id]; f.accepted = a && !(a.until && new Date(a.until) < Date.now()) ? a : undefined; });
  r.findings = all.filter(f => !f.accepted).sort((a, b) => ord[a.sev] - ord[b.sev] || a.cat.localeCompare(b.cat) || a.title.localeCompare(b.title));
  r.accepted = all.filter(f => f.accepted);
  const w = r.weights || { w: { hoch: 12, mittel: 4, niedrig: 1, info: 0 }, cap: { hoch: 60, mittel: 32, niedrig: 15, info: 0 } };
  r.counts = Object.fromEntries(['hoch', 'mittel', 'niedrig', 'info'].map(k => [k, r.findings.filter(f => f.sev === k).length]));
  r.score = Math.max(0, Math.round(100 - Object.keys(r.counts).reduce((s, k) => s + Math.min(w.w[k] * r.counts[k], w.cap[k]), 0)));
  r.grade = r.score >= 90 ? 'A' : r.score >= 75 ? 'B' : r.score >= 60 ? 'C' : r.score >= 40 ? 'D' : 'E';
}
async function secExc(kind, body, box) {
  const msg = box.querySelector('[data-accmsg]');
  box.querySelectorAll('button').forEach(b => { b.disabled = true; });
  try {
    const r = await fetch('api/security/' + kind, { method: 'POST', headers: { 'X-Orbit': '1', 'Content-Type': 'application/json' }, body: JSON.stringify(body), cache: 'no-store' });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(r.status === 404 ? 'Der Befund ist im aktuellen Bericht nicht mehr enthalten.' : `Der Pi hat abgelehnt (HTTP ${r.status}).`);
    secApplyAcc(S.sec, j.accepted || {});
    const y = window.scrollY; route(); window.scrollTo(0, y);
  } catch (e) {
    if (msg) msg.textContent = e.message.startsWith('Der') ? e.message : 'Pi nicht erreichbar – nichts gespeichert.';
    box.querySelectorAll('button').forEach(b => { b.disabled = false; });
  }
}
document.addEventListener('click', e => {
  const o = e.target.closest('[data-accopen]');
  if (o) { const f = o.closest('.acc-b').querySelector('.sec-accf'); f.hidden = false; o.hidden = true; f.querySelector('textarea').focus(); return; }
  const c = e.target.closest('[data-acccancel]');
  if (c) { const f = c.closest('.sec-accf'); f.hidden = true; f.closest('.acc-b').querySelector('[data-accopen]').hidden = false; return; }
  const s = e.target.closest('[data-accsave]');
  if (s) {
    const f = s.closest('.sec-accf'), reason = f.querySelector('textarea').value.trim();
    if (reason.length < 3) { f.querySelector('[data-accmsg]').textContent = 'Bitte kurz begründen, warum das in Ordnung ist.'; f.querySelector('textarea').focus(); return; }
    secExc('accept', { id: s.dataset.accsave, reason, days: +f.querySelector('select').value }, f);
    return;
  }
  const u = e.target.closest('[data-unaccept]');
  if (u) secExc('unaccept', { id: u.dataset.unaccept }, u.closest('.sec-acc'));
});
const secEvFor = (slug, ip) => (S.sec?.events || []).filter(e => (slug && e.target === slug) || (ip && e.target === ip));
function secEvRows(evs) {
  return `<ul class="rows sec-ev">${evs.map(e => {
    const k = SEC_EV[e.kind] || [e.kind, 'info', ''], sv = SEC_SEV[e.sev];
    return `<li data-g="${k[2]}"><span class="ev-i ${esc(e.kind)}">${icon(k[1])}</span><span class="grow"><span class="ev-t">${esc(e.title)}</span>
      <span class="small muted">${esc(k[0])}${sv && e.kind === 'neu' ? ` · ${sv.label}` : ''}${e.target ? ` · ${secTarget(e)}` : ''}<time class="ev-tm" datetime="${esc(e.t)}"> · ${dDE(e.t.slice(0, 16).replace('T', ' '))}</time></span></span><time class="ev-tr small muted" datetime="${esc(e.t)}">${dDE(e.t.slice(0, 16).replace('T', ' '))}</time></li>`;
  }).join('')}</ul>`;
}
async function loadSecStatus() {
  try { const r = await fetch('api/security/status', { cache: 'no-store' }); S.secSt = r.ok ? await r.json() : null; } catch (e) { S.secSt = null; }
  // Eine Prüfung, die seit über 7 Stunden „läuft“, ist hängen geblieben (z. B. Pi neu gestartet)
  if (S.secSt?.running && Date.now() - new Date(S.secSt.started) > 7 * 3600e3) S.secSt.running = false;
  return S.secSt;
}
const secTarget = f => {
  const d = S.kb.devices.find(x => devId(x) === f.target);
  if (d) return `<a href="#/geraet/${esc(devId(d))}">${esc(d.name)}</a>`;
  if (/^192\.168\./.test(f.target || '')) return `<a href="#/netzpruefung?q=${esc(f.target)}">${esc(f.target)}</a>`;
  return esc(f.target || '');
};
const secFor = (slug, ip) => (S.sec?.findings || []).filter(f => f.target === slug || (ip && (f.target === ip || (f.evidence || []).some(e => e.includes(ip + ':') || e.includes(`(${ip})`)))));
const secIsNew = f => f.first_seen && Date.now() - new Date(f.first_seen) < 24 * 3600e3;
const secGradeTone = g => ({ A: 'ok', B: 'ok', C: 'warn', D: 'bad', E: 'bad' })[g] || '';
const secGradeText = g => ({ A: 'Sehr gut abgesichert', B: 'Gut abgesichert', C: 'Ausbaufähig – einige Lücken', D: 'Deutliche Lücken', E: 'Kritisch' })[g] || 'Noch nicht bewertet';
const secAt = iso => iso ? `${dDE(iso.slice(0, 16).replace('T', ' '))} (${ago(iso)})` : 'noch nie';

/* Knöpfe & Live-Fortschritt */
/* Bewertungsring: SVG-Bogen mit Zahl – Übersicht (sm) und Sicherheitszentrale */
function scoreRing(score, grade, { sm = false } = {}) {
  const v = num(score), s = Math.max(0, Math.min(100, v ?? 0)), r = 44, C = 2 * Math.PI * r;
  return `<svg class="sring ${sm ? 'sm ' : ''}${secGradeTone(grade)}" viewBox="0 0 110 110" role="img" aria-label="Sicherheitsbewertung ${v === null ? 'unbekannt' : Math.round(s) + ' von 100'}, Note ${esc(grade || '–')}">
    <circle class="tr" cx="55" cy="55" r="${r}"/>
    <circle class="ar" cx="55" cy="55" r="${r}" transform="rotate(-90 55 55)" stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${(C * (1 - s / 100)).toFixed(1)}"/>
    <text class="n" x="55" y="62" text-anchor="middle">${v === null ? '–' : Math.round(s)}</text>
    <text class="u" x="55" y="79" text-anchor="middle">VON 100</text></svg>`;
}
const secRunBtns = (modes = ['quick', 'full', 'malware']) => modes.map(m => `<button type="button" class="btn ${m === 'full' ? 'primary' : ''}" data-secrun="${m}" title="${esc(SEC_MODE[m][1])}">${icon(m === 'malware' ? 'bug' : m === 'full' ? 'radar' : 'play')}<span class="lbl">${SEC_MODE[m][0]}</span></button>`).join('');
function secProgressHTML(st) {
  if (!st) return '';
  const m = SEC_MODE[st.mode]?.[0] || st.mode, el = st.started ? Math.max(0, Math.round((Date.now() - new Date(st.started)) / 1000)) : 0; // PC- und Pi-Uhr können leicht abweichen
  if (st.running) return card(`${m} läuft`, `<div style="display:flex;justify-content:space-between;gap:12px;font-size:14px;margin-bottom:8px"><b>${esc(st.phase)}</b><span class="muted">${st.pct} % · seit ${dur(el)}</span></div>${meterHTML(st.pct)}
      <ul class="sec-log">${(st.log || []).slice(-6).reverse().map(l => `<li><code>${esc(l)}</code></li>`).join('')}</ul>`, { ico: 'activity', cls: 'sec-run', aside: pill('läuft', 'ok', 'activity') });
  const bad = /^(Abgebrochen|Sperrzeit)/.test(st.phase || '');
  return note(`<b>Letzte Prüfung (${esc(m)}):</b> ${esc(st.phase)}${st.finished ? ` · ${hhmm(st.finished)} Uhr` : ''}`, bad ? 'warn' : '', bad ? 'alert' : 'checkCircle');
}
async function secRun(mode, btnEl) {
  if (btnEl) btnEl.disabled = true;
  try {
    const r = await fetch('api/security/run/' + mode, { method: 'POST', headers: { 'X-Orbit': '1' }, cache: 'no-store' });
    if (r.status === 409) secToast('Es läuft bereits eine Prüfung – der Fortschritt wird angezeigt.');
    else if (!r.ok) secToast('Der Pi hat den Auftrag abgelehnt (HTTP ' + r.status + ').', true);
  } catch (e) { secToast('Pi nicht erreichbar – Auftrag nicht gesendet.', true); }
  setTimeout(secWatch, 1200);
}
function secToast(text, bad) { setHTML('sec-prog', note(esc(text), bad ? 'bad' : 'warn')); }
/* Fortschritt alle 2 s, solange eine Prüfung läuft; danach Bericht neu laden und Seite neu aufbauen */
async function secWatch() {
  clearTimeout(S.secPoll);
  const page = APP.key;
  if (!['sicherheit', 'netzpruefung'].includes(page)) return;
  const st = await loadSecStatus();
  setHTML('sec-prog', secProgressHTML(st));
  document.querySelectorAll('[data-secrun]').forEach(b => { b.disabled = !!st?.running; });
  if (st?.running) { S.secWatching = true; S.secPoll = setTimeout(secWatch, 2000); return; }
  if (S.secWatching) { S.secWatching = false; await loadSec(true); if (APP.key === page) route(); }
}
document.addEventListener('click', e => { const b = e.target.closest('[data-secrun]'); if (b) secRun(b.dataset.secrun, b); });

/* Maßnahmenplan: ein aufklappbarer Eintrag je Befund – Warum, Was tun (kopierbare Befehle), Beleg */
function secAction(f) {
  const sv = SEC_SEV[f.sev] || SEC_SEV.info, a = f.accepted;
  return `<details class="card acc sec-act sev-${esc(f.sev)}" data-sev="${esc(f.sev)}" data-cat="${esc(f.cat)}" data-k="${esc((f.title + ' ' + f.target + ' ' + f.cat + ' ' + (f.evidence || []).join(' ')).toLowerCase())}">
    <summary><span class="ico">${icon(SEC_CAT_ICON[f.cat] || 'alert')}</span><span class="sa-t"><span class="sa-h">${esc(f.title)}</span>
      <span class="sa-m">${pill(sv.label, sv.tone, f.sev === 'hoch' ? 'alertCircle' : f.sev === 'mittel' ? 'alert' : 'info')}${tag(f.cat)}${secIsNew(f) ? tag('neu', 'accent') : ''}${f.effort ? `<span class="muted small">${icon('clock')} ${esc(f.effort)}</span>` : ''}</span></span>${icon('chevron', 'chev')}</summary>
    <div class="acc-b">
      ${a ? `<div class="sec-acc">${note(`<b>Bewusst akzeptiert</b> am ${dDE(a.at.slice(0, 10))} · ${a.until ? `gilt bis ${dDE(a.until.slice(0, 10))}` : 'unbefristet'}<br>„${esc(a.reason)}“`, '', 'checkCircle')}
        <div class="sec-accf-r"><button type="button" class="btn sm" data-unaccept="${esc(f.id)}">${icon('history')}<span>Wieder in den Maßnahmenplan</span></button><span class="small muted" data-accmsg></span></div></div>` : ''}
      <p class="sa-why">${inline(f.why)}</p>
      <h4 class="sa-sub">Was tun</h4>${steps(f.fix || [])}
      ${(f.evidence || []).length ? `<h4 class="sa-sub">Beleg</h4><ul class="sa-ev">${f.evidence.map(e => `<li><code>${esc(e)}</code></li>`).join('')}</ul>` : ''}
      <div class="sa-foot">${f.target ? `<span>${icon('pin')} ${secTarget(f)}</span>` : ''}<span>${icon('clock')} erkannt ${secAt(f.first_seen)}</span><span>${icon('search')} ${esc(SEC_SRC[f.source] || f.source)}</span>
        <span class="muted">Verschwindet automatisch, sobald die nächste Prüfung den Punkt nicht mehr findet.</span></div>
      ${a ? '' : `<button type="button" class="btn sm ghost sec-accb" data-accopen>${icon('check')}<span>Bewusst akzeptieren …</span></button>
      <div class="sec-accf" hidden><label class="sec-accl"><span class="small">Warum ist das in Ordnung? <span class="muted">(steht danach beim Befund)</span></span>
        <textarea class="field" rows="2" maxlength="300" placeholder="z. B. „Fernzugang wird für unterwegs gebraucht, Kennwort 20 Zeichen, nur hier genutzt“"></textarea></label>
        <div class="sec-accf-r"><label class="small">Gilt <select class="field" aria-label="Dauer der Ausnahme">${SEC_DAYS.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select></label>
          <button type="button" class="btn sm primary" data-accsave="${esc(f.id)}">${icon('check')}<span>Akzeptieren</span></button><button type="button" class="btn sm ghost" data-acccancel>Abbrechen</button><span class="small muted" data-accmsg></span></div>
        <p class="small muted" style="margin:0">Akzeptierte Befunde zählen nicht mehr in die Bewertung und stehen unter „Bewusst akzeptiert“. Nach Ablauf kommen sie automatisch zurück in den Maßnahmenplan.</p></div>`}
    </div></details>`;
}
const SECUI = { sev: 'alle', q: '' };
function secPlanMount() {
  const apply = () => {
    let n = 0;
    document.querySelectorAll('#sec-plan .sec-act').forEach(el => { const on = (SECUI.sev === 'alle' || el.dataset.sev === SECUI.sev) && (!SECUI.q || el.dataset.k.includes(SECUI.q)); el.hidden = !on; if (on) n++; });
    setText('sec-n', `${n} von ${document.querySelectorAll('#sec-plan .sec-act').length}`);
    const em = document.getElementById('sec-empty'); if (em) em.hidden = n > 0;
  };
  wireSel('sec-sev', v => { SECUI.sev = v; apply(); });
  document.getElementById('sec-q')?.addEventListener('input', e => { SECUI.q = e.target.value.trim().toLowerCase(); apply(); });
  apply();
}

function secCheckTable(checks) {
  const tone = { ok: ['ok', 'ok', 'checkCircle'], befund: ['Befund', 'warn', 'alert'], 'übersprungen': ['übersprungen', '', 'info'] };
  const areas = [...new Set(checks.map(c => c.area))];
  return `<div class="table-wrap"><table class="t"><thead><tr><th>Ergebnis</th><th>Prüfung</th><th>Detail</th></tr></thead><tbody>${areas.map(a => `<tr class="grp"><td colspan="3">${esc(a)}</td></tr>` + checks.filter(c => c.area === a).map(c => { const t = tone[c.result] || tone['übersprungen']; return `<tr><td>${pill(t[0], t[1], t[2])}</td><td class="nm">${esc(c.name)}</td><td class="small muted">${esc(c.detail)}</td></tr>`; }).join('')).join('')}</tbody></table></div>`;
}

function secVirusCards(r) {
  const c = r.clamav, d = r.defender, l = r.lynis;
  const clam = c ? kv([['Letzter Scan', secAt(c.at)], ['Geprüfte Dateien', fmt(c.files, 0)], ['Funde', c.infected.length ? `<b>${c.infected.length}</b>` : '0 – sauber'], ['Dauer', dur(c.seconds)], ['Signaturen', c.sigAgeDays === null ? '–' : `${fmt(c.sigAgeDays, 1)} Tage alt`], ['Bereiche', c.paths.map(p => `<code>${esc(p)}</code>`).join(' ')], ['Ausgenommen', ((r.scope || {}).clam_excluded || ['Sicherungsarchive auf der NAS']).map(esc).join(' · ')]])
    : emptyMsg('Noch kein Virenscan gelaufen. Er läuft samstags um 01:00 oder über „Virenscan & Audit“.');
  const yes = (v, t = 'an') => v === true ? pill(t, 'ok', 'checkCircle') : v === false ? pill('aus', 'bad', 'xCircle') : '–';
  const def = d ? kv([['Gemeldet', secAt(d.time)], ['Echtzeitschutz', yes(d.rtp)], ['Verhaltensüberwachung', yes(d.behavior)], ['Manipulationsschutz', yes(d.tamper)], ['Signaturen', `${d.sigAgeDays} Tage alt · ${esc(d.sigVersion || '')}`], ['Schnellprüfung', d.quickScanEnd ? secAt(d.quickScanEnd) : 'nie'], ['Vollprüfung', d.fullScanEnd ? `${secAt(d.fullScanEnd)}` : 'nie'], ['Firewall', Object.entries(d.firewall || {}).map(([k, v]) => `${esc(k)} ${v ? '✓' : '✗'}`).join(' · ')], ['Funde', (d.threats || []).length ? `${d.threats.length}` : 'keine'], ['System', esc(d.os || '')]])
    : emptyMsg('Vom Windows-PC liegt noch keine Meldung vor (geplante Aufgabe „ORBIT Defender-Meldung“, stündlich).');
  const idx = l?.index ?? null;
  const lyn = l ? `<div class="sec-index"><span class="v">${idx ?? '–'}</span><span class="muted">von 100 · ${fmt(l.tests, 0)} Tests · ${secAt(l.at)}</span></div>${meterHTML(idx ?? 0, toneFor(100 - (idx ?? 0), 35, 45))}
      <p class="small muted" style="margin:10px 0 0">Der Härtungsindex von Lynis bewertet die Grundkonfiguration des Pi. Über 65 gilt als solide, über 80 als gut gehärtet.</p>
      ${(l.warnings || []).length ? `<h4 class="sa-sub">Warnungen</h4><ul class="rows">${l.warnings.map(w => `<li><code>${esc(w.id)}</code><span class="grow">${esc(w.text)}</span></li>`).join('')}</ul>` : ''}
      ${(l.suggestions || []).length ? `<details class="sec-more"><summary>${l.suggestions.length} Härtungsvorschläge anzeigen</summary><ul class="rows">${l.suggestions.map(s => `<li><code>${esc(s.id)}</code><span class="grow">${esc(s.text)}${s.detail && s.detail !== '-' ? ` <span class="muted">(${esc(s.detail)})</span>` : ''}</span></li>`).join('')}</ul><p class="small muted">Details zu einem Punkt: <code>sudo lynis show details &lt;ID&gt;</code></p></details>` : ''}`
    : emptyMsg('Noch kein Audit gelaufen (zusammen mit dem Virenscan).');
  return `<div class="grid cols-3">${card('Virenscan Pi & NAS (ClamAV)', clam, { ico: 'bug', aside: c ? (c.infected.length ? pill(`${c.infected.length} Funde`, 'bad', 'alertCircle') : pill('sauber', 'ok', 'checkCircle')) : '' })}
    ${card('Windows-PC (Microsoft Defender)', def, { ico: 'monitor', aside: d ? (d.rtp && d.av ? pill('geschützt', 'ok', 'checkCircle') : pill('Schutz aus', 'bad', 'xCircle')) : '' })}
    ${card('Härtung des Pi (Lynis)', lyn, { ico: 'wrench' })}</div>`;
}

/* Router: was vom Internet aus erreichbar ist, WLAN, Ereignisprotokoll (TR-064, stündlich) */
function secRouterCards(r) {
  const x = r.router;
  if (!x || !x.model) return card('Router (FRITZ!Box)', emptyMsg('Noch keine Router-Prüfung – sie läuft stündlich mit der Schnellprüfung und bei jeder Vollprüfung.'), { ico: 'router' });
  const yn = (on, badIfOn = true) => on ? pill('an', badIfOn ? 'warn' : 'ok', badIfOn ? 'alert' : 'checkCircle') : pill('aus', badIfOn ? 'ok' : '', badIfOn ? 'checkCircle' : 'info');
  const extN = (x.remote?.enabled ? 1 : 0) + (x.myfritz?.services || []).length + (x.portmaps || []).filter(p => p.enabled).length + (x.ftp_wan ? 1 : 0) + (x.filelinks || 0);
  const ext = kv([
    ['Fernzugang', `${yn(x.remote?.enabled)}${x.remote?.enabled ? ` <span class="muted small">HTTPS, Port ${esc(x.remote.port)}</span>` : ''}`],
    ['MyFRITZ', x.myfritz?.enabled ? `angemeldet · ${(x.myfritz.services || []).length} Freigaben` : 'aus'],
    ['Portfreigaben', (x.portmaps || []).length ? x.portmaps.map(p => `<code>${esc(p.proto)} ${esc(p.ext)} → ${esc(p.client)}:${esc(p.int)}</code>`).join(' ') : 'keine'],
    ['FTP von außen', yn(x.ftp_wan)],
    ['Freigabelinks', x.filelinks ? `${x.filelinks}` : 'keine'],
    ['Fehlversuche', x.login_fails ? `${x.login_fails} <span class="muted small">(davon ${x.login_fails_ext} aus dem Internet)</span>` : 'keine Fehlanmeldungen im Protokoll'],
  ]);
  const wl = kv([
    ...(x.wlan || []).map(w => [`WLAN ${w.band}`, w.enabled ? `${esc(w.sec)}${['11i', 'WPAand11i'].includes(w.beacon) && /WPA3/.test(w.possible) ? ' <span class="muted small">· WPA3 möglich</span>' : ''}` : 'aus']),
    ['WPS', !x.wps || x.wps === 'stop' ? 'aus' : esc(x.wps)], ['UPnP', x.upnp ? 'Statusinformationen an, 0 automatische Freigaben' : 'aus'],
    ['FRITZ!NAS', `FTP ${x.ftp_lan ? 'an' : 'aus'} · SMB ${x.smb ? 'an' : 'aus'}`],
    ['Abbrüche', `${x.inet_fails}× Internet weg in den letzten ${x.log_lines} Protokolleinträgen`], ['Geprüft', secAt(x.at)],
  ]);
  return `<div class="grid cols-2">${card('Vom Internet aus erreichbar', ext, { ico: 'globe', aside: extN ? pill(`${extN} offen`, 'warn', 'alert') : pill('nichts offen', 'ok', 'checkCircle') })}
    ${card(`${esc(x.model)} · FRITZ!OS ${esc(x.os)}`, wl, { ico: 'router' })}</div>
    <p class="small muted" style="margin:10px 0 0">Nur lesende Abfragen über TR-064 mit dem Zugang des fritz_exporter. IPv6-Freigaben sind darüber nicht lesbar – einmal von Hand prüfen: FRITZ!Box → Internet → Freigaben.</p>`;
}

/* DNS-Wächter: Pi-hole-Anfragen der letzten 24 h je Gerät – nur Zahlen und Bedrohungs-Domains, kein Surfverlauf */
function secDns(r) {
  const d = r.dns;
  if (!d || !d.clients) return card('DNS-Wächter', emptyMsg('Noch keine Auswertung – sie läuft mit jeder Schnellprüfung.'), { ico: 'shield' });
  const pct = d.total ? 100 * d.blocked / d.total : 0, hits = d.clients.filter(c => c.threat);
  const tiles = [['Anfragen (24 h)', fmt(d.total, 0), 'activity', `${d.clients.length} Geräte`], ['Gesperrt', `${fmt(pct, 1)} %`, 'shield', `${fmt(d.blocked, 0)} Anfragen`],
    ['Bedrohungs-Treffer', fmt(d.threat, 0), 'alertCircle', `${d.trackers_on_threat_lists} Tracker auf Malware-Listen herausgefiltert`], ['fritz.box', d.fritzbox_ok ? 'Router' : 'Internet', 'router', d.fritzbox_ok ? 'zeigt auf 10.0.0.1' : `zeigt auf ${(d.fritzbox_ips || []).join(', ') || '–'}`]];
  const hr = (d.hourly || []).map(h => h[0] * 1000);
  return `<div class="grid cols-4">${tiles.map(([l, v, ic, sub], i) => `<div class="card tile"><div class="tile-top">${icon(ic)}<span class="tile-l">${esc(l)}</span>${i === 3 ? `<span class="tile-flag">${d.fritzbox_ok ? pill('ok', 'ok', 'checkCircle') : pill('falsch', 'warn', 'alert')}</span>` : i === 2 && d.threat ? `<span class="tile-flag">${pill('prüfen', 'warn', 'alert')}</span>` : ''}</div><div class="tile-row"><div class="tile-val"><span class="v">${v}</span></div></div><div class="tile-sub">${esc(sub)}</div></div>`).join('')}</div>
    <div class="grid cols-2" style="margin-top:16px">${hr.length > 1 ? chartCard('dnsh', { title: 'DNS-Anfragen je Stunde', ico: 'activity', unit: '', digits: 0, yMin: 0, foot: 'Letzte 24 Stunden aus der Pi-hole-Datenbank; die laufende Stunde ist noch nicht vollständig.',
      static: [{ name: 'Anfragen', slot: 1, pts: d.hourly.map(h => [h[0] * 1000, h[1]]) }, { name: 'davon gesperrt', slot: 2, pts: d.hourly.map(h => [h[0] * 1000, h[2]]) }] }) : ''}
      ${card('Bedrohungs-Domains', (hits.length ? `<ul class="rows">${hits.map(c => `<li>${icon('alertCircle')}<span class="grow"><b>${c.slug ? secTarget({ target: c.slug }) : esc(c.name)}</b> <span class="mono small">${esc(c.ip)}</span><br>${c.threat_domains.map(t => `<code>${esc(t.d)}</code> <span class="muted small">${t.n}×</span>`).join(' · ')}</span></li>`).join('')}</ul>`
        : `<p class="muted" style="margin:0">${icon('checkCircle')} Kein Gerät hat in den letzten 24 Stunden eine Domain angefragt, die nur auf einer Bedrohungsliste steht.</p>`) + `<p class="small muted" style="margin:12px 0 0">Bedrohungslisten: ${(d.lists?.threat || []).map(l => `<code>${esc(l)}</code>`).join(' ') || 'keine'}. Domains, die zugleich auf einer Werbe-/Tracking-Liste stehen, zählen als Tracker und nicht als Bedrohung – so gibt es keine Fehlalarme durch Werbenetzwerke.</p>`, { ico: 'bug' })}</div>
    <div style="margin-top:16px">${card('Anfragen je Gerät (24 h)', `<div class="table-wrap"><table class="t"><thead><tr><th>Gerät</th><th class="num">Anfragen</th><th class="num">gesperrt</th><th class="num">ohne Ergebnis</th><th class="num">Trend</th><th>Bedrohung</th></tr></thead><tbody>${d.clients.slice(0, 25).map(c => {
      const tr = c.base ? c.q / c.base : null;
      return `<tr><td><div class="nm">${c.slug ? secTarget({ target: c.slug }) : esc(c.name)}</div><div class="ds mono">${esc(c.ip)}</div></td><td class="num">${fmt(c.q, 0)}</td><td class="num">${fmt(c.blocked, 0)}</td><td class="num">${c.q ? fmt(100 * c.nx / c.q, 0) + ' %' : '–'}</td><td class="num">${tr === null ? '<span class="muted">neu</span>' : `${fmt(tr, 1)}×`}</td><td>${c.threat ? pill(`${c.threat} Treffer`, 'warn', 'alert') : '<span class="muted small">–</span>'}</td></tr>`;
    }).join('')}</tbody></table></div><p class="small muted" style="margin:10px 0 0">Trend = heute im Vergleich zum Tagesschnitt der Vorwoche. „Ohne Ergebnis“ = Anfragen nach Namen, die es nicht gibt (NXDOMAIN). Auswertung in ${fmt(d.seconds, 1)} s, ${secAt(d.at)}.</p>`, { ico: 'devices' })}</div>`;
}

/* Zeitleiste: neue und behobene Befunde, Ports, neue Geräte, Ausnahmen */
const SECEV = { f: 'alle', n: 30 };
function secTimeline(r) {
  const ev = r.events || [];
  if (!ev.length) return card('', emptyMsg('Noch keine Ereignisse – sie entstehen mit den nächsten Prüfungen.'));
  return `<div class="filters">${segCtl('ev-f', [['alle', 'Alle'], ['befund', 'Befunde'], ['port', 'Ports'], ['geraet', 'Geräte'], ['ausnahme', 'Ausnahmen']], SECEV.f)}<span class="f-right muted small" id="ev-n"></span></div>
    ${card('', secEvRows(ev) + `<div id="ev-empty" hidden>${emptyMsg('Keine Ereignisse dieser Art in den letzten 90 Tagen.')}</div><button type="button" class="btn sm ghost" id="ev-more" style="margin-top:10px">${icon('chevron')}<span>Weitere anzeigen</span></button>`)}`;
}
function secTimelineMount() {
  const apply = () => {
    const li = [...document.querySelectorAll('#tl .sec-ev li')], hit = li.filter(el => SECEV.f === 'alle' || el.dataset.g === SECEV.f);
    li.forEach(el => { el.hidden = true; });
    hit.slice(0, SECEV.n).forEach(el => { el.hidden = false; });
    setText('ev-n', `${Math.min(SECEV.n, hit.length)} von ${hit.length}`);
    const m = document.getElementById('ev-more'); if (m) m.hidden = hit.length <= SECEV.n;
    const em = document.getElementById('ev-empty'); if (em) em.hidden = hit.length > 0;
  };
  wireSel('ev-f', v => { SECEV.f = v; SECEV.n = 30; apply(); });
  document.getElementById('ev-more')?.addEventListener('click', () => { SECEV.n += 50; apply(); });
  apply();
}

/* ------------------------------------------------------------------ Seite: Sicherheitszentrale */
const SEC_PAGES = {};
SEC_PAGES.sicherheit = {
  title: 'Sicherheitszentrale',
  render(_, params) {
    const r = S.sec;
    if (params?.sev && (params.sev === 'alle' || SEC_SEV[params.sev])) SECUI.sev = params.sev;
    const hd = head({ kicker: 'Automatische Prüfung · Pi, Netz, Geräte, PC', title: 'Sicherheitszentrale', ico: 'shield', text: 'Prüft das Heimnetz laufend auf Lücken: offene Dienste, Zugänge ohne Passwort, fehlende Updates, unbekannte Geräte, Schadsoftware. Zu jedem Befund steht, warum er wichtig ist und was genau zu tun ist.', actions: secRunBtns() });
    if (!r) return hd + `<div id="sec-prog"></div>` + note('Der Prüfbericht ist noch nicht verfügbar. Der Prüfdienst auf dem Pi (<code>orbit-security</code>) schreibt ihn nach der ersten Schnellprüfung.', 'warn');
    const c = r.counts, open = r.findings.filter(f => f.sev !== 'info');
    const tone = secGradeTone(r.grade);
    const hist = (r.history || []).map(h => [new Date(h.t).getTime(), h.score]);
    return hd + `<div id="sec-prog"></div>
      <section class="card sec-hero">
        <div class="sec-score">${scoreRing(r.score, r.grade)}
          <div><div class="kicker">Sicherheitsbewertung · Note ${esc(r.grade)}</div><h2>${esc(secGradeText(r.grade))}</h2>
          <p class="muted">${open.length ? `${open.length} offene Punkte. Wer oben im Maßnahmenplan beginnt, gewinnt am meisten.` : 'Keine offenen Punkte gefunden.'}${(r.accepted || []).length ? ` <a href="#akzeptiert">${r.accepted.length} bewusst akzeptiert</a>.` : ''}</p>
          <div class="sec-last">${['quick', 'full', 'malware'].map(m => `<span>${icon(m === 'malware' ? 'bug' : m === 'full' ? 'radar' : 'play')}<b>${SEC_MODE[m][0]}:</b> ${r.last[m] ? ago(r.last[m]) : 'noch nie'}</span>`).join('')}</div></div></div>
        <div><div class="sev-stack" aria-hidden="true">${['hoch', 'mittel', 'niedrig'].map(k => `<i class="${k}" style="flex:${c[k] || 0}"></i>`).join('')}</div>
        <div class="sec-counts">${['hoch', 'mittel', 'niedrig'].map(k => `<button type="button" class="sec-c ${SEC_SEV[k].tone}${c[k] ? '' : ' zero'}" data-jump="${k}"><span class="v">${c[k]}</span><span class="l">${icon(k === 'hoch' ? 'alertCircle' : k === 'mittel' ? 'alert' : 'info')} ${SEC_SEV[k].label}</span></button>`).join('')}
          <a class="sec-c" href="#behoben"><span class="v">${(r.resolved || []).length}</span><span class="l">${icon('checkCircle')} behoben (30 Tage)</span></a></div></div>
      </section>` +
      section('Maßnahmenplan', `<div class="filters">${segCtl('sec-sev', [['alle', 'Alle'], ['hoch', 'Hoch'], ['mittel', 'Mittel'], ['niedrig', 'Niedrig']], SECUI.sev)}${searchField('sec-q', 'Befund, Gerät, IP …', SECUI.q)}<span class="f-right muted small" id="sec-n"></span></div>
        <div class="stack-v" id="sec-plan">${open.map(secAction).join('')}</div><div id="sec-empty" hidden>${emptyMsg('Keine Befunde für diese Auswahl.')}</div>`, { sub: 'nach Dringlichkeit sortiert · aufklappen für Begründung und Schritte', id: 'plan' }) +
      ((r.accepted || []).length ? section('Bewusst akzeptiert', `<div class="stack-v">${r.accepted.map(secAction).join('')}</div>`, { sub: 'zählen nicht in die Bewertung · nach Ablauf automatisch wieder im Maßnahmenplan', id: 'akzeptiert' }) : '') +
      ((r.resolved || []).length ? section('Behoben', card('', `<ul class="rows">${r.resolved.map(x => `<li>${icon('checkCircle')}<span class="grow">${esc(x.title)} <span class="muted small">· ${esc(SEC_SEV[x.sev]?.label || x.sev)}</span></span><span class="small muted">${dDE(x.at.slice(0, 16).replace('T', ' '))}</span></li>`).join('')}</ul>`), { sub: 'von der Prüfung nicht mehr gefunden', id: 'behoben' }) : '') +
      section('Router (FRITZ!Box)', secRouterCards(r), { sub: 'Fernzugang, Freigaben, WLAN-Verschlüsselung, Ereignisprotokoll · stündlich', id: 'router' }) +
      section('DNS-Wächter', secDns(r), { sub: 'Pi-hole-Anfragen der letzten 24 Stunden je Gerät · nur Zahlen und Bedrohungs-Domains, kein Surfverlauf', id: 'dns' }) +
      section('Virenschutz & Härtung', secVirusCards(r)) +
      section('Verlauf', `<div class="grid cols-2">${hist.length > 1 ? chartCard('secscore', { title: 'Sicherheitsbewertung', ico: 'shield', unit: '/100', digits: 0, yMin: 0, static: [{ name: 'Bewertung', slot: 1, pts: hist }] }) : card('Sicherheitsbewertung', emptyMsg('Der Verlauf entsteht mit den nächsten Prüfungen (alle 15 Minuten).'), { ico: 'shield' })}
        ${card('Zeitplan', `<ul class="rows">${[['quick', 'alle 15 Minuten'], ['full', 'täglich 02:15'], ['malware', 'samstags 01:00']].map(([m, w]) => `<li>${icon(m === 'malware' ? 'bug' : m === 'full' ? 'radar' : 'play')}<span class="grow"><b>${SEC_MODE[m][0]}</b> · ${w}<br><span class="small muted">${esc(SEC_MODE[m][1])}</span></span><span class="small muted" style="text-align:right">zuletzt ${r.last[m] ? hhmm(r.last[m]) : '–'}${r.durations?.[m] ? `<br>${dur(r.durations[m])}` : ''}</span></li>`).join('')}</ul>
          <p class="small muted" style="margin:12px 0 0">Alle Prüfungen sind lesend: keine Passwort-Rateversuche, keine Angriffe, keine Änderungen an Geräten. Knopf-Aufträge haben eine Sperrzeit (Vollprüfung 10 min, Virenscan 6 h).</p>`, { ico: 'calendar' })}</div>`) +
      section('Zeitleiste', `<div id="tl">${secTimeline(r)}</div>`, { sub: 'neue und behobene Befunde, Ports, neue Geräte, Ausnahmen · 90 Tage', id: 'zeitleiste' }) +
      section('Was geprüft wurde', card('', secCheckTable(r.checks || []), { bodyCls: 'flush' }), { sub: `${(r.checks || []).length} Einzelprüfungen · Bericht vom ${dDE(r.generated.slice(0, 16).replace('T', ' '))}` });
  },
  mount() {
    secPlanMount();
    secTimelineMount();
    document.querySelectorAll('[data-jump]').forEach(b => b.addEventListener('click', () => {
      SECUI.sev = b.dataset.jump; document.querySelector(`#sec-sev [data-v="${SECUI.sev}"]`)?.click();
      document.getElementById('plan')?.scrollIntoView({ behavior: 'smooth' });
    }));
    secWatch();
  },
  async live() { if (S.secWatching) return; const g = S.sec?.generated; await loadSec(true); if (S.sec?.generated !== g && APP.key === 'sicherheit' && !document.querySelector('.sec-act[open]')) route(); },
};

/* ------------------------------------------------------------------ Seite: Netzwerkprüfung */
const NETUI = { f: 'alle', q: '' };
function secHostCard(h, r) {
  const fs = secFor(h.slug, h.ip), worst = fs.reduce((a, f) => Math.min(a, ['hoch', 'mittel', 'niedrig', 'info'].indexOf(f.sev)), 9);
  const tone = worst === 0 ? 'bad' : worst === 1 ? 'warn' : '';
  const d = h.slug ? S.kb.devices.find(x => devId(x) === h.slug) : null;
  const risky = new Set(fs.map(f => (f.id.match(/:(\d+)$/) || [])[1]).filter(Boolean));
  const fname = h.fritz_name || ((r.discovered || []).find(x => x.mac && x.mac === h.mac) || {}).fritz_name || '', ev = secEvFor(h.slug, h.ip);
  return `<details class="card acc net-h" data-f="${fs.length ? 'befund' : 'ok'}${h.known ? '' : ' unbekannt'}" data-k="${esc([h.name, h.ip, h.mac, h.vendor, fname, ...h.ports.map(p => p.port + ' ' + p.service + ' ' + p.product)].join(' ').toLowerCase())}">
    <summary><span class="ico">${icon(d ? devIcon(d) : h.known ? 'devices' : 'help')}</span><span class="sa-t"><span class="sa-h">${esc(h.name)}</span>
      <span class="sa-m"><span class="mono small">${esc(h.ip)}</span>${h.known ? '' : pill('unbekannt', 'warn', 'help')}<span class="muted small">${h.ports.length} offene Dienste</span>${fs.length ? pill(`${fs.length} Befund${fs.length > 1 ? 'e' : ''}`, tone, tone === 'bad' ? 'alertCircle' : 'alert') : pill('keine Befunde', 'ok', 'checkCircle')}</span></span>${icon('chevron', 'chev')}</summary>
    <div class="acc-b">
      ${kv([['MAC', `<code>${esc(h.mac || '–')}</code>${h.random_mac ? ' <span class="muted small">(zufällige private Adresse)</span>' : ''}`], ['Hersteller', esc(h.vendor || '–')], ['Name in der FRITZ!Box', esc(fname)], ['Inventar', d ? `<a href="#/geraet/${esc(devId(d))}">${esc(d.name)}</a>` : 'nicht im Inventar'], ['DNS (24 h)', secDnsLine(h.ip)]])}
      ${h.ports.length ? `<div class="table-wrap" style="margin-top:12px"><table class="t"><thead><tr><th>Port</th><th>Dienst</th><th>Erkannt</th></tr></thead><tbody>${h.ports.map(p => `<tr><td class="mono nowrap">${p.port}/${esc(p.proto)}${risky.has(String(p.port)) ? ` ${pill('Risiko', 'warn', 'alert')}` : ''}</td><td><div class="nm">${esc(p.service || '?')}${p.tunnel === 'ssl' ? ' · TLS' : ''}</div></td><td><div class="ds">${esc([p.product, p.version, p.extra ? `(${p.extra})` : ''].filter(Boolean).join(' ') || '–')}</div>${(p.notes || []).length ? `<div class="ds">${p.notes.map(esc).join(' · ')}</div>` : ''}</td></tr>`).join('')}</tbody></table></div>` : `<p class="muted small" style="margin:12px 0 0">${h.scanned ? 'Keine offenen TCP-Ports unter den 1.000 häufigsten – gut.' : 'In der letzten Vollprüfung nicht gescannt.'}</p>`}
      ${fs.length ? `<h4 class="sa-sub">Befunde</h4><div class="stack-v">${fs.map(secAction).join('')}</div>` : ''}
      ${ev.length ? `<h4 class="sa-sub">Verlauf</h4>${secEvRows(ev.slice(0, 8))}` : ''}
    </div></details>`;
}
SEC_PAGES.netzpruefung = {
  title: 'Netzwerkprüfung',
  render(_, params) {
    const r = S.sec;
    if (params?.q) NETUI.q = params.q;
    const hd = head({ kicker: 'Alle Geräte im 10.0.0.0/24', title: 'Netzwerkprüfung', ico: 'radar', text: 'Jedes erreichbare Gerät mit seinen offenen Diensten: welche Ports lauschen, welche Software antwortet, wie gut TLS und SSH eingestellt sind und ob sich jemand ohne Passwort anmelden kann.', actions: secRunBtns(['full']) + btn('Sicherheitszentrale', '#/sicherheit', { ico: 'shield' }) });
    if (!r) return hd + `<div id="sec-prog"></div>` + note('Noch kein Prüfbericht vorhanden.', 'warn');
    const hosts = r.hosts.length ? r.hosts : (r.discovered || []).map(h => Object.assign({ known: true, ports: [], scanned: false, slug: '' }, h));
    const nf = hosts.filter(h => secFor(h.slug, h.ip).length).length, unk = (r.findings || []).filter(f => f.id.startsWith('unknown:')).length;
    return hd + `<div id="sec-prog"></div>` +
      (r.hosts.length ? '' : note('Es gibt noch keine Vollprüfung – angezeigt werden nur die gefundenen Geräte. Die Vollprüfung läuft täglich um 02:15 oder über den Knopf oben.', 'warn')) +
      `<div class="grid cols-4">${[['Geräte aktiv', (r.discovered || hosts).length, 'devices', `Vollprüfung ${r.last.full ? ago(r.last.full) : 'noch nie'}`], ['Offene Dienste', hosts.reduce((a, h) => a + h.ports.length, 0), 'layers', 'TCP (1.000 Ports) + UDP'], ['Geräte mit Befunden', nf, 'alert', `${hosts.length - nf} ohne Befund`], ['Unbekannte Geräte', unk, 'help', 'nicht im Inventar']].map(([l, v, ic, sub]) => `<div class="card tile"><div class="tile-top">${icon(ic)}<span class="tile-l">${esc(l)}</span></div><div class="tile-row"><div class="tile-val"><span class="v">${v}</span></div></div><div class="tile-sub">${esc(sub)}</div></div>`).join('')}</div>` +
      section('Geräte', `<div class="filters">${segCtl('net-f', [['alle', 'Alle'], ['befund', 'Mit Befund'], ['unbekannt', 'Unbekannt']], NETUI.f)}${searchField('net-q', 'Name, IP, MAC, Port, Software …', NETUI.q)}<span class="f-right muted small" id="net-n"></span></div>
        <div class="stack-v">${hosts.map(h => secHostCard(h, r)).join('')}</div><div id="net-empty" hidden>${emptyMsg('Kein Gerät passt zur Auswahl.')}</div>`, { sub: 'aufklappen für Ports, erkannte Software und Befunde' });
  },
  mount() {
    const apply = () => {
      let n = 0;
      document.querySelectorAll('.net-h').forEach(el => { const on = (NETUI.f === 'alle' || el.dataset.f.includes(NETUI.f)) && (!NETUI.q || el.dataset.k.includes(NETUI.q.toLowerCase())); el.hidden = !on; if (on) n++; });
      setText('net-n', `${n} von ${document.querySelectorAll('.net-h').length}`);
      const em = document.getElementById('net-empty'); if (em) em.hidden = n > 0;
    };
    wireSel('net-f', v => { NETUI.f = v; apply(); });
    document.getElementById('net-q')?.addEventListener('input', e => { NETUI.q = e.target.value.trim(); apply(); });
    apply();
    secWatch();
  },
  async live() { if (S.secWatching) return; const g = S.sec?.generated; await loadSec(true); if (S.sec?.generated !== g && APP.key === 'netzpruefung' && !document.querySelector('.net-h[open]')) route(); },
};

/* Bausteine für andere Seiten */
function secDnsLine(ip) { // DNS-Wächter: Kurzzeile für ein Gerät
  const c = (S.sec?.dns?.clients || []).find(x => x.ip === ip);
  return c ? `${fmt(c.q, 0)} Anfragen · ${fmt(c.blocked, 0)} gesperrt · ${c.q ? fmt(100 * c.nx / c.q, 0) : 0} % ohne Ergebnis${c.threat ? ` · ${pill(`${c.threat} Bedrohungs-Treffer`, 'warn', 'alert')}` : ''}` : '';
}
function secDeviceCard(d) { // Gerät-Detail: offene Dienste und Befunde aus der letzten Prüfung
  const r = S.sec; if (!r) return '';
  const ip = firstIp(d), h = (r.hosts || []).find(x => x.slug === devId(d) || x.ip === ip), fs = secFor(devId(d), ip);
  const ev = secEvFor(devId(d), ip), dl = secDnsLine(ip);
  if (!h && !fs.length && !ev.length) return '';
  return card('Sicherheitsprüfung', `${fs.length ? `<div class="stack-v">${fs.map(secAction).join('')}</div>` : `<p class="muted small" style="margin:0">Keine Befunde in der letzten Prüfung.</p>`}
    ${h ? `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:12px">${h.ports.map(p => monoChip(`${p.port}/${p.proto} ${p.service || ''}`)).join('') || '<span class="muted small">keine offenen Ports gefunden</span>'}</div><p class="small muted" style="margin:10px 0 0">Vollprüfung ${r.last.full ? ago(r.last.full) : 'noch nie'} · <a href="#/netzpruefung?q=${esc(h.ip)}">in der Netzwerkprüfung</a></p>` : ''}
    ${dl ? `<p class="small muted" style="margin:8px 0 0">${icon('shield')} DNS (24 h): ${dl}</p>` : ''}${ev.length ? `<h4 class="sa-sub">Verlauf</h4>${secEvRows(ev.slice(0, 5))}` : ''}`,
  { ico: 'shield', aside: fs.length ? pill(`${fs.length} Befund${fs.length > 1 ? 'e' : ''}`, fs.some(f => f.sev === 'hoch') ? 'bad' : 'warn', 'alert') : pill('ok', 'ok', 'checkCircle') });
}
function secOverview() { // Übersicht: zwei Lage-Karten – Bewertung und offene Befunde nach Stufe (im Lage-Raster, #sec-ov ist display:contents)
  const r = S.sec, tone = r ? secGradeTone(r.grade) : '';
  const c = r?.counts || {}, open = r ? r.findings.filter(f => f.sev !== 'info') : [];
  const total = Math.max(1, (c.hoch || 0) + (c.mittel || 0) + (c.niedrig || 0));
  const scoreCard = `<div class="card lage ov-4"><div class="card-h"><span class="ico">${icon('shield')}</span><h3>Sicherheit</h3></div>
    <div class="card-b">${r ? `<div class="score">${scoreRing(r.score, r.grade)}<div class="score-t ${tone}"><span class="grade">Bewertung <b>${esc(r.grade)}</b></span><h3>${esc(secGradeText(r.grade))}</h3><p>${open.length ? `${open.length} offene Punkte` : 'Keine offenen Punkte'}${(r.accepted || []).length ? ` · ${r.accepted.length} akzeptiert` : ''}</p></div></div>` : emptyMsg('Noch kein Prüfbericht – der Prüfdienst auf dem Pi schreibt ihn nach der ersten Schnellprüfung.')}
    <div class="lage-foot"><span>${r ? `geprüft ${ago(r.generated)}` : ''}</span><a href="#/sicherheit">Sicherheitszentrale ${icon('right')}</a></div></div></div>`;
  const top = open.slice(0, 3);
  const sevCard = `<div class="card lage ov-4"><div class="card-h"><span class="ico">${icon('alert')}</span><h3>Offene Befunde nach Stufe</h3></div>
    <div class="card-b"><ul class="sev-list">${['hoch', 'mittel', 'niedrig'].map(k => `<li><a class="sev-row ${k}${c[k] ? '' : ' zero'}" href="#/sicherheit?sev=${k}"><span class="sl">${SEC_SEV[k].label}</span><span class="sb"><i style="width:${((c[k] || 0) / total * 100).toFixed(1)}%"></i></span><span class="sn">${c[k] ?? '–'}</span></a></li>`).join('')}</ul>
    ${top.length ? `<ul class="sev-top">${top.map(f => `<li class="${esc(f.sev)}">${icon(SEC_CAT_ICON[f.cat] || 'alert')}<span>${esc(f.title)}</span></li>`).join('')}</ul>` : r ? `<p class="muted small" style="margin:14px 0 0">Keine offenen Befunde.</p>` : ''}
    <div class="lage-foot"><span>${(r?.resolved || []).length ? `${r.resolved.length} behoben in 30 Tagen` : ''}</span><a href="#/sicherheit?sev=alle">Maßnahmenplan ${icon('right')}</a></div></div></div>`;
  return scoreCard + sevCard;
}
const SEC_NAV = ['Sicherheit', [['sicherheit', 'Sicherheitszentrale', 'shield', () => (S.sec?.counts?.hoch ? { n: S.sec.counts.hoch, alert: true } : null)], ['netzpruefung', 'Netzwerkprüfung', 'radar']]];
