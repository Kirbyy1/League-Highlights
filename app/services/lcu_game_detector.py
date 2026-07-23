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


LCU_CONNECTION_BUILD = "V22-LCU-LIFECYCLE-IDENTITY"


@dataclass(frozen=True, slots=True)
class LcuCredentials:
    port: int
    password: str
    protocol: str = "https"
    source: str = "lockfile"


@dataclass(frozen=True, slots=True)
class LeagueClientIdentity:
    puuid: str = ""
    riot_id: str = ""
    game_name: str = ""
    tag_line: str = ""
    summoner_name: str = ""
    summoner_id: str = ""
    account_id: str = ""
    level: int = 0
    platform: str = ""
    locale: str = ""
    client_version: str = ""

    @property
    def display_name(self) -> str:
        return self.riot_id or self.summoner_name or self.game_name or "League account"

    @property
    def stable_key(self) -> str:
        return self.puuid or self.riot_id.casefold() or self.summoner_id


class LeagueClientConnection:
    """Best-effort read-only League Client API connection.

    The LCU is unsupported by Riot, so discovery and every optional endpoint are
    defensive. A missing endpoint does not invalidate otherwise-good lockfile
    credentials, and all callers keep a fallback path.
    """

    DISCOVERY_CACHE_SECONDS = 8.0
    IDENTITY_CACHE_SECONDS = 8.0
    REGION_CACHE_SECONDS = 30.0

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._credentials: LcuCredentials | None = None
        self._last_discovery = 0.0
        self._current_summoner: dict[str, Any] = {}
        self._current_summoner_at = 0.0
        self._region_locale: dict[str, Any] = {}
        self._region_locale_at = 0.0
        self._identity = LeagueClientIdentity()
        self._identity_at = 0.0

    def invalidate(self) -> None:
        with self._lock:
            self._credentials = None
            self._last_discovery = 0.0
            self._current_summoner = {}
            self._current_summoner_at = 0.0
            self._region_locale = {}
            self._region_locale_at = 0.0
            self._identity = LeagueClientIdentity()
            self._identity_at = 0.0

    def gameflow_phase(self) -> str:
        payload = self.get_json("/lol-gameflow/v1/gameflow-phase")
        return str(payload or "") if not isinstance(payload, dict) else ""

    def gameflow_session(self) -> dict[str, Any]:
        payload = self.get_json_optional("/lol-gameflow/v1/session", {})
        return dict(payload) if isinstance(payload, dict) else {}

    def champ_select_session(self) -> dict[str, Any]:
        payload = self.get_json_optional("/lol-champ-select/v1/session", {})
        return dict(payload) if isinstance(payload, dict) else {}

    def end_of_game_stats(self) -> dict[str, Any]:
        for endpoint in (
            "/lol-end-of-game/v1/eog-stats-block",
            "/lol-end-of-game/v1/gameclient-eog-stats-block",
        ):
            payload = self.get_json_optional(endpoint, {})
            if isinstance(payload, dict) and payload:
                return dict(payload)
        return {}

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

    def region_locale(self, max_age_seconds: float = REGION_CACHE_SECONDS) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if self._region_locale and now - self._region_locale_at <= max_age_seconds:
                return dict(self._region_locale)
        payload = self.get_json_optional("/riotclient/region-locale", {})
        region = dict(payload) if isinstance(payload, dict) else {}
        with self._lock:
            self._region_locale = region
            self._region_locale_at = now
        return dict(region)

    def current_identity(self, max_age_seconds: float = IDENTITY_CACHE_SECONDS) -> LeagueClientIdentity:
        now = time.monotonic()
        with self._lock:
            if self._identity.stable_key and now - self._identity_at <= max_age_seconds:
                return self._identity

        summoner = self.current_summoner(max_age_seconds=max_age_seconds)
        region = self.region_locale(max_age_seconds=max_age_seconds)
        if not summoner and not region:
            return LeagueClientIdentity()

        game_name = str(
            summoner.get("gameName")
            or summoner.get("riotIdGameName")
            or ""
        ).strip()
        tag_line = str(
            summoner.get("tagLine")
            or summoner.get("riotIdTagLine")
            or ""
        ).strip()
        summoner_name = str(
            summoner.get("displayName")
            or summoner.get("internalName")
            or summoner.get("name")
            or ""
        ).strip()
        riot_id = (
            f"{game_name}#{tag_line}"
            if game_name and tag_line
            else game_name or summoner_name
        )

        platform = str(
            region.get("platformId")
            or region.get("webRegion")
            or region.get("region")
            or ""
        ).strip()
        locale = str(region.get("locale") or "").strip()
        client_version = str(
            region.get("clientVersion")
            or region.get("version")
            or ""
        ).strip()

        identity = LeagueClientIdentity(
            puuid=str(summoner.get("puuid") or "").strip(),
            riot_id=riot_id,
            game_name=game_name,
            tag_line=tag_line,
            summoner_name=summoner_name,
            summoner_id=str(summoner.get("summonerId") or summoner.get("id") or "").strip(),
            account_id=str(summoner.get("accountId") or "").strip(),
            level=self._safe_int(summoner.get("summonerLevel") or summoner.get("level")),
            platform=platform,
            locale=locale,
            client_version=client_version,
        )
        with self._lock:
            self._identity = identity
            self._identity_at = now
        return identity

    def get_json_optional(self, endpoint: str, default: Any = None) -> Any:
        try:
            return self.get_json(endpoint)
        except ConnectionError:
            return default

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
                "User-Agent": "LeagueHighlights/LCUV22",
            },
        )
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        try:
            with urlopen(request, timeout=1.25, context=context) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            # Optional LCU resources commonly return 404 outside their phase. Do
            # not throw away valid credentials for a normal phase-specific miss.
            if int(getattr(exc, "code", 0) or 0) in {401, 403}:
                with self._lock:
                    self._credentials = None
            raise ConnectionError(
                f"League Client API endpoint is unavailable ({getattr(exc, 'code', 'HTTP')})"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
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
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

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
