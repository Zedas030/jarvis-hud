# Raspberry Pi: Monitoring und Doku

Stand der beiden statischen Seiten, die der Raspberry Pi (nginx) im Heimnetz ausliefert und die das HUD
oben rechts verlinkt. Beide Kopien sind anonymisiert (Adressen, MAC-Adressen, Seriennummern, Tailnet,
Benutzername durch Platzhalter ersetzt).

| Ordner | Auf dem Pi | Port | Inhalt |
|---|---|---|---|
| `monitoring/` | `/var/www/monitoring` | 8092 | **ORBIT Monitor v3**: einseitige Anwendung (`index.html`, `assets/`, `data/kb.json`). Geräte, Dienste, Prometheus-Jobs, Timer, Backups, Befunde, Energiefluss, Runbooks. Die Unterordner `devices/`, `systems/` usw. sind nur Weiterleitungen alter v2-Adressen. |
| `raspi-doku/` | `/var/www/raspi-doku` | 8091 | **Pi-Dokumentation**: eine HTML-Seite (Dienste, Docker, Ports, Dateien, Wartung, Auffälligkeiten, Änderungsprotokoll) mit dem Abschnitt „Jarvis“ zu Steckbrief, Werkzeugen, Sicherheit, Stimme, Notbetrieb, Gedächtnis, Hintergrundjobs, Geräten, Ports und Betrieb. |

Die Wissensbasis `data/kb.json` wird auf dem Pi aus `build_kb.py` erzeugt (liegt im Release-Ordner auf
der NAS, nicht hier). Jarvis liest die Pi-Doku und das Monitoring nicht; sie sind die Beschreibung der
Umgebung, in der er läuft.
