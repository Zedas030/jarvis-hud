/* One catalogue for both menus. Favorites stay on this device; the selected view is shared by Jarvis. */
(function () {
  var entries = [
    { id: 'tablet', label: 'Room display' },
    { id: 'stark', label: 'Stark-HUD' },
    { id: 'classic', label: 'Classic with memory graph' },
    { id: 'aurora', label: 'Aurora · glass & gradient', accent: '#73efd1', background: '#10142b' },
    { id: 'ivory', label: 'Daylight · bright & clear', accent: '#28716b', background: '#f4f2ec' },
    { id: 'midnight', label: 'Midnight · panorama', accent: '#9bbcff', background: '#090f20' },
    { id: 'cockpit', label: 'Cockpit · system centre', accent: '#f4b85a', background: '#10120f', instruments: true },
    { id: 'bento', label: 'Bento · big tiles', accent: '#b8a2ff', background: '#141221' },
    { id: 'horizon', label: 'Horizon · day overview', accent: '#ffb48e', background: '#211c23' },
    { id: 'editorial', label: 'Journal · magazine', accent: '#9b4b32', background: '#f1ece2' },
    { id: 'neon', label: 'Neon · contrast & outlines', accent: '#f481df', background: '#110c1d' },
    { id: 'zen', label: 'Zen · calm & overview', accent: '#9fcbb0', background: '#15201d' },
    { id: 'orbit', label: 'Orbit · space station', accent: '#70c8ff', background: '#070f1b', instruments: true }
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
      group('★ Favourites', favorites.map(get));
      group('New · discover', entries.slice(13).filter(function (entry) { return favorites.indexOf(entry.id) < 0; }));
      group('More views', entries.slice(3, 13).filter(function (entry) { return favorites.indexOf(entry.id) < 0; }));
      group('Earlier views', entries.slice(0, 3).filter(function (entry) { return favorites.indexOf(entry.id) < 0; }));
      if (get(previous)) select.value = previous;
    });
    document.querySelectorAll('.layout-favorite').forEach(function (button) {
      var selected = favorites.indexOf(active) >= 0;
      button.setAttribute('aria-pressed', String(selected));
      button.setAttribute('aria-label', selected ? 'Remove layout from favourites' : 'Save layout as favourite');
      button.title = button.getAttribute('aria-label');
      button.textContent = button.classList.contains('layout-favorite-full') ? (selected ? '★ Saved as favourite' : '☆ Save as favourite') : (selected ? '★' : '☆');
    });
  }
  function toggleFavorite() {
    if (!get(active)) return;
    var index = favorites.indexOf(active);
    if (index >= 0) favorites.splice(index, 1); else favorites.push(active);
    var persisted = true;
    try { localStorage.setItem(KEY, JSON.stringify(favorites)); } catch (e) { persisted = false; }
    populate();
    if (feedback) feedback.textContent = persisted ? 'Favourites are saved on this device.' : 'Saving is blocked in this browser; the star only lasts until the next reload.';
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
      feedback.textContent = 'Favourites are saved on this device.';
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
