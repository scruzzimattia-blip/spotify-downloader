# Spotify Downloader

Web-basierter Downloader für Spotify-Tracks, -Alben, -Artists und -Playlists.
Serviert über FastAPI mit einer modernen, dunklen Oberfläche; Audio kommt per
**yt-dlp** (Standard: **SoundCloud**).

> Hinweis: Spotify liefert nur Metadaten. Die Audiodateien stammen von Drittanbietern
> (Standard SoundCloud, optional YouTube). Urheberrecht und Nutzungsbedingungen
> beachten.

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

**Keine `.env` Datei nötig** — `docker-compose.yml` setzt Defaults und leere
optionale Variablen. Im Projektroot:

```bash
docker compose up -d --build
# UI: http://localhost:8000
```

Downloads liegen unter `./downloads` (Ordner wird beim ersten Start angelegt).

Spotify-Keys für echte Downloads (einmalig), z. B. in der Shell:

```bash
export SPOTIFY_CLIENT_ID=…
export SPOTIFY_CLIENT_SECRET=…
docker compose up -d
```

Oder dauerhaft in `docker-compose.yml` unter `environment:` eintragen
(nicht committen). Optional: `.env.example` nach `.env` kopieren — reine
Bequemlichkeit, Compose lädt `.env` automatisch **nur wenn die Datei existiert**.

> **Wichtig:** Ohne Spotify-API-Credentials startet die App, aber Metadaten-
> Abruf/Downloads schlagen fehl — siehe nächster Abschnitt.

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

## Audio-Quelle (Standard: SoundCloud, kein YouTube)

Die App lädt die Audiodateien mit **yt-dlp**. Standard ist **`AUDIO_SOURCES=soundcloud`**
— die Suche läuft über **SoundCloud** (kein YouTube). Dafür ist **yt-dlp ≥ 2026.3**
nötig (ältere Versionen hatten einen kaputten SoundCloud-Extractor).

Nicht jeder Spotify-Titel existiert auf SoundCloud. Optional kannst du YouTube
als **zweite** Quelle aktivieren:

```dotenv
AUDIO_SOURCES=soundcloud,youtube
```

Dann brauchst du meist wieder `YTDLP_COOKIES_FILE` (siehe nächster Abschnitt).

## YouTube Cookies (nur bei AUDIO_SOURCES=…,youtube)

YouTube blockiert zunehmend nicht-authentifizierte Zugriffe mit der Fehlermeldung
*"Sign in to confirm you're not a bot"*. Das ist keine App-eigene Limitierung,
sondern betrifft alle yt-dlp-basierten Tools.

**Nur nötig, wenn du `youtube` in `AUDIO_SOURCES` verwendest.** Dann: YouTube-Cookies
im Netscape-Format exportieren und in den Container mounten.

1. Browser-Extension installieren:
   - Chrome/Edge: *Get cookies.txt LOCALLY*
   - Firefox: *cookies.txt*
2. Auf <https://www.youtube.com> eingeloggt sein → Extension öffnen →
   *Export as Netscape* → als `cookies.txt` ins Projektverzeichnis legen.
3. In `docker-compose.yml` die optionalen Zeilen für `cookies.txt` aktivieren
   und `YTDLP_COOKIES_FILE=/etc/cookies.txt` setzen (Shell, `environment:` in
   der Compose-Datei, oder optional eine `.env`-Datei).

4. `docker compose up -d` neu starten.

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
| `YTDLP_COOKIES_FILE`     | –                   | Netscape-Cookies (nur bei `youtube` in Quellen) |
| `AUDIO_SOURCES`          | `soundcloud`        | z. B. `soundcloud` oder `soundcloud,youtube` |

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
3. **Registry-Login (wichtig):** Zwei Repo-Secrets anlegen:

   | Secret | Inhalt |
   |--------|--------|
   | **`REGISTRY_USERNAME`** | Dein Gitea-Benutzername (exakt, inkl. Groß-/Kleinschreibung) |
   | **`REGISTRY_PASSWORD`** | Personal Access Token mit **`read:package`** und **`write:package`** |

   Token erzeugen: Gitea → Profil → **Settings** → **Applications** →
   **Generate New Token**, passende Scopes wählen, den String nur als
   **`REGISTRY_PASSWORD`** speichern — nicht ins Repo committen.

   Wenn weiterhin `Get "https://…/v2/": unauthorized` erscheint, stimmen
   Benutzername/Token/Registry-Host nicht — nicht der Workflow.

4. **Registry-Host (optional):** Als Repository-Variable `REGISTRY` nur den
   Hostnamen setzen (z. B. `git.scruzzi.com`), **ohne** `https://` und ohne
   Slash am Ende.

5. **Image-Name:** Docker verlangt Kleinbuchstaben. Der Workflow wandelt
   `owner/repo` automatisch in Kleinbuchstaben um (z. B. `mattia/spotify-downloader`).

### Manuell testen (vom Rechner mit Docker)

```bash
echo 'DEIN_PAT' | docker login git.scruzzi.com -u DEIN_GITEA_USER --password-stdin
```

Wenn das lokal scheitert, muss zuerst der Token oder die Server-Konfiguration
(Packages/Registry aktiviert?) gefixt werden — nicht der Workflow.

Der CI-Workflow nutzt dieselben beiden Werte mit `docker login … --password-stdin`.
Ohne **`REGISTRY_USERNAME`** oder **`REGISTRY_PASSWORD`** bricht der Job mit einer
klaren Fehlermeldung ab.

Erzeugte Tags (Beispiel `git.scruzzi.com/mattia/spotify-downloader`):

- `YYYYMMDD-<kurz-sha>` (eindeutige Build-Version)
- vollständiger **`github.sha`** als Tag
- `latest` nur bei Push auf Branch **`main`**

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
