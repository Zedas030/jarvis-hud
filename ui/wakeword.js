/* Wake word on the device (Web Worker): openWakeWord's three models in ONNX Runtime Web,
   the same pipeline as the Python package — melspectrogram, then one embedding per 80 ms
   from the last 76 mel frames, then the word models over the last 16 embeddings.
   app.js (OnDevice) sends 1280 samples at 16 kHz in int16 range and gets a score back:
   `s` from the deciding model (the first in ?m=), `by` from every model, for the log.
   The files come from scripts/fetch_wakeword.py; without them this reports an error and
   the device keeps sending its clips to the PC. */
var ORT = 'vendor/ort-1.20.1/', OWW = 'vendor/oww-v0.5.1/';
var CHUNK = 1280, CONTEXT = 480, MEL_WINDOW = 76, MEL_KEEP = 120, FEATURES = 16;
// only these files, whatever the query says (wakeword.LOCAL_FILES on the server)
var FILES = { hey_jarvis: 'hey_jarvis_v0.1.onnx', jarvis_v1: 'jarvis_v1.onnx', jarvis_v2: 'jarvis_v2.onnx' };
var WANT = (new URLSearchParams(self.location.search).get('m') || 'hey_jarvis').split(',')
  .filter(function (n) { return FILES.hasOwnProperty(n); });
if (!WANT.length) WANT = ['hey_jarvis'];

var mel, emb, words = [], raw = new Float32Array(0), melRows = [], feats = [], queue = [], busy = false;

function reset() {
  raw = new Float32Array(0);
  melRows = [];
  for (var i = 0; i < MEL_WINDOW; i++) melRows.push(new Float32Array(32).fill(1));   // as openWakeWord starts
  feats = [];
}

async function load() {
  try {
    importScripts(ORT + 'ort.wasm.min.js');
    ort.env.wasm.numThreads = 1;              // no cross-origin isolation on this page; one thread is plenty
    ort.env.wasm.wasmPaths = new URL(ORT, self.location.href).href;
    var opt = { executionProviders: ['wasm'] };
    mel = await ort.InferenceSession.create(OWW + 'melspectrogram.onnx', opt);
    emb = await ort.InferenceSession.create(OWW + 'embedding_model.onnx', opt);
    for (var i = 0; i < WANT.length; i++) {
      try {
        words.push({ name: WANT[i], s: await ort.InferenceSession.create(OWW + FILES[WANT[i]], opt) });
      } catch (e) {
        if (!i) throw e;                       // without the deciding model nothing works; the others are extras
      }
    }
    reset();
    postMessage({ t: 'ready', models: words.map(function (w) { return w.name; }) });
  } catch (e) {
    postMessage({ t: 'error', msg: String(e && e.message || e).slice(0, 160) });
  }
}

async function step(chunk) {
  var next = new Float32Array(Math.min(raw.length, CONTEXT) + chunk.length);
  next.set(raw.subarray(Math.max(0, raw.length - CONTEXT)));
  next.set(chunk, next.length - chunk.length);
  raw = next;
  if (raw.length < CHUNK + CONTEXT) return null;

  var m = (await mel.run({ [mel.inputNames[0]]: new ort.Tensor('float32', raw, [1, raw.length]) }))[mel.outputNames[0]];
  var frames = m.dims[2];
  for (var f = 0; f < frames; f++) {
    var row = new Float32Array(32);
    for (var k = 0; k < 32; k++) row[k] = m.data[f * 32 + k] / 10 + 2;
    melRows.push(row);
  }
  if (melRows.length > MEL_KEEP) melRows.splice(0, melRows.length - MEL_KEEP);

  var win = new Float32Array(MEL_WINDOW * 32);
  for (var r = 0; r < MEL_WINDOW; r++) win.set(melRows[melRows.length - MEL_WINDOW + r], r * 32);
  var e = (await emb.run({ [emb.inputNames[0]]: new ort.Tensor('float32', win, [1, MEL_WINDOW, 32, 1]) }))[emb.outputNames[0]];
  feats.push(Float32Array.from(e.data));
  if (feats.length > FEATURES) feats.shift();
  if (feats.length < FEATURES) return null;

  var x = new Float32Array(FEATURES * 96);
  for (var i = 0; i < FEATURES; i++) x.set(feats[i], i * 96);
  var by = {};
  for (var w = 0; w < words.length; w++) {
    var m2 = words[w].s, out = await m2.run({ [m2.inputNames[0]]: new ort.Tensor('float32', x, [1, FEATURES, 96]) });
    by[words[w].name] = out[m2.outputNames[0]].data[0];
  }
  return { s: by[words[0].name], by: by };
}

async function drain() {
  if (busy) return;
  busy = true;
  try {
    while (queue.length) {
      // Fallen behind (a slow moment on the device): the newest second is what matters.
      if (queue.length > 12) queue.splice(0, queue.length - 12);
      var job = queue.shift(), score = await step(job.pcm);
      if (score !== null) postMessage({ t: 'score', s: score.s, by: score.by, at: job.at });
    }
  } catch (e) {
    postMessage({ t: 'error', msg: String(e && e.message || e).slice(0, 160) });
  } finally { busy = false; }
}

onmessage = function (ev) {
  var d = ev.data;
  if (d.t === 'pcm' && mel) { queue.push(d); drain(); }
  else if (d.t === 'reset') { queue = []; reset(); }
};

load();
