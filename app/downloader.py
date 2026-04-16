"""Download manager wrapping spotdl for asynchronous job execution."""
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

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class DownloadJob:
    id: str
    url: str
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    message: str = ""
    log: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    archive: Optional[str] = None
    output_dir: Optional[Path] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() + "Z",
            "finished_at": (
                self.finished_at.isoformat() + "Z" if self.finished_at else None
            ),
            "message": self.message,
            "log": self.log[-200:],
            "files": self.files,
            "archive": self.archive,
        }


SPOTIFY_URL_RE = re.compile(
    r"^https?://(open\.)?spotify\.com/(intl-[a-z]{2}/)?(track|album|playlist|artist)/[A-Za-z0-9]+"
)


def is_valid_spotify_url(url: str) -> bool:
    return bool(SPOTIFY_URL_RE.match(url.strip()))


class DownloadManager:
    """Keeps track of download jobs and runs spotdl in isolated subprocesses."""

    def __init__(self, base_dir: Path, audio_format: str = "mp3") -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.audio_format = audio_format
        self.jobs: dict[str, DownloadJob] = {}
        self._lock = asyncio.Lock()

    async def create_job(self, url: str) -> DownloadJob:
        async with self._lock:
            job_id = uuid.uuid4().hex[:12]
            output_dir = self.base_dir / job_id
            output_dir.mkdir(parents=True, exist_ok=True)
            job = DownloadJob(id=job_id, url=url.strip(), output_dir=output_dir)
            self.jobs[job_id] = job

        asyncio.create_task(self._run_job(job))
        return job

    def list_jobs(self) -> list[DownloadJob]:
        return sorted(
            self.jobs.values(), key=lambda j: j.created_at, reverse=True
        )

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

    async def _run_job(self, job: DownloadJob) -> None:
        job.status = JobStatus.RUNNING
        job.message = "Download wird gestartet..."
        logger.info("Starting download job %s for %s", job.id, job.url)

        cmd = [
            "spotdl",
            "download",
            job.url,
            "--output",
            str(job.output_dir),
            "--format",
            self.audio_format,
            "--print-errors",
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(job.output_dir),
            )

            assert process.stdout is not None
            async for raw in process.stdout:
                line = raw.decode(errors="replace").rstrip()
                if line:
                    job.log.append(line)
                    job.message = line

            return_code = await process.wait()

            job.files = sorted(
                p.name
                for p in job.output_dir.iterdir()
                if p.is_file() and not p.name.startswith(".")
            )

            if return_code == 0 and job.files:
                if len(job.files) > 1:
                    archive_path = job.output_dir / f"{job.id}.zip"
                    self._build_archive(job.output_dir, archive_path)
                    job.archive = archive_path.name
                    job.files = sorted(
                        p.name
                        for p in job.output_dir.iterdir()
                        if p.is_file() and not p.name.startswith(".")
                    )
                job.status = JobStatus.COMPLETED
                job.message = f"{len(job.files)} Datei(en) heruntergeladen."
            else:
                job.status = JobStatus.FAILED
                job.message = (
                    "Download fehlgeschlagen (Exit-Code "
                    f"{return_code}). Siehe Log."
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Job %s crashed", job.id)
            job.status = JobStatus.FAILED
            job.message = f"Interner Fehler: {exc}"
        finally:
            job.finished_at = datetime.utcnow()

    @staticmethod
    def _build_archive(source_dir: Path, archive_path: Path) -> None:
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(source_dir.iterdir()):
                if file.is_file() and file != archive_path:
                    zf.write(file, arcname=file.name)
