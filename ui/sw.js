/* Only one job: a page load that lands while the server restarts (Tailscale
   answers 502, or nothing answers at all) gets a small waiting page instead of
   an error page — the waiting page polls and reloads once Jarvis is back.
   Nothing is cached; every other request goes straight to the network. */
var WAIT = '<!doctype html><html lang="de"><head><meta charset="utf-8">'
  + '<meta name="viewport" content="width=device-width,initial-scale=1"><title>JARVIS</title>'
  + '<style>html,body{margin:0;height:100%;background:#04070c;color:#5fe4ff;font:16px system-ui,sans-serif}'
  + 'body{display:grid;place-items:center;text-align:center}b{display:block;font-size:22px;letter-spacing:.2em;margin-bottom:10px}'
  + 'span{color:#6b8796}</style></head><body><div><b>J.A.R.V.I.S.</b><span id="t">startet neu …</span></div>'
  + '<script>(function poll(n){fetch("/api/status",{cache:"no-store"}).then(function(r){'
  + 'if(r.status===401){location.href="/pair.html";return;}'
  + 'if(r.ok&&/json/.test(r.headers.get("content-type")||"")){location.reload();return;}'
  + 'throw 0;}).catch(function(){if(n>20)document.getElementById("t").textContent="noch nicht erreichbar — läuft der PC?";'
  + 'setTimeout(function(){poll(n+1);},3000);});})(0);</script></body></html>';

function wait() {
  return new Response(WAIT, { headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' } });
}

self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function (e) {
  if (e.request.mode !== 'navigate') return;
  e.respondWith(fetch(e.request).then(function (r) { return r.status >= 500 ? wait() : r; }, wait));
});
