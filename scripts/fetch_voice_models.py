"""
Fetch the models of the voice engine into models/ (ignored by git):

  silero_vad.onnx   Silero VAD v5.1.2, MIT — https://github.com/snakers4/silero-vad  (~2.3 MB)

    .venv\\Scripts\\python.exe scripts\\fetch_voice_models.py

Without the file the engine falls back to an energy detector (see voice_vad.py). Checksums are
pinned; a mismatch means a different file than the one this code was tested with.
"""
import hashlib
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
FILES = {
    "silero_vad.onnx": (
        "https://raw.githubusercontent.com/snakers4/silero-vad/v5.1.2/src/silero_vad/data/silero_vad.onnx",
        "2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f"),
}


def main():
    MODELS.mkdir(exist_ok=True)
    ok = True
    for name, (url, sha) in FILES.items():
        target = MODELS / name
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == sha:
            print(f"  {name}: vorhanden")
            continue
        print(f"  {name}: lade {url}")
        try:
            data = urllib.request.urlopen(url, timeout=120).read()
        except Exception as e:  # noqa: BLE001
            print(f"  {name}: Download fehlgeschlagen: {e}")
            ok = False
            continue
        got = hashlib.sha256(data).hexdigest()
        if got != sha:
            print(f"  {name}: Prüfsumme falsch ({got[:12]}…), Datei verworfen")
            ok = False
            continue
        target.write_bytes(data)
        print(f"  {name}: {len(data) // 1024} KB gespeichert")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
