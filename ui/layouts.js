/* One catalogue for both menus. Favorites stay on this device; the selected view is shared by Jarvis. */
(function () {
  var entries = [
    { id: 'tablet', label: 'Raum-Display' },
    { id: 'stark', label: 'Stark-HUD' },
    { id: 'classic', label: 'Klassisch mit Gedächtnis-Graph' },
    { id: 'aurora', label: 'Aurora · Glas & Farbverlauf', accent: '#73efd1', background: '#10142b' },
    { id: 'ivory', label: 'Tageslicht · Hell & klar', accent: '#28716b', background: '#f4f2ec' },
    { id: 'midnight', label: 'Mitternacht · Panorama', accent: '#9bbcff', background: '#090f20' },
    { id: 'cockpit', label: 'Cockpit · Systemzentrale', accent: '#f4b85a', background: '#10120f', instruments: true },
    { id: 'bento', label: 'Bento · Große Kacheln', accent: '#b8a2ff', background: '#141221' },
    { id: 'horizon', label: 'Horizont · Tagesübersicht', accent: '#ffb48e', background: '#211c23' },
    { id: 'editorial', label: 'Journal · Magazin', accent: '#9b4b32', background: '#f1ece2' },
    { id: 'neon', label: 'Neon · Kontrast & Konturen', accent: '#f481df', background: '#110c1d' },
    { id: 'zen', label: 'Zen · Ruhe & Übersicht', accent: '#9fcbb0', background: '#15201d' },
    { id: 'orbit', label: 'Orbit · Raumstation', accent: '#70c8ff', background: '#070f1b', instruments: true }
  ].concat(window.JarvisLayoutPlus ? JarvisLayoutPlus.entries : []);
  var KEY = 'jarvis.layoutFavorites', active = '', favorites = [], feedback = null;
  function get(id) { return entries.filter(function (entry) { return entry.id === id; })[0]; }
  function readFavorites() {
    try {
      var saved = JSON.parse(localStorage.getItem(KEY) || '[]');
      return Array.isArray(saved) ? saved.filter(function (id, index) { return !!get(id) && saved.indexOf(id) === index; }) : [];
    } catch (e) { return []; }
  }
  favorites = readFavorites();
  function populate() {
    ['layoutSel', 'tLayout'].forEach(function (id) {
      var select = document.getElementById(id);
      if (!select) return;
      var previous = active || select.value;
      select.textContent = '';
      function group(label, items) {
        if (!items.length) return;
        var box = document.createElement('optgroup'); box.label = label;
        items.forEach(function (entry) {
          var option = document.createElement('option'); option.value = entry.id;
          option.textContent = (favorites.indexOf(entry.id) >= 0 ? '★ ' : '') + entry.label;
          box.appendChild(option);
        });
        select.appendChild(box);
      }
      group('★ Favoriten', favorites.map(get));
      group('Neu · Entdecken', entries.slice(13).filter(function (entry) { return favorites.indexOf(entry.id) < 0; }));
      group('Weitere Ansichten', entries.slice(3, 13).filter(function (entry) { return favorites.indexOf(entry.id) < 0; }));
      group('Bisherige Ansichten', entries.slice(0, 3).filter(function (entry) { return favorites.indexOf(entry.id) < 0; }));
      if (get(previous)) select.value = previous;
    });
    document.querySelectorAll('.layout-favorite').forEach(function (button) {
      var selected = favorites.indexOf(active) >= 0;
      button.setAttribute('aria-pressed', String(selected));
      button.setAttribute('aria-label', selected ? 'Layout aus Favoriten entfernen' : 'Layout als Favorit speichern');
      button.title = button.getAttribute('aria-label');
      button.textContent = button.classList.contains('layout-favorite-full') ? (selected ? '★ Als Favorit gespeichert' : '☆ Als Favorit speichern') : (selected ? '★' : '☆');
    });
  }
  function toggleFavorite() {
    if (!get(active)) return;
    var index = favorites.indexOf(active);
    if (index >= 0) favorites.splice(index, 1); else favorites.push(active);
    var persisted = true;
    try { localStorage.setItem(KEY, JSON.stringify(favorites)); } catch (e) { persisted = false; }
    populate();
    if (feedback) feedback.textContent = persisted ? 'Favoriten werden auf diesem Gerät gespeichert.' : 'Speichern ist in diesem Browser gesperrt; der Stern gilt nur bis zum Neuladen.';
  }
  ['layoutSel', 'tLayout'].forEach(function (id) {
    var select = document.getElementById(id);
    if (!select) return;
    var button = document.createElement('button'); button.type = 'button';
    button.className = 'layout-favorite' + (id === 'tLayout' ? ' layout-favorite-full' : '');
    button.onclick = toggleFavorite;
    if (id === 'tLayout') {
      select.parentNode.insertAdjacentElement('afterend', button);
      feedback = document.createElement('p'); feedback.className = 'layout-feedback'; feedback.setAttribute('role', 'status');
      feedback.textContent = 'Favoriten werden auf diesem Gerät gespeichert.';
      button.insertAdjacentElement('afterend', feedback);
    } else select.insertAdjacentElement('afterend', button);
  });
  populate();
  window.addEventListener('storage', function (event) {
    if (event.key === KEY || event.key === null) { favorites = readFavorites(); populate(); }
  });
  window.JarvisLayouts = {
    entries: entries, get: get,
    activate: function (id) {
      var entry = get(id); if (!entry) return;
      active = id; document.body.dataset.layout = id; populate();
      if (entry.accent) {
        document.body.classList.add('layout-new', 'layout-' + id);
        if (entry.instruments) document.body.classList.add('layout-instruments');
        var badge = document.createElement('span'); badge.className = 'layout-name'; badge.textContent = entry.label.split(' · ')[0];
        document.querySelector('.tbrand').appendChild(badge);
        document.querySelector('meta[name="theme-color"]').content = entry.background;
      }
      if (window.JarvisLayoutPlus) JarvisLayoutPlus.mount(id);
    }
  };
})();
