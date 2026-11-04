[← Zurück zum README](../README.md)

# Sicherheitsmodell

Die Regeln, die im Code stehen und nicht im Prompt.

## Sicherheitsmodell

- **Nur lokal:** Der Server bindet an `127.0.0.1`. Jede Anfrage braucht ein pro Start erzeugtes Token, Host und Origin werden geprüft.
- **Fernzugriff nur gekoppelt:** Der Geräte-Port (`JARVIS_REMOTE=1`) bindet ebenfalls nur an `127.0.0.1` und ist nur über `tailscale serve` erreichbar. Ohne gekoppeltes Gerät gibt es dort nur die Kopplungsseite. Ein Code wird nach fünf Fehlversuchen verworfen. In `config/devices.json` liegen nur Hashes der Geräte-Schlüssel. Kopplung, Geräteliste und Bestätigungs-Schnittstelle sind nur am PC erreichbar. Optional beschränkt `JARVIS_REMOTE_HOSTS` die erlaubten Hostnamen (Standard: `*.ts.net`).
- **Bestätigungskarten:** Systemeingriffe laufen über Claude Codes `--permission-prompt-tool` zu einer Karte im HUD. Dazu gehören Shell-Befehle, Kalender-Einträge und Schreiben außerhalb des Vaults. Ohne Antwort wird nach 75 s abgelehnt.
- **Geschützte Geräte:** Router, PC, Server und Kühlschrank schaltet der Router nie selbst aus. Home Assistant schaltet `light`, `switch`, `fan`, `media_player` und `scene`; Thermostate nur für die in `config/home.json` unter `climate_groups` eingetragenen `climate.*`-Entities, mit Solltemperatur von 5–30 °C oder einem unterstützten Heizmodus `heat`, `off` oder `auto`.
- **Fragen schalten nie:** „Ist … an?“ wird gelesen, nie ausgeführt.
- **Kein Selbstumbau:** Zur Laufzeit gibt es keine Werkzeuge zum Bearbeiten von Code.
- **Daten bleiben lokal:** `.env`, `config/home.json`, `vault/`, `logs/`, `state.json` und Modelle stehen in `.gitignore`. Spracherkennung und GPU-Stimme laufen offline.
