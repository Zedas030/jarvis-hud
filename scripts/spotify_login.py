"""
One-time Spotify login for spotify.py (PKCE: only the Client ID is needed, no secret in .env).

    .venv\\Scripts\\python.exe scripts\\spotify_login.py

Opens the Spotify login in the browser, catches the answer on http://127.0.0.1:8722/callback and
stores the refresh token in config/spotify_token.json. Log in with the account that plays the music
(the family plan member whose phone is on the speaker).
"""
import base64
import hashlib
import json
import os
import pathlib
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if (ROOT / ".env").exists() else []:
    k, sep, v = line.strip().partition("=")
    if sep and not k.startswith("#"):
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
import paths  # noqa: E402,F401 — the jarvis/<group> folders
import spotify  # noqa: E402


def main():
    if not spotify.CLIENT_ID:
        sys.exit("JARVIS_SPOTIFY_CLIENT_ID fehlt in .env (Client ID der App auf developer.spotify.com).")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": spotify.CLIENT_ID, "response_type": "code", "redirect_uri": spotify.REDIRECT,
        "scope": spotify.SCOPES, "state": state, "code_challenge_method": "S256", "code_challenge": challenge})
    got = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state:
                got.update(code=q.get("code", [""])[0], error=q.get("error", [""])[0])
            text = "Fertig - dieses Fenster kann zu." if got.get("code") else "Kein Login erhalten."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(text.encode())

        def log_message(self, *a):
            pass

    host, port = urllib.parse.urlparse(spotify.REDIRECT).hostname, urllib.parse.urlparse(spotify.REDIRECT).port
    server = HTTPServer((host, port), Callback)
    print("Spotify-Login im Browser … (falls nichts aufgeht, diese Adresse öffnen)\n" + url, flush=True)
    webbrowser.open(url)
    while "code" not in got:
        server.handle_request()
    if not got["code"]:
        sys.exit(f"Spotify hat abgelehnt: {got.get('error') or 'unbekannt'}")
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": got["code"],
                                   "redirect_uri": spotify.REDIRECT, "client_id": spotify.CLIENT_ID,
                                   "code_verifier": verifier}).encode()
    req = urllib.request.Request(spotify.TOKEN_URL, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=10) as r:
        spotify.save_token(json.loads(r.read()))
    on, device = spotify.playing()
    print(f"Gespeichert in {spotify.TOKEN_FILE}. Spielt gerade: {'ja' if on else 'nein'}"
          + (f" (Gerät {device[:8]}…)" if device else ""))


if __name__ == "__main__":
    main()
