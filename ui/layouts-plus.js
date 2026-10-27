/* Responsive feature panels, built from the dashboard's existing readings. */
(function () {
  'use strict';
  var view = '', feature = null, latest = null;
  var entries = [
    {id:'solstice',label:'Sonnenlauf · Tag & Mond',accent:'#f6be78',background:'#111c2e'},
    {id:'atelier',label:'Atelier · Elegant',accent:'#d9bc8f',background:'#191817'},
    {id:'mono',label:'Mono · Minimalistisch',accent:'#c5d2df',background:'#12161b'},
    {id:'nexus',label:'Nexus · Alle Infos',accent:'#69ead1',background:'#091b22',instruments:true},
    {id:'tide',label:'Tide · Energie im Fluss',accent:'#86ded7',background:'#0b282e'},
    {id:'prism',label:'Prisma · Tageskompass',accent:'#c6afff',background:'#1c1730'}
  ];
  function q(selector) { return document.querySelector(selector); }
  function esc(value) { return String(value == null ? '' : value).replace(/[&<>"']/g,function (c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function num(value, digits) { return value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('de-DE',{maximumFractionDigits:digits || 0}); }
  function text(selector, value) { var el=q(selector); if(el)el.textContent=value; }
  function card(title, value, sub) { return '<article class="lx-insight"><span>'+esc(title)+'</span><strong>'+esc(value)+'</strong><p>'+esc(sub || '')+'</p></article>'; }
  function nextEvent(d) { var event=(d.agenda || [])[0]; return event ? {title:event.title,sub:[event.day,event.time].filter(Boolean).join(' · ')} : {title:d.agenda == null ? 'Kalender nicht verbunden' : 'Zeit für dich',sub:d.agenda == null ? 'Noch keine Termindaten verfügbar' : 'Keine anstehenden Termine'}; }
  function moonSvg() {
    return '<svg class="lx-moon" viewBox="0 0 100 100" role="img" aria-label="Aktuelle Mondphase"><defs><radialGradient id="lxMoonLight" cx="35%" cy="30%"><stop stop-color="#fff9e6"/><stop offset="1" stop-color="#aab6ce"/></radialGradient><clipPath id="lxMoonClip"><path id="lxMoonClipPath"/></clipPath></defs><circle cx="50" cy="50" r="49.5" fill="#26334a"/><path id="lxMoonLightPath" fill="url(#lxMoonLight)"/><g clip-path="url(#lxMoonClip)" fill="#596780" opacity=".24"><circle cx="32" cy="27" r="11"/><circle cx="66" cy="67" r="15"/><circle cx="71" cy="29" r="6"/><circle cx="28" cy="71" r="7"/><circle cx="50" cy="48" r="4"/></g><circle cx="50" cy="50" r="49.5" fill="none" stroke="#b6c9e4" stroke-opacity=".22"/></svg>';
  }
  function mount(id) {
    view=id;
    if (!q('body').classList.contains('layout-new')) return;
    q('#tTiles').addEventListener('keydown',function(event){
      var tile=event.target.closest('.ttile');
      if(tile && (event.key==='Enter' || event.key===' ')){event.preventDefault();tile.click();}
    });
    if (id==='horizon' || entries.some(function(entry){return entry.id===id;})) {
      feature=document.createElement('section');feature.id='layoutFeature';feature.className='lx-feature';
      q('.tscreen').appendChild(feature);
    }
    if(id==='solstice') {
      feature.classList.add('lx-celestial');
      feature.innerHTML='<div class="lx-sky-copy"><span class="lx-eyebrow">Im Rhythmus des Tages</span><h2 id="lxSkyTitle">Dein Himmel, live.</h2><p id="lxSkySubtitle">Wetterstandort wird geladen …</p></div><div class="lx-sky-scene" aria-hidden="true"><div class="lx-stars"></div><div class="lx-sun"></div><div class="lx-moon-wrap">'+moonSvg()+'</div><svg class="lx-ridges" viewBox="0 0 800 220" preserveAspectRatio="none"><path d="M0 130 Q130 15 290 115 T580 80 T800 110 V220 H0Z" fill="currentColor" opacity=".35"/><path d="M0 170 Q160 80 380 165 T800 140 V220 H0Z" fill="currentColor" opacity=".7"/><path d="M0 205 Q240 140 470 200 T800 185 V220 H0Z" fill="currentColor"/></svg></div><div class="lx-sky-facts"><div><span>Sonnenaufgang</span><b id="lxSunrise">—</b></div><div><span>Sonnenuntergang</span><b id="lxSunset">—</b></div><div><span id="lxSkyFactLabel">Mondbeleuchtung</span><b id="lxSkyFact">—</b></div></div><div class="lx-day-track"><i id="lxDayProgress"></i></div>';
      tick();
      setInterval(tick,30000);
      document.addEventListener('visibilitychange',function(){if(!document.hidden)tick();});
    }
    if(id==='mono') {
      var more=document.createElement('details');more.className='lx-more';
      var summary=document.createElement('summary');summary.textContent='Wetter, Termine & weitere Informationen';more.appendChild(summary);
      var content=document.createElement('div');content.className='lx-more-content';more.appendChild(content);
      content.appendChild(q('.tweather'));content.appendChild(q('.tagenda'));content.appendChild(q('#tTiles'));
      q('#tab').appendChild(more);
    }
  }
  function dateKey(date,zone) {
    try {
      var parts=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(date), values={};
      parts.forEach(function(p){values[p.type]=p.value;});return values.year+'-'+values.month+'-'+values.day;
    } catch(e) { return date.toISOString().slice(0,10); }
  }
  function tick() {
    if(view!=='solstice' || !window.JarvisSky) return;
    var now=new Date(), weather=latest && latest.weather || {}, location=weather.location || {};
    var solar=JarvisSky.sun(now,location.latitude,location.longitude), lunar=JarvisSky.moon(now);
    var shape=JarvisSky.moonPath(lunar.fraction,lunar.waxing);
    ['#lxMoonLightPath','#lxMoonClipPath'].forEach(function(selector){var el=q(selector);if(el){el.setAttribute('d',shape.path);el.setAttribute('transform',shape.transform);}});
    var moonLabel=lunar.label+' · ≈ '+Math.round(lunar.fraction*100)+' % beleuchtet';
    var moon=q('.lx-moon');if(moon)moon.setAttribute('aria-label',moonLabel);
    var row=(weather.sun_times || []).filter(function(item){return item.date===dateKey(now,location.timezone);})[0];
    text('#lxSunrise',row && row.sunrise ? row.sunrise.slice(-5) : '—');
    text('#lxSunset',row && row.sunset ? row.sunset.slice(-5) : '—');
    if(!solar){document.body.dataset.sky='unknown';text('#lxSkySubtitle','Sonnenstand wartet auf den Wetterstandort · '+moonLabel);text('#lxSkyFact','≈ '+Math.round(lunar.fraction*100)+' %');return;}
    var night=solar.altitude < -.833;
    var period=solar.altitude >= 6 ? 'day' : solar.altitude >= -.833 ? 'golden' : solar.altitude >= -6 ? 'twilight' : 'night';
    document.body.dataset.sky=period;document.body.classList.toggle('sky-moon',night);
    var titles={day:'Ein Tag voller Möglichkeiten.',golden:'Das Licht wird golden.',twilight:'Der Tag wird leiser.',night:'Die Nacht gehört dir.'};
    if(solar.hourAngle<0 && period==='twilight')titles.twilight='Ein neuer Tag kündigt sich an.';
    text('#lxSkyTitle',titles[period]);
    text('#lxSkySubtitle',night ? moonLabel : 'Sonne ≈ '+num(solar.altitude,1)+'° über dem Horizont · '+(weather.sky || 'am Wetterstandort'));
    text('#lxSkyFactLabel',night ? 'Mondbeleuchtung' : 'Tageslicht vergangen');
    text('#lxSkyFact',night ? '≈ '+Math.round(lunar.fraction*100)+' %' : Math.round(solar.progress*100)+' %');
    var scene=q('.lx-sky-scene');scene.style.setProperty('--sun-x',(8+solar.progress*84)+'%');scene.style.setProperty('--sun-y',(74-Math.max(0,solar.altitude)*.72)+'%');
    q('#lxDayProgress').style.width=(solar.progress*100)+'%';
    var meta=q('meta[name="theme-color"]');if(meta)meta.content=night ? '#111c2e' : period==='golden' ? '#593e3b' : '#203c50';
  }
  function richTiles(d) {
    var box=q('#tTiles'), nodes=Array.prototype.slice.call(box.children).filter(function(node){return node.classList.contains('ttile');});
    if(!nodes.length)return;
    var mail=box.querySelector('[data-kind="mail"]'), tech=box.querySelector('[data-kind="tech"]');
    if(mail){
      var m=d.mail || {};mail.classList.add('mail');mail.tabIndex=0;
      mail.innerHTML='<div class="lx-tile-heading"><span>Posteingang</span><b>'+num(m.unread)+'<small> ungelesen</small></b></div><ul class="lx-mail-list">'+(m.latest || []).map(function(item){return '<li><div><strong>'+esc(item.sender)+'</strong><small>'+esc(item.when || '')+'</small></div><p>'+esc(item.subject || '(ohne Betreff)')+'</p></li>';}).join('')+'</ul><em class="lx-tile-foot">'+esc(m.at ? 'Stand '+m.at+' · ' : '')+esc(num(m.accounts))+' '+(m.accounts===1 ? 'Postfach' : 'Postfächer')+' · Details öffnen ›</em>';
    }
    if(tech){
      tech.classList.add('tech');tech.tabIndex=0;
      var rows=[], nas=d.nas || {}, pi=d.pi || {}, router=(d.house || {}).router;
      if(nas.model || nas.error)rows.push(['NAS',nas.error ? 'Nicht erreichbar' : (nas.ok===false ? 'Prüfen' : num(nas.used_pct)+' % belegt')]);
      if(pi.temp!=null || pi.error)rows.push(['Raspberry Pi',pi.error ? 'Nicht erreichbar' : num(pi.up)+' / '+num(pi.containers)+' Container · '+num(pi.temp)+' °C']);
      if(router)rows.push(['Internet',router.online ? 'Verbunden' : 'Offline']);
      var list=document.createElement('ul');list.className='lx-system-list';
      list.innerHTML=rows.map(function(row){return '<li><span>'+esc(row[0])+'</span><strong>'+esc(row[1])+'</strong></li>';}).join('');
      tech.appendChild(list);
      var pc=d.pc || {}, cells=[];
      if(!pc.error){[['CPU','pc_cpu'],['RAM','pc_ram'],['VRAM','pc_vram'],['Datenträger','pc_disk']].forEach(function(item){
        if(pc[item[1]]!=null)cells.push('<div><span>'+item[0]+'</span><strong>'+num(pc[item[1]])+' %</strong></div>');
      });}
      if(cells.length){var metrics=document.createElement('div');metrics.className='lx-system-metrics';metrics.innerHTML=cells.join('');tech.appendChild(metrics);}
    }
    if(view==='bento')return;
    var metrics=document.createElement('div'),updates=document.createElement('div');
    metrics.className='lx-metrics';updates.className='lx-updates';
    nodes.forEach(function(node){(node===mail || node===tech ? updates : metrics).appendChild(node);});
    box.textContent='';if(metrics.children.length)box.appendChild(metrics);
    if(updates.children.length){updates.classList.toggle('single',updates.children.length===1);box.appendChild(updates);}
    box.classList.add('lx-grouped');
  }
  function render(d) {
    latest=d;
    if(!document.body.classList.contains('layout-new'))return;
    richTiles(d);
    if(!feature)return;
    var next=nextEvent(d), h=d.house || {}, me=d.me || {}, mail=d.mail || {}, pc=d.pc || {};
    if(view==='solstice'){tick();return;}
    if(view==='atelier') {
      feature.innerHTML='<span class="lx-eyebrow">Ein guter Tag beginnt mit Übersicht</span><h2>Raum für das Wesentliche.</h2><div class="lx-appointment"><span>Dein nächster Termin</span><strong>'+esc(next.title)+'</strong><p>'+esc(next.sub)+'</p></div><div class="lx-inline-stats">'+card('Zuhause',h.rooms && h.rooms.length ? num(h.rooms[0].temp,1)+' °C' : '—',h.rooms && h.rooms[0] ? h.rooms[0].name : 'Keine Raumdaten')+card('Wohlbefinden',num(me.battery),'Body Battery')+'</div>';
    } else if(view==='mono') {
      feature.innerHTML='<span class="lx-eyebrow">Als Nächstes</span><h2>'+esc(next.title)+'</h2><p>'+esc(next.sub)+'</p>';
    } else if(view==='nexus') {
      feature.innerHTML='<div class="lx-feature-head"><span class="lx-eyebrow">Deine Lageübersicht</span><span>Bestehende Live-Daten</span></div><div class="lx-insights">'+card('Nächster Termin',next.title,next.sub)+card('Energie heute',h.error ? '—' : num(h.gen_kwh,1)+' kWh','Solarertrag')+card('Gesundheit',me.error ? '—' : num(me.steps)+' Schritte','Schlaf '+num(me.sleep_h,1)+' h · Stress '+num(me.stress))+card('Rechenleistung',pc.error ? 'Nicht erreichbar' : num(pc.pc_cpu)+' % CPU','RAM '+num(pc.pc_ram)+' % · GPU '+num(pc.pc_gpu_temp)+' °C')+'</div>';
    } else if(view==='tide') {
      feature.innerHTML='<div class="lx-feature-head"><div><span class="lx-eyebrow">Energie im Fluss</span><h2>Dein Zuhause atmet.</h2></div><span>'+esc(h.error ? 'Keine Energiedaten' : 'Solar & Speicher')+'</span></div><div class="lx-flow"><svg viewBox="0 0 600 180" role="img" aria-label="Solar und Batteriespeicher"><defs><linearGradient id="lxFlowColor"><stop stop-color="#f4d8a3"/><stop offset="1" stop-color="#78e1d0"/></linearGradient></defs><path d="M110 90 C210 10 390 170 490 90" fill="none" stroke="url(#lxFlowColor)" stroke-width="2"/><path d="M110 90 C210 170 390 10 490 90" fill="none" stroke="url(#lxFlowColor)" stroke-width="1" opacity=".3"/><circle cx="110" cy="90" r="54" fill="#24494c" stroke="#d8c69c"/><circle cx="490" cy="90" r="54" fill="#24494c" stroke="#78e1d0"/><text x="110" y="86" text-anchor="middle">'+esc(h.error ? '—' : num(h.solar_w))+' W</text><text class="lx-flow-label" x="110" y="110" text-anchor="middle">SOLAR</text><text x="490" y="86" text-anchor="middle">'+esc(h.error ? '—' : num(h.soc))+' %</text><text class="lx-flow-label" x="490" y="110" text-anchor="middle">SPEICHER</text></svg><div class="lx-flow-caption">'+esc(h.error ? 'Warte auf Home Assistant.' : h.charge_w>5 ? 'Speicher lädt mit '+num(h.charge_w)+' W' : h.out_w>5 ? 'Speicher liefert '+num(h.out_w)+' W' : 'Speicher ruht')+'</div></div><div class="lx-inline-stats">'+card('Ertrag heute',h.error ? '—' : num(h.gen_kwh,1)+' kWh','Aus deiner Solaranlage')+card('Luftqualität',h.air || '—','PM2,5 '+num(h.pm25,1)+' µg/m³')+'</div>';
    } else if(view==='prism') {
      feature.innerHTML='<div class="lx-feature-head"><div><span class="lx-eyebrow">Dein Tageskompass</span><h2>Alles greift ineinander.</h2></div></div><div class="lx-prism-board">'+card('01 / Zeit',next.title,next.sub)+card('02 / Zuhause',h.rooms && h.rooms[0] ? num(h.rooms[0].temp,1)+' °C' : '—',h.rooms && h.rooms[0] ? h.rooms[0].name : 'Keine Raumdaten')+card('03 / Energie',num(me.battery),'Deine Body Battery')+card('04 / Post',mail.error ? 'Nicht verbunden' : num(mail.unread)+' ungelesen',mail.latest && mail.latest[0] ? mail.latest[0].subject : 'Dein Posteingang')+'</div>';
    } else if(view==='horizon') {
      feature.innerHTML='<span class="lx-eyebrow">Mehr von deinem Tag</span><div class="lx-insights">'+card('Als Nächstes',next.title,next.sub)+card('Deine Energie',num(me.battery),'Body Battery · '+num(me.sleep_h,1)+' h Schlaf')+card('Sonnenuntergang',(d.weather || {}).sunset || '—','Am eingestellten Wetterstandort')+'</div>';
    }
  }
  window.JarvisLayoutPlus={entries:entries,mount:mount,render:render};
})();
