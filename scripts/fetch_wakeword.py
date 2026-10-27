"""
The wake word on the iPad: fetch the ONNX runtime and the openWakeWord models once —
the official "hey jarvis" and two community models for a plain "Jarvis" (JARVIS_WAKE_LOCAL_MODEL).

    .venv\\Scripts\\python.exe scripts\\fetch_wakeword.py

Puts them into ui/vendor/ (not in git), from where the server hands them to paired
devices. Every file is checked against its SHA-256 below — a changed download is
refused, nothing half-written stays behind. Without these files the iPad simply
sends its clips to the PC as before.
"""
import hashlib
import io
import pathlib
import sys
import tarfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "ui" / "vendor"

ORT = "1.20.1"   # onnxruntime-web, MIT
ORT_TGZ = (f"https://registry.npmjs.org/onnxruntime-web/-/onnxruntime-web-{ORT}.tgz",
           "0a38facf0672dfaaeaf352e8a9f44533732b60b4af93b9dd507fe7c2d2be437b")
ORT_FILES = {   # only the WebAssembly build, single-threaded is enough for three small models
    "ort.wasm.min.js": "e66568724f8848e57cc2a56e4bea3a5b86ce3ff81b2da11eac8ed7ab02b27bd4",
    "ort-wasm-simd-threaded.mjs": "745eb7c0ce6f18a6aa521971b2877babc7ffb27eecb58ab3bc6e5ef4692672e8",
    "ort-wasm-simd-threaded.wasm": "207d02be4591c156b0a98f024f3d58005b5b04c92274d759fb390338c63559ea",
}
OWW = "v0.5.1"   # openWakeWord, Apache 2.0
OWW_FILES = {
    "melspectrogram.onnx": "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f",
    "embedding_model.onnx": "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f",
    "hey_jarvis_v0.1.onnx": "94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb",
}
# Trained on "jarvis", "jarvis!", "jarvis?" and "hey jarvis" — fwartner/home-assistant-wakewords-collection
# (MIT), pinned to one commit. v1 hears more and errs more, v2 the other way round (its README).
COMMUNITY = "1c6d6a82947ff1367d8e5e50cc999c00943ccb9c"
COMMUNITY_FILES = {
    "jarvis_v1.onnx": "32171d04d3e4b6fdb8907412ab060c486a2b0a8bc6cff0703212a812b5dd5056",
    "jarvis_v2.onnx": "dae408c0fa69ec888bf8e3a8b41a41f97677522be3b8163821e4105fa754b988",
}


def _get(url, sha):
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    _check(url, data, sha)
    return data


def _check(name, data, sha):
    got = hashlib.sha256(data).hexdigest()
    if got != sha:
        sys.exit(f"Prüfsumme stimmt nicht: {name}\n  erwartet {sha}\n  bekommen {got}")


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def main():
    ort_dir, oww_dir = VENDOR / f"ort-{ORT}", VENDOR / f"oww-{OWW}"
    wanted = list(OWW_FILES) + list(COMMUNITY_FILES)
    if all((ort_dir / n).exists() for n in ORT_FILES) and all((oww_dir / n).exists() for n in wanted):
        print(f"Schon da: {ort_dir.relative_to(ROOT)}, {oww_dir.relative_to(ROOT)}")
        return
    if not all((ort_dir / n).exists() for n in ORT_FILES):
        print(f"onnxruntime-web {ORT} …")
        with tarfile.open(fileobj=io.BytesIO(_get(*ORT_TGZ)), mode="r:gz") as tgz:
            for name, sha in ORT_FILES.items():
                data = tgz.extractfile(f"package/dist/{name}").read()
                _check(name, data, sha)
                _write(ort_dir / name, data)
    for name, sha in OWW_FILES.items():
        if not (oww_dir / name).exists():
            print(f"openWakeWord {name} …")
            _write(oww_dir / name, _get(f"https://github.com/dscripka/openWakeWord/releases/download/{OWW}/{name}", sha))
    for name, sha in COMMUNITY_FILES.items():
        if not (oww_dir / name).exists():
            print(f"Community-Modell {name} …")
            _write(oww_dir / name, _get("https://raw.githubusercontent.com/fwartner/home-assistant-wakewords-collection/"
                                        f"{COMMUNITY}/en/jarvis/{name}", sha))
    print(f"Fertig: {VENDOR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
