from __future__ import annotations

import base64
import json
import logging
import os
import re
import ssl
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class LcuCredentials:
    port: int
    password: str
    protocol: str = "https"
    source: str = "lockfile"


class LeagueClientConnection:
    """Best-effort local League Client API connection.

    The LCU is unsupported by Riot, so discovery is intentionally defensive:
    common lockfile paths are tried first, followed by the running
    LeagueClientUx process command line on Windows.
    """

    DISCOVERY_CACHE_SECONDS = 8.0

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._credentials: LcuCredentials | None = None
        self._last_discovery = 0.0
        self._current_summoner: dict[str, Any] = {}
        self._current_summoner_at = 0.0

    def gameflow_phase(self) -> str:
        payload = self.get_json("/lol-gameflow/v1/gameflow-phase")
        return str(payload or "") if not isinstance(payload, dict) else ""

    def current_summoner(self, max_age_seconds: float = 20.0) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if self._current_summoner and now - self._current_summoner_at <= max_age_seconds:
                return dict(self._current_summoner)
        payload = self.get_json("/lol-summoner/v1/current-summoner")
        if not isinstance(payload, dict):
            return {}
        with self._lock:
            self._current_summoner = dict(payload)
            self._current_summoner_at = now
        return dict(payload)

    def get_json(self, endpoint: str) -> Any:
        credentials = self._get_credentials()
        if credentials is None:
            raise ConnectionError("League Client lockfile was not found")

        token = base64.b64encode(
            f"riot:{credentials.password}".encode("utf-8")
        ).decode("ascii")
        request = Request(
            f"{credentials.protocol}://127.0.0.1:{credentials.port}{endpoint}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Basic {token}",
                "User-Agent": "LeagueHighlights/LiveMatchV15",
            },
        )
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        try:
            with urlopen(request, timeout=1.25, context=context) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            with self._lock:
                self._credentials = None
            raise ConnectionError("League Client API is unavailable") from exc

    def _get_credentials(self) -> LcuCredentials | None:
        with self._lock:
            if self._credentials is not None:
                return self._credentials
            now = time.monotonic()
            if now - self._last_discovery < self.DISCOVERY_CACHE_SECONDS:
                return None
            self._last_discovery = now

        credentials = self._discover_credentials()
        with self._lock:
            self._credentials = credentials
        return credentials

    def _discover_credentials(self) -> LcuCredentials | None:
        for path in self._candidate_lockfiles():
            credentials = self._credentials_from_lockfile(path)
            if credentials is not None:
                return credentials

        command_line = self._league_process_command_line()
        if command_line:
            credentials = self._credentials_from_command_line(command_line)
            if credentials is not None:
                return credentials
        return None

    @staticmethod
    def _credentials_from_lockfile(path: Path) -> LcuCredentials | None:
        try:
            raw = path.read_text(encoding="utf-8").strip()
            parts = raw.split(":")
            if len(parts) < 5:
                return None
            return LcuCredentials(
                port=int(parts[2]),
                password=str(parts[3]),
                protocol=str(parts[4] or "https"),
                source=str(path),
            )
        except (OSError, ValueError, IndexError):
            return None

    @staticmethod
    def _credentials_from_command_line(command_line: str) -> LcuCredentials | None:
        port_match = re.search(r"--app-port(?:=|\s+)(\d+)", command_line)
        token_match = re.search(
            r'--remoting-auth-token(?:=|\s+)(?:"([^"]+)"|(\S+))',
            command_line,
        )
        if not port_match or not token_match:
            return None
        password = token_match.group(1) or token_match.group(2) or ""
        if not password:
            return None
        return LcuCredentials(
            port=int(port_match.group(1)),
            password=password,
            protocol="https",
            source="LeagueClientUx command line",
        )

    @staticmethod
    def _candidate_lockfiles() -> list[Path]:
        candidates: list[Path] = []

        explicit = os.environ.get("LEAGUE_INSTALL_DIR", "").strip()
        if explicit:
            candidates.append(Path(explicit) / "lockfile")

        system_drive = os.environ.get("SystemDrive", "C:")
        candidates.extend(
            [
                Path(system_drive + r"\Riot Games\League of Legends\lockfile"),
                Path(system_drive + r"\Games\Riot Games\League of Legends\lockfile"),
            ]
        )

        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(variable, "").strip()
            if root:
                candidates.append(Path(root) / "Riot Games" / "League of Legends" / "lockfile")

        # Preserve order while removing duplicate paths.
        unique: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = str(path).casefold()
            if key not in seen:
                seen.add(key)
                unique.append(path)
        return unique

    @staticmethod
    def _league_process_command_line() -> str:
        if os.name != "nt":
            return ""
        command = (
            "$p = Get-CimInstance Win32_Process -Filter "
            "\"Name='LeagueClientUx.exe'\" | Select-Object -First 1; "
            "if ($p) { $p.CommandLine }"
        )
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True,
                text=True,
                timeout=2.5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            return completed.stdout.strip()
        except Exception:
            logging.debug("Could not inspect LeagueClientUx command line", exc_info=True)
            return ""
