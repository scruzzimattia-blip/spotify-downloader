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

from .downloader import DownloadManager, is_valid_spotify_url

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", BASE_DIR.parent / "downloads"))
AUDIO_FORMAT = os.environ.get("AUDIO_FORMAT", "mp3")

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

manager = DownloadManager(base_dir=DOWNLOAD_DIR, audio_format=AUDIO_FORMAT)


class DownloadRequest(BaseModel):
    url: str = Field(..., min_length=10, description="Spotify Track-, Album- oder Playlist-URL")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "audio_format": AUDIO_FORMAT},
    )


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/downloads", status_code=201)
async def create_download(payload: DownloadRequest) -> JSONResponse:
    if not is_valid_spotify_url(payload.url):
        raise HTTPException(
            status_code=400,
            detail="Ungültige Spotify-URL. Erlaubt sind Track-, Album-, Artist- und Playlist-Links.",
        )
    job = await manager.create_job(payload.url)
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
