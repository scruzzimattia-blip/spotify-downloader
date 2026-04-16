# Spotify Downloader

Web-basierter Downloader für Spotify-Tracks, -Alben, -Artists und -Playlists.
Angetrieben von [`spotdl`](https://github.com/spotDL/spotify-downloader) und
serviert über FastAPI mit einer modernen, dunklen Oberfläche.

> Hinweis: Die eigentlichen Audio-Streams werden von `spotdl` auf YouTube gesucht
> und heruntergeladen. Spotify dient nur als Metadatenquelle.
> Achte auf die Urheberrechte in deinem Land.

## Features

- Modernes Dark-Theme UI (Desktop & Mobile)
- Unterstützt Tracks, Alben, Artists und Playlists
- **Formatwahl**: MP3, M4A, OPUS, FLAC, WAV pro Download
- Pipeline: Spotify → Metadaten (offizielle API) → yt-dlp → ffmpeg → mutagen-Tags
- Cover-Art wird aus Spotify in die Audiodateien embedded
- Asynchrone Job-Queue mit Live-Status, Track-Fortschritt und Log
- Mehrfach-Downloads werden automatisch als ZIP-Archiv gebündelt
- REST-API für Automatisierung (`/api/downloads`)
- Containerisiert mit `ffmpeg` als non-root-User
- Gitea-Actions-Workflow zum automatischen Bauen und Pushen des Images

## Schnellstart mit Docker Compose

```bash
cp .env.example .env
# SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET eintragen (siehe unten)
docker compose up -d --build
# UI öffnen: http://localhost:8000
```

Heruntergeladene Dateien landen im gemounteten Volume `./downloads`.

> **Wichtig:** Ohne eigene Spotify-API-Credentials landet spotdl in den
> öffentlichen, geteilten Rate-Limits und quittiert schnell mit
> `"Your application has reached a rate/request limit. Retry will occur
> after: 86400 s"`. Das Setup dafür ist zwei Klicks und kostenlos – siehe
> nächster Abschnitt.

## Spotify API Credentials (empfohlen)

1. Melde dich bei <https://developer.spotify.com/dashboard> an.
2. **Create app** → Name/Beschreibung frei wählen.
3. Als *Redirect URI* irgendwas wie `http://127.0.0.1:8080/` eintragen
   (wird nicht benötigt, aber Pflichtfeld).
4. Nach dem Speichern: **Settings** → *Client ID* kopieren, daneben
   *View client secret* anklicken und ebenfalls kopieren.
5. In `.env` setzen:

   ```dotenv
   SPOTIFY_CLIENT_ID=xxxxxxxxxxxxxxxx
   SPOTIFY_CLIENT_SECRET=xxxxxxxxxxxxxxxx
   ```

6. Container neu starten: `docker compose up -d`.

Solange die Werte leer sind, zeigt die UI oben ein orangenes Hinweis-Banner an.

## YouTube Cookies (sehr empfohlen)

YouTube blockiert zunehmend nicht-authentifizierte Zugriffe mit der Fehlermeldung
*"Sign in to confirm you're not a bot"*. Das ist keine App-eigene Limitierung,
sondern betrifft alle yt-dlp-basierten Tools.

**Abhilfe:** exportiere deine YouTube-Cookies im Netscape-Format und mounte sie
in den Container.

1. Browser-Extension installieren:
   - Chrome/Edge: *Get cookies.txt LOCALLY*
   - Firefox: *cookies.txt*
2. Auf <https://www.youtube.com> eingeloggt sein → Extension öffnen →
   *Export as Netscape* → als `cookies.txt` ins Projektverzeichnis legen.
3. In `docker-compose.yml` die auskommentierte Volume-Zeile aktivieren:

   ```yaml
   - ./cookies.txt:/etc/cookies.txt:ro
   ```

4. In `.env` setzen:

   ```dotenv
   YTDLP_COOKIES_FILE=/etc/cookies.txt
   ```

5. `docker compose up -d` neu starten.

> Die Cookies werden **nur lokal** im Container verwendet und nie nach außen
> versendet. Lege `cookies.txt` nicht in öffentliche Git-Repos!

## Nur Docker

```bash
docker build -t spotify-downloader .
docker run -d \
  --name spotify-downloader \
  -p 8000:8000 \
  -v "$(pwd)/downloads:/data/downloads" \
  spotify-downloader
```

Oder das in Gitea gebaute Image direkt ziehen:

```bash
docker pull git.scruzzi.com/mattia/spotify-downloader:latest
```

## Lokale Entwicklung ohne Docker

Voraussetzungen: Python 3.11+ und `ffmpeg` im `PATH`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Konfiguration (Umgebungsvariablen)

| Variable                 | Default             | Beschreibung                              |
|--------------------------|---------------------|-------------------------------------------|
| `PORT`                   | `8000`              | HTTP-Port                                 |
| `DOWNLOAD_DIR`           | `/data/downloads`   | Zielverzeichnis für Downloads (Container) |
| `AUDIO_FORMAT`           | `mp3`               | `mp3`, `m4a`, `opus`, `ogg`, `flac`, `wav`|
| `LOG_LEVEL`              | `INFO`              | Log-Level des FastAPI-Prozesses           |
| `SPOTIFY_CLIENT_ID`      | –                   | Eigene Spotify-App Client-ID              |
| `SPOTIFY_CLIENT_SECRET`  | –                   | Passend zum Client-ID                     |
| `YTDLP_COOKIES_FILE`     | –                   | Pfad zu Netscape-Cookie-Datei (YouTube)   |

## REST-API

| Methode | Pfad                                     | Zweck                                  |
|---------|------------------------------------------|----------------------------------------|
| `GET`   | `/api/health`                            | Health-Check                           |
| `POST`  | `/api/downloads`                         | Neuen Download starten (`{ "url": … }`)|
| `GET`   | `/api/downloads`                         | Alle Jobs auflisten                    |
| `GET`   | `/api/downloads/{id}`                    | Status eines Jobs                      |
| `DELETE`| `/api/downloads/{id}`                    | Job + Dateien löschen                  |
| `GET`   | `/api/downloads/{id}/files/{filename}`   | Einzelne Datei herunterladen           |

Beispiel:

```bash
curl -X POST http://localhost:8000/api/downloads \
  -H "Content-Type: application/json" \
  -d '{"url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"}'
```

## Gitea CI/CD

Der Workflow unter `.gitea/workflows/docker-build.yml` baut bei jedem Push auf
`main` sowie bei Version-Tags (`v*`) automatisch ein Multi-Tag-Image und pusht
es in die Gitea Container Registry.

Benötigte Einstellungen im Gitea-Repo:

1. **Actions aktivieren:** Repository-Settings → *Enable Repository Actions*.
2. **Runner registrieren:** Mindestens ein Gitea Actions Runner mit Docker
   muss gegen das Repo/den Owner registriert sein.
3. **Packages-Berechtigung:** Das Repo-Token (`GITEA_TOKEN`) braucht
   `write:package`-Rechte. Alternativ kann ein eigener Token als Secret
   `REGISTRY_TOKEN` hinterlegt werden.
4. **Registry-Host (optional):** Als Repository-Variable `REGISTRY` kann ein
   abweichender Host gesetzt werden (Default: `git.scruzzi.com`).

Erzeugte Tags:

- `latest` (nur auf `main`)
- `main` (Branch)
- `sha-<kurz-sha>`
- `v1.2.3` (bei passenden Git-Tags)

## Projektstruktur

```
.
├── app/
│   ├── downloader.py      # Async Job-Manager um spotdl
│   ├── main.py            # FastAPI-Routen
│   ├── static/            # CSS + JS
│   └── templates/         # index.html
├── .gitea/workflows/      # CI für Docker-Build & Push
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Lizenz

Nur zum privaten Gebrauch. Kein Support für kommerzielle Nutzung.
