"""FastAPI application exposing the Spotify downloader web UI and API."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .downloader import SUPPORTED_FORMATS, DownloadManager, is_valid_spotify_url

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", BASE_DIR.parent / "downloads"))
AUDIO_FORMAT = os.environ.get("AUDIO_FORMAT", "mp3").lower()
if AUDIO_FORMAT not in SUPPORTED_FORMATS:
    AUDIO_FORMAT = "mp3"
SPOTIFY_CLIENT_ID = (
    os.environ.get("SPOTIFY_CLIENT_ID")
    or os.environ.get("SPOTIPY_CLIENT_ID")
    or ""
).strip()
SPOTIFY_CLIENT_SECRET = (
    os.environ.get("SPOTIFY_CLIENT_SECRET")
    or os.environ.get("SPOTIPY_CLIENT_SECRET")
    or ""
).strip()
YTDLP_COOKIES_FILE = os.environ.get("YTDLP_COOKIES_FILE", "").strip() or None
AUDIO_SOURCES = os.environ.get("AUDIO_SOURCES", "soundcloud").strip()

app = FastAPI(
    title="Spotify Downloader",
    description="Webbasierter Downloader für Spotify Songs, Alben und Playlists.",
    version="1.0.0",
)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

manager = DownloadManager(
    base_dir=DOWNLOAD_DIR,
    default_audio_format=AUDIO_FORMAT,
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    cookies_file=YTDLP_COOKIES_FILE,
    audio_sources=AUDIO_SOURCES,
)


class DownloadRequest(BaseModel):
    url: str = Field(..., min_length=10, description="Spotify Track-, Album-, Artist- oder Playlist-URL")
    audio_format: str | None = Field(
        default=None,
        description="Zielformat: " + ", ".join(SUPPORTED_FORMATS),
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "audio_format": AUDIO_FORMAT,
            "supported_formats": list(SUPPORTED_FORMATS),
            "has_credentials": manager.has_custom_credentials,
            "audio_sources_label": ", ".join(manager.audio_sources),
        },
    )


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/config")
async def config() -> dict:
    return {
        "audio_format": AUDIO_FORMAT,
        "supported_formats": list(SUPPORTED_FORMATS),
        "has_credentials": manager.has_custom_credentials,
        "audio_sources": manager.audio_sources,
    }


@app.post("/api/downloads", status_code=201)
async def create_download(payload: DownloadRequest) -> JSONResponse:
    if not is_valid_spotify_url(payload.url):
        raise HTTPException(
            status_code=400,
            detail="Ungültige Spotify-URL. Erlaubt sind Track-, Album-, Artist- und Playlist-Links.",
        )
    fmt = (payload.audio_format or AUDIO_FORMAT).lower()
    if fmt not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Format '{fmt}' wird nicht unterstützt. Erlaubt: "
            + ", ".join(SUPPORTED_FORMATS),
        )
    try:
        job = await manager.create_job(payload.url, audio_format=fmt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(job.to_dict(), status_code=201)


@app.get("/api/downloads")
async def list_downloads() -> dict:
    return {"jobs": [j.to_dict() for j in manager.list_jobs()]}


@app.get("/api/downloads/{job_id}")
async def get_download(job_id: str) -> dict:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    return job.to_dict()


@app.delete("/api/downloads/{job_id}", status_code=204, response_class=Response)
async def delete_download(job_id: str) -> Response:
    removed = await manager.delete_job(job_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    return Response(status_code=204)


@app.get("/api/downloads/{job_id}/files/{filename}")
async def download_file(job_id: str, filename: str) -> FileResponse:
    path = manager.get_job_file(job_id, filename)
    if not path:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/octet-stream",
    )
