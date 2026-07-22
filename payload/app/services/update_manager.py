from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from app.config import AppConfig
from app.version import APP_VERSION, REPOSITORY_SLUG, UPDATE_MANIFEST_URL


LOGGER = logging.getLogger(__name__)
_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_MAX_MANIFEST_BYTES = 512 * 1024
_MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
_MAX_EXTRACTED_BYTES = 4 * 1024 * 1024 * 1024
_MAX_ARCHIVE_ENTRIES = 20_000


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    version: str
    package_url: str
    sha256: str
    size_bytes: int = 0
    release_notes: tuple[dict[str, Any], ...] = ()


class UpdateManager(QObject):
    """Check, verify, and stage GitHub Release updates without touching the live app."""

    status_changed = Signal(str)
    update_available = Signal(object)
    download_progress = Signal(int, str)
    update_ready = Signal(object)
    no_update = Signal(str)
    error_occurred = Signal(str, bool)

    def __init__(self, config: AppConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.manifest_url = UPDATE_MANIFEST_URL
        self.update_root = (
            Path(os.environ.get("LOCALAPPDATA", Path.home()))
            / "LeagueHighlights"
            / "updates"
        )
        self.download_dir = self.update_root / "downloads"
        self.staging_root = self.update_root / "staging"
        self.helper_dir = self.update_root / "helper"
        self.pending_file = self.update_root / "pending_update.json"
        self.result_file = self.update_root / "update_result.json"
        self._lock = threading.Lock()
        self._worker_running = False
        self._launch_started = False
        self._status_text = (
            "Updates are checked automatically. Verified downloads install after the app exits."
            if self.can_self_update
            else "Automatic updates are available in packaged builds."
        )
        self.update_root.mkdir(parents=True, exist_ok=True)

    @property
    def can_self_update(self) -> bool:
        return bool(getattr(sys, "frozen", False)) and os.name == "nt"

    @property
    def status_text(self) -> str:
        return self._status_text

    @property
    def pending_update(self) -> UpdateInfo | None:
        data = self._read_pending()
        if data is None:
            return None
        try:
            return UpdateInfo(
                version=str(data["version"]),
                package_url=str(data.get("package_url", "")),
                sha256=str(data.get("sha256", "")),
                size_bytes=int(data.get("size_bytes", 0) or 0),
                release_notes=tuple(data.get("release_notes", ()) or ()),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def check_for_updates(self, manual: bool = False) -> None:
        if not self._begin_worker():
            if manual:
                self.error_occurred.emit("An update check is already running.", True)
            return
        thread = threading.Thread(
            target=self._check_worker,
            args=(bool(manual),),
            name="LeagueHighlightsUpdateCheck",
            daemon=True,
        )
        thread.start()

    def launch_pending_update(self, restart: bool = False) -> bool:
        """Launch the external helper. The helper waits until this PID has exited."""

        if self._launch_started:
            return True
        if not self.can_self_update or not self.pending_file.exists():
            return False

        pending = self._read_pending()
        if pending is None:
            return False

        install_dir = Path(str(pending.get("install_dir", "")))
        helper_source = install_dir / "LeagueHighlightsUpdater.exe"
        if not helper_source.is_file():
            LOGGER.error("Updater helper is missing: %s", helper_source)
            return False

        try:
            self.helper_dir.mkdir(parents=True, exist_ok=True)
            helper_copy = self.helper_dir / "LeagueHighlightsUpdater.exe"
            temp_copy = helper_copy.with_suffix(".exe.tmp")
            shutil.copy2(helper_source, temp_copy)
            os.replace(temp_copy, helper_copy)

            command = [
                str(helper_copy),
                "--pending",
                str(self.pending_file),
                "--pid",
                str(os.getpid()),
            ]
            if restart:
                command.append("--restart")

            creation_flags = 0
            if os.name == "nt":
                creation_flags = (
                    getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                )
            subprocess.Popen(
                command,
                cwd=str(self.update_root),
                close_fds=True,
                creationflags=creation_flags,
            )
        except OSError:
            LOGGER.exception("Could not launch the updater helper")
            return False

        self._launch_started = True
        return True

    def _begin_worker(self) -> bool:
        with self._lock:
            if self._worker_running:
                return False
            self._worker_running = True
            return True

    def _finish_worker(self) -> None:
        with self._lock:
            self._worker_running = False

    def _set_status(self, message: str) -> None:
        self._status_text = message
        self.status_changed.emit(message)

    def _check_worker(self, manual: bool) -> None:
        try:
            self._set_status("Checking GitHub Releases for updates…")
            manifest = self._download_json(self.manifest_url)
            info = self._parse_manifest(manifest)
            if _version_tuple(info.version) <= _version_tuple(APP_VERSION):
                message = f"League Highlights {APP_VERSION} is up to date."
                self._set_status(message)
                self.no_update.emit(message)
                return

            self.update_available.emit(info)
            if not self.can_self_update:
                self._set_status(
                    f"Version {info.version} is available. Run a packaged installation to use self-update."
                )
                return

            self._download_and_stage(info)
        except Exception as exc:  # errors are converted into one safe UI message
            LOGGER.exception("Update check failed")
            message = _friendly_error(exc)
            self._set_status(message)
            self.error_occurred.emit(message, manual)
        finally:
            self._finish_worker()

    def _download_json(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": f"LeagueHighlights/{APP_VERSION}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=18) as response:
            raw = response.read(_MAX_MANIFEST_BYTES + 1)
        if len(raw) > _MAX_MANIFEST_BYTES:
            raise ValueError("The update manifest is unexpectedly large.")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("The update manifest is invalid.") from exc
        if not isinstance(data, dict):
            raise ValueError("The update manifest must be a JSON object.")
        return data

    @staticmethod
    def _parse_manifest(data: dict[str, Any]) -> UpdateInfo:
        version = str(data.get("version", "")).strip()
        _version_tuple(version)

        package = data.get("package")
        if not isinstance(package, dict):
            raise ValueError("The update manifest does not contain a package.")
        package_url = str(package.get("url", "")).strip()
        sha256 = str(package.get("sha256", "")).strip().lower()
        size_bytes = int(package.get("size", 0) or 0)

        allowed_prefix = f"https://github.com/{REPOSITORY_SLUG}/releases/download/"
        if not package_url.startswith(allowed_prefix):
            raise ValueError("The update package URL is outside the official GitHub repository.")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("The update package checksum is invalid.")
        if size_bytes <= 0 or size_bytes > _MAX_PACKAGE_BYTES:
            raise ValueError("The update package size is invalid.")

        raw_notes = data.get("release_notes", ())
        notes: tuple[dict[str, Any], ...]
        if isinstance(raw_notes, list):
            notes = tuple(item for item in raw_notes if isinstance(item, dict))
        else:
            notes = ()
        return UpdateInfo(version, package_url, sha256, size_bytes, notes)

    def _download_and_stage(self, info: UpdateInfo) -> None:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        archive = self.download_dir / f"LeagueHighlights-{info.version}.zip"
        part = archive.with_suffix(".zip.part")

        if not archive.is_file() or _sha256_file(archive) != info.sha256:
            archive.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
            self._set_status(f"Downloading League Highlights {info.version}…")
            self._download_package(info, part)
            actual_hash = _sha256_file(part)
            if actual_hash != info.sha256:
                part.unlink(missing_ok=True)
                raise ValueError("The downloaded update failed SHA-256 verification.")
            os.replace(part, archive)
        else:
            self.download_progress.emit(100, "Verified cached update package")

        self._set_status(f"Preparing League Highlights {info.version}…")
        staging = self._extract_package(archive, info.version)
        executable_name = Path(sys.executable).name
        if not (staging / executable_name).is_file():
            raise ValueError(f"The update package does not contain {executable_name}.")
        if not (staging / "LeagueHighlightsUpdater.exe").is_file():
            raise ValueError("The update package does not contain the updater helper.")

        pending = {
            "schema": 1,
            "version": info.version,
            "current_version": APP_VERSION,
            "install_dir": str(Path(sys.executable).resolve().parent),
            "staged_dir": str(staging.resolve()),
            "executable_name": executable_name,
            "package_url": info.package_url,
            "sha256": info.sha256,
            "size_bytes": info.size_bytes,
            "release_notes": list(info.release_notes),
            "created_at": int(time.time()),
        }
        _write_json_atomic(self.pending_file, pending)
        self._set_status(
            f"Version {info.version} is ready. It will install after League Highlights exits."
        )
        self.update_ready.emit(info)

    def _download_package(self, info: UpdateInfo, destination: Path) -> None:
        request = urllib.request.Request(
            info.package_url,
            headers={"User-Agent": f"LeagueHighlights/{APP_VERSION}"},
        )
        received = 0
        with urllib.request.urlopen(request, timeout=45) as response, destination.open("wb") as handle:
            content_length = int(response.headers.get("Content-Length", "0") or 0)
            expected = info.size_bytes or content_length
            if expected > _MAX_PACKAGE_BYTES:
                raise ValueError("The update package is too large.")

            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > _MAX_PACKAGE_BYTES:
                    raise ValueError("The update package exceeded the size limit.")
                handle.write(chunk)
                percent = int(received * 100 / expected) if expected > 0 else 0
                self.download_progress.emit(min(percent, 99), "Downloading verified update")

        if info.size_bytes and received != info.size_bytes:
            raise ValueError("The downloaded update size does not match the release manifest.")
        self.download_progress.emit(100, "Download complete; verifying SHA-256")

    def _extract_package(self, archive: Path, version: str) -> Path:
        self.staging_root.mkdir(parents=True, exist_ok=True)
        final_dir = self.staging_root / version
        temp_dir = self.staging_root / f".{version}.extracting"
        shutil.rmtree(temp_dir, ignore_errors=True)
        shutil.rmtree(final_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(archive, "r") as bundle:
                items = bundle.infolist()
                if len(items) > _MAX_ARCHIVE_ENTRIES:
                    raise ValueError("The update archive contains too many files.")
                extracted_size = sum(max(0, int(item.file_size)) for item in items)
                if extracted_size > _MAX_EXTRACTED_BYTES:
                    raise ValueError("The extracted update would exceed the safety limit.")
                for item in items:
                    relative = Path(item.filename.replace("\\", "/"))
                    if relative.is_absolute() or ".." in relative.parts:
                        raise ValueError("The update archive contains an unsafe path.")
                    destination = (temp_dir / relative).resolve()
                    if temp_dir.resolve() not in destination.parents and destination != temp_dir.resolve():
                        raise ValueError("The update archive contains an unsafe path.")
                    if item.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(item, "r") as source, destination.open("wb") as target:
                        shutil.copyfileobj(source, target, length=1024 * 1024)
            os.replace(temp_dir, final_dir)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        return final_dir

    def _read_pending(self) -> dict[str, Any] | None:
        if not self.pending_file.is_file():
            return None
        try:
            data = json.loads(self.pending_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None


def _version_tuple(version: str) -> tuple[int, int, int]:
    match = _VERSION_PATTERN.fullmatch(str(version).strip())
    if match is None:
        raise ValueError(f"Invalid semantic version: {version!r}")
    return tuple(int(part) for part in match.groups())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return "No published update manifest was found on GitHub Releases."
        return f"GitHub returned HTTP {exc.code} while checking for updates."
    if isinstance(exc, urllib.error.URLError):
        return "Could not reach GitHub to check for updates."
    return str(exc) or "The update check failed."
