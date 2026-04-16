"""Download manager.

Spotify wird ausschliesslich als Metadaten-Quelle verwendet
(via spotipy + eigene Client-Credentials). Der eigentliche Audio-Stream
wird mit yt-dlp aus YouTube bezogen und via ffmpeg ins Zielformat
transkodiert. Anschliessend werden die Spotify-Metadaten mit mutagen
in die Datei getaggt.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

import requests
import spotipy
import yt_dlp
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from spotipy.oauth2 import SpotifyClientCredentials

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS: tuple[str, ...] = ("mp3", "m4a", "opus", "flac", "wav")


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TrackRef:
    title: str
    artist: str
    album: str = ""
    duration_ms: int = 0
    cover_url: Optional[str] = None
    status: str = "pending"  # pending | downloading | done | failed
    filename: Optional[str] = None
    error: Optional[str] = None

    @property
    def query(self) -> str:
        return f"{self.artist} - {self.title}".strip(" -")

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "filename": self.filename,
            "error": self.error,
        }


@dataclass
class DownloadJob:
    id: str
    url: str
    audio_format: str
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    message: str = ""
    log: list[str] = field(default_factory=list)
    tracks: list[TrackRef] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    archive: Optional[str] = None
    output_dir: Optional[Path] = None
    total: int = 0
    done: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "audio_format": self.audio_format,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() + "Z",
            "finished_at": (
                self.finished_at.isoformat() + "Z" if self.finished_at else None
            ),
            "message": self.message,
            "log": self.log[-200:],
            "tracks": [t.to_dict() for t in self.tracks],
            "files": self.files,
            "archive": self.archive,
            "total": self.total,
            "done": self.done,
        }


SPOTIFY_URL_RE = re.compile(
    r"^https?://(open\.)?spotify\.com/(intl-[a-z]{2}/)?"
    r"(track|album|playlist|artist)/([A-Za-z0-9]+)"
)


def parse_spotify_url(url: str) -> Optional[tuple[str, str]]:
    m = SPOTIFY_URL_RE.match(url.strip())
    if not m:
        return None
    return m.group(3), m.group(4)


def is_valid_spotify_url(url: str) -> bool:
    return parse_spotify_url(url) is not None


_INVALID_CHARS_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def sanitize_filename(name: str) -> str:
    cleaned = _INVALID_CHARS_RE.sub("_", name).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:180] if cleaned else "track"


class SpotifyFetcher:
    """Zieht Track-Metadaten aus der offiziellen Spotify-Web-API."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        self.sp = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=client_id,
                client_secret=client_secret,
            ),
            requests_timeout=20,
            retries=3,
        )

    def fetch(self, url: str) -> list[TrackRef]:
        parsed = parse_spotify_url(url)
        if not parsed:
            raise ValueError("Ungültige Spotify-URL")
        kind, sp_id = parsed

        if kind == "track":
            return [self._from_track(self.sp.track(sp_id))]

        if kind == "album":
            album = self.sp.album(sp_id)
            album_name = album.get("name", "")
            album_artists = ", ".join(a["name"] for a in album.get("artists", []))
            cover = self._pick_cover(album.get("images"))
            out: list[TrackRef] = []
            for item in self._paginate(self.sp.album_tracks(sp_id, limit=50)):
                out.append(TrackRef(
                    title=item.get("name", ""),
                    artist=", ".join(a["name"] for a in item.get("artists", []))
                    or album_artists,
                    album=album_name,
                    duration_ms=item.get("duration_ms") or 0,
                    cover_url=cover,
                ))
            return out

        if kind == "playlist":
            out = []
            page = self.sp.playlist_items(sp_id, additional_types=("track",), limit=100)
            for item in self._paginate(page):
                track = item.get("track") if item else None
                if not track or track.get("is_local"):
                    continue
                out.append(self._from_track(track))
            return out

        if kind == "artist":
            top = self.sp.artist_top_tracks(sp_id)
            return [self._from_track(t) for t in top.get("tracks", [])]

        raise ValueError(f"Unbekannter URL-Typ: {kind}")

    def _paginate(self, page):
        while page:
            for item in page.get("items", []):
                yield item
            page = self.sp.next(page) if page.get("next") else None

    @staticmethod
    def _pick_cover(images: Optional[list[dict]]) -> Optional[str]:
        if not images:
            return None
        return max(images, key=lambda i: i.get("width") or 0).get("url")

    @classmethod
    def _from_track(cls, track: dict) -> TrackRef:
        album = track.get("album") or {}
        return TrackRef(
            title=track.get("name", ""),
            artist=", ".join(a["name"] for a in track.get("artists", [])),
            album=album.get("name", ""),
            duration_ms=track.get("duration_ms") or 0,
            cover_url=cls._pick_cover(album.get("images")),
        )


class DownloadManager:
    def __init__(
        self,
        base_dir: Path,
        default_audio_format: str = "mp3",
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        cookies_file: Optional[str] = None,
    ) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.default_audio_format = default_audio_format.lower()
        self.client_id = (client_id or "").strip() or None
        self.client_secret = (client_secret or "").strip() or None
        cookies_path = Path(cookies_file).expanduser() if cookies_file else None
        self.cookies_file = (
            str(cookies_path) if cookies_path and cookies_path.is_file() else None
        )
        self.jobs: dict[str, DownloadJob] = {}
        self._lock = asyncio.Lock()

    @property
    def has_custom_credentials(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def create_job(
        self, url: str, audio_format: Optional[str] = None
    ) -> DownloadJob:
        fmt = (audio_format or self.default_audio_format).lower()
        if fmt not in SUPPORTED_FORMATS:
            raise ValueError(
                f"Format '{fmt}' wird nicht unterstützt. Erlaubt: "
                + ", ".join(SUPPORTED_FORMATS)
            )

        async with self._lock:
            job_id = uuid.uuid4().hex[:12]
            output_dir = self.base_dir / job_id
            output_dir.mkdir(parents=True, exist_ok=True)
            job = DownloadJob(
                id=job_id,
                url=url.strip(),
                audio_format=fmt,
                output_dir=output_dir,
            )
            self.jobs[job_id] = job

        asyncio.create_task(self._run_job(job))
        return job

    def list_jobs(self) -> list[DownloadJob]:
        return sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)

    def get_job(self, job_id: str) -> Optional[DownloadJob]:
        return self.jobs.get(job_id)

    def get_job_file(self, job_id: str, filename: str) -> Optional[Path]:
        job = self.jobs.get(job_id)
        if not job or not job.output_dir:
            return None
        candidate = (job.output_dir / filename).resolve()
        if not str(candidate).startswith(str(job.output_dir.resolve())):
            return None
        if candidate.exists() and candidate.is_file():
            return candidate
        return None

    async def delete_job(self, job_id: str) -> bool:
        async with self._lock:
            job = self.jobs.pop(job_id, None)
        if job and job.output_dir and job.output_dir.exists():
            shutil.rmtree(job.output_dir, ignore_errors=True)
        return job is not None

    # ---- Job-Runner -----------------------------------------------------

    async def _run_job(self, job: DownloadJob) -> None:
        job.status = JobStatus.RUNNING
        job.message = "Metadaten werden abgerufen..."
        logger.info(
            "Job %s: fetching Spotify metadata for %s (format=%s)",
            job.id, job.url, job.audio_format,
        )

        try:
            if not self.has_custom_credentials:
                raise RuntimeError(
                    "Spotify-API-Credentials fehlen. Bitte SPOTIFY_CLIENT_ID und "
                    "SPOTIFY_CLIENT_SECRET setzen."
                )

            fetcher = SpotifyFetcher(self.client_id, self.client_secret)
            try:
                tracks = await asyncio.to_thread(fetcher.fetch, job.url)
            except spotipy.SpotifyException as exc:
                raise RuntimeError(
                    f"Spotify API-Fehler ({exc.http_status}): {exc.msg}"
                ) from exc

            if not tracks:
                raise RuntimeError("Keine Tracks gefunden.")

            job.tracks = tracks
            job.total = len(tracks)
            job.message = f"{job.total} Track(s) gefunden, Download läuft..."
            self._log(job, f"Gefundene Tracks: {job.total}")

            for idx, track in enumerate(tracks, start=1):
                track.status = "downloading"
                job.message = f"[{idx}/{job.total}] {track.query}"
                try:
                    filename = await asyncio.to_thread(
                        self._download_track, track, job
                    )
                    track.filename = filename
                    track.status = "done"
                    job.done += 1
                    self._log(job, f"OK  {idx}/{job.total}: {filename}")
                except Exception as exc:  # noqa: BLE001
                    track.status = "failed"
                    track.error = str(exc)
                    self._log(
                        job,
                        f"ERR {idx}/{job.total}: {track.query} :: {exc}",
                    )

            job.files = self._collect_files(job.output_dir)

            if len(job.files) > 1:
                archive_path = job.output_dir / f"{job.id}.zip"
                self._build_archive(job.output_dir, archive_path)
                job.archive = archive_path.name
                job.files = self._collect_files(job.output_dir)

            succeeded = sum(1 for t in tracks if t.status == "done")
            if succeeded == 0:
                job.status = JobStatus.FAILED
                job.message = "Kein Track konnte heruntergeladen werden."
            elif succeeded < job.total:
                job.status = JobStatus.COMPLETED
                job.message = (
                    f"{succeeded}/{job.total} Track(s) heruntergeladen "
                    "(Rest fehlgeschlagen)."
                )
            else:
                job.status = JobStatus.COMPLETED
                job.message = f"{succeeded} Track(s) heruntergeladen."
        except Exception as exc:  # noqa: BLE001
            logger.exception("Job %s crashed", job.id)
            job.status = JobStatus.FAILED
            job.message = f"Fehler: {exc}"
        finally:
            job.finished_at = datetime.utcnow()

    # ---- Download ------------------------------------------------------

    _BOT_DETECTION_RE = re.compile(
        r"Sign in to confirm you[’']re not a bot", re.IGNORECASE
    )

    # Such-Strategien: (Beschreibung, Such-Präfix, extractor_args).
    # Werden der Reihe nach probiert, bis eine Strategie einen Treffer liefert.
    # Wenn Cookies vorhanden sind, reicht meist schon die erste.
    _SEARCH_STRATEGIES: list[tuple[str, str, dict]] = [
        (
            "YouTube (default)",
            "ytsearch1",
            {},
        ),
        (
            "YouTube (mweb+tv_embedded)",
            "ytsearch1",
            {"youtube": {"player_client": ["mweb", "tv_embedded", "web"]}},
        ),
        (
            "YouTube (android+ios)",
            "ytsearch1",
            {"youtube": {"player_client": ["android", "ios"]}},
        ),
    ]

    def _download_track(self, track: TrackRef, job: DownloadJob) -> str:
        assert job.output_dir is not None
        base = sanitize_filename(f"{track.artist} - {track.title}")
        outtmpl = str(job.output_dir / f"{base}.%(ext)s")

        postprocessors: list[dict] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": job.audio_format,
            "preferredquality": "0" if job.audio_format in ("mp3", "m4a", "opus") else "192",
        }]

        errors: list[str] = []
        bot_detection_hit = False

        for desc, prefix, extractor_args in self._SEARCH_STRATEGIES:
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": outtmpl,
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "ignoreerrors": False,
                "retries": 3,
                "fragment_retries": 3,
                "postprocessors": postprocessors,
                "default_search": prefix,
                "extractor_args": extractor_args,
            }
            if self.cookies_file:
                ydl_opts["cookiefile"] = self.cookies_file

            query = f"{prefix}:{track.artist} {track.title} audio"

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.extract_info(query, download=True)
                target = self._find_output(job.output_dir, base, job.audio_format)
                try:
                    self._write_tags(target, track)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Konnte Tags nicht schreiben für %s", target, exc_info=True
                    )
                if desc != self._SEARCH_STRATEGIES[0][0]:
                    self._log(job, f"  -> via {desc}")
                return target.name
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if self._BOT_DETECTION_RE.search(msg):
                    bot_detection_hit = True
                errors.append(f"[{desc}] {msg.splitlines()[-1]}")
                logger.info(
                    "Strategie '%s' für '%s' fehlgeschlagen: %s",
                    desc, track.query, exc,
                )
                self._cleanup_partials(job.output_dir, base)
                continue

        if bot_detection_hit and not self.cookies_file:
            raise RuntimeError(
                "YouTube verlangt Login-Cookies (Bot-Detection). Lege eine "
                "Netscape-Cookie-Datei an und setze die Env-Var "
                "YTDLP_COOKIES_FILE (siehe README)."
            )
        raise RuntimeError(" | ".join(errors) or "yt-dlp: unbekannter Fehler")

    @staticmethod
    def _find_output(output_dir: Path, base: str, audio_format: str) -> Path:
        target = output_dir / f"{base}.{audio_format}"
        if target.exists():
            return target
        candidates = [
            c for c in sorted(output_dir.glob(f"{base}.*"))
            if c.is_file() and c.suffix != ".part"
        ]
        if candidates:
            return candidates[0]
        raise RuntimeError("Audiodatei nach Download nicht gefunden")

    @staticmethod
    def _cleanup_partials(output_dir: Path, base: str) -> None:
        for p in output_dir.glob(f"{base}.*"):
            if p.suffix in (".part", ".ytdl", ".webm", ".m4a", ".opus", ".mp3", ".flac", ".wav"):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass

    # ---- Tagging -------------------------------------------------------

    @staticmethod
    def _write_tags(path: Path, track: TrackRef) -> None:
        ext = path.suffix.lower()
        cover_bytes: Optional[bytes] = None
        if track.cover_url:
            try:
                resp = requests.get(track.cover_url, timeout=15)
                if resp.ok and resp.headers.get("content-type", "").startswith("image/"):
                    cover_bytes = resp.content
            except requests.RequestException:
                cover_bytes = None

        if ext == ".mp3":
            mp3 = MP3(str(path))
            try:
                mp3.add_tags()
            except Exception:
                pass
            try:
                easy = EasyID3(str(path))
            except ID3NoHeaderError:
                easy = EasyID3()
                easy.save(str(path))
                easy = EasyID3(str(path))
            easy["title"] = track.title
            easy["artist"] = track.artist
            if track.album:
                easy["album"] = track.album
            easy.save(str(path))
            if cover_bytes:
                id3 = ID3(str(path))
                id3.delall("APIC")
                id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_bytes))
                id3.save(str(path))
        elif ext == ".flac":
            audio = FLAC(str(path))
            audio["title"] = track.title
            audio["artist"] = track.artist
            if track.album:
                audio["album"] = track.album
            if cover_bytes:
                audio.clear_pictures()
                pic = Picture()
                pic.data = cover_bytes
                pic.type = 3
                pic.mime = "image/jpeg"
                audio.add_picture(pic)
            audio.save()
        elif ext == ".m4a":
            audio = MP4(str(path))
            audio["\xa9nam"] = track.title
            audio["\xa9ART"] = track.artist
            if track.album:
                audio["\xa9alb"] = track.album
            if cover_bytes:
                audio["covr"] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]
            audio.save()
        elif ext == ".opus":
            audio = OggOpus(str(path))
            audio["title"] = track.title
            audio["artist"] = track.artist
            if track.album:
                audio["album"] = track.album
            audio.save()
        # .wav unterstützt keine Standard-Tags – Dateiname reicht.

    # ---- Helpers -------------------------------------------------------

    @staticmethod
    def _collect_files(output_dir: Optional[Path]) -> list[str]:
        if not output_dir:
            return []
        return sorted(
            p.name
            for p in output_dir.iterdir()
            if p.is_file() and not p.name.startswith(".") and p.suffix != ".part"
        )

    @staticmethod
    def _build_archive(source_dir: Path, archive_path: Path) -> None:
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(source_dir.iterdir()):
                if file.is_file() and file != archive_path:
                    zf.write(file, arcname=file.name)

    @staticmethod
    def _log(job: DownloadJob, line: str) -> None:
        job.log.append(line)
