from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QStandardPaths, QTimer, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


DATA_DRAGON_VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
DATA_DRAGON_CDN = "https://ddragon.leagueoflegends.com/cdn"
FALLBACK_DATA_DRAGON_VERSION = "16.9.1"


def normalize_spell_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


class SummonerSpellIconProvider(QObject):
    """Asynchronously downloads and caches Data Dragon summoner-spell icons."""

    icon_ready = Signal(str, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._network = QNetworkAccessManager(self)
        self._version = ""
        self._spell_ids: dict[str, str] = {}
        self._pending_names: set[str] = set()
        self._inflight_ids: set[str] = set()
        self._waiters: dict[str, set[str]] = {}
        self._memory_icons: dict[str, QPixmap] = {}

        cache_location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation
        )
        if cache_location:
            self._cache_root = Path(cache_location) / "ddragon_spells"
        else:
            self._cache_root = (
                Path.home() / ".league_highlights" / "cache" / "ddragon_spells"
            )
        self._cache_root.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._cache_root / "summoner_manifest.json"

        self._load_cached_manifest()
        self._request_latest_version()

    def request_icon(self, spell_name: str) -> None:
        request_key = normalize_spell_name(spell_name)
        if not request_key:
            return

        spell_id = self._spell_ids.get(request_key)
        if not spell_id:
            self._pending_names.add(str(spell_name))
            return

        self._request_resolved_icon(request_key, spell_id)

    def _request_latest_version(self) -> None:
        request = QNetworkRequest(QUrl(DATA_DRAGON_VERSIONS_URL))
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"User-Agent", b"LeagueHighlights/DataDragonSpells")
        reply = self._network.get(request)
        reply.finished.connect(lambda current=reply: self._handle_versions(current))

    def _handle_versions(self, reply: QNetworkReply) -> None:
        try:
            if reply.error() == QNetworkReply.NetworkError.NoError:
                payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
                if isinstance(payload, list) and payload:
                    latest = str(payload[0])
                    if latest == self._version and self._spell_ids:
                        self._flush_pending()
                        return
                    self._request_manifest(latest)
                    return
        except Exception:
            pass
        finally:
            reply.deleteLater()

        if not self._version:
            self._request_manifest(FALLBACK_DATA_DRAGON_VERSION)
        else:
            self._flush_pending()

    def _request_manifest(self, version: str) -> None:
        url = f"{DATA_DRAGON_CDN}/{version}/data/en_US/summoner.json"
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"User-Agent", b"LeagueHighlights/DataDragonSpells")
        reply = self._network.get(request)
        reply.finished.connect(
            lambda current=reply, selected_version=version:
            self._handle_manifest(current, selected_version)
        )

    def _handle_manifest(self, reply: QNetworkReply, version: str) -> None:
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return

            payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            mapping: dict[str, str] = {}

            for spell_id, entry in data.items() if isinstance(data, dict) else []:
                if not isinstance(entry, dict):
                    continue
                canonical_id = str(entry.get("id", "") or spell_id)
                display_name = str(entry.get("name", "") or canonical_id)
                for candidate in (
                    canonical_id,
                    display_name,
                    spell_id,
                    str(entry.get("description", "") or ""),
                ):
                    normalized = normalize_spell_name(candidate)
                    if normalized:
                        mapping[normalized] = canonical_id

            # Common Live Client names/aliases.
            alias_map = {
                "flash": "SummonerFlash",
                "smite": "SummonerSmite",
                "heal": "SummonerHeal",
                "barrier": "SummonerBarrier",
                "ignite": "SummonerDot",
                "exhaust": "SummonerExhaust",
                "teleport": "SummonerTeleport",
                "ghost": "SummonerHaste",
                "cleanse": "SummonerBoost",
                "clarity": "SummonerMana",
                "mark": "SummonerSnowball",
                "snowball": "SummonerSnowball",
            }
            for alias, canonical_id in alias_map.items():
                if canonical_id in data:
                    mapping[normalize_spell_name(alias)] = canonical_id

            if not mapping:
                return

            self._version = str(version)
            self._spell_ids = mapping
            self._save_manifest()
            self._flush_pending()
        except Exception:
            pass
        finally:
            reply.deleteLater()

    def _request_resolved_icon(self, request_key: str, spell_id: str) -> None:
        cached = self._memory_icons.get(spell_id)
        if cached is not None and not cached.isNull():
            QTimer.singleShot(
                0,
                lambda key=request_key, pixmap=cached:
                self.icon_ready.emit(key, pixmap),
            )
            return

        self._waiters.setdefault(spell_id, set()).add(request_key)
        icon_path = self._icon_path(spell_id)

        if icon_path.exists():
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                self._memory_icons[spell_id] = pixmap
                self._emit_waiters(spell_id, pixmap)
                return

        if spell_id in self._inflight_ids or not self._version:
            return

        self._inflight_ids.add(spell_id)
        url = f"{DATA_DRAGON_CDN}/{self._version}/img/spell/{spell_id}.png"
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"Accept", b"image/png")
        request.setRawHeader(b"User-Agent", b"LeagueHighlights/DataDragonSpells")
        reply = self._network.get(request)
        reply.finished.connect(
            lambda current=reply, requested_id=spell_id:
            self._handle_icon(current, requested_id)
        )

    def _handle_icon(self, reply: QNetworkReply, spell_id: str) -> None:
        self._inflight_ids.discard(spell_id)
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            image_bytes = bytes(reply.readAll())
            pixmap = QPixmap()
            if not pixmap.loadFromData(image_bytes, "PNG"):
                return

            icon_path = self._icon_path(spell_id)
            icon_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                icon_path.write_bytes(image_bytes)
            except OSError:
                pass

            self._memory_icons[spell_id] = pixmap
            self._emit_waiters(spell_id, pixmap)
        finally:
            reply.deleteLater()

    def _emit_waiters(self, spell_id: str, pixmap: QPixmap) -> None:
        request_keys = self._waiters.pop(spell_id, set())
        for request_key in request_keys:
            self.icon_ready.emit(request_key, pixmap)

    def _flush_pending(self) -> None:
        pending = list(self._pending_names)
        self._pending_names.clear()
        for spell_name in pending:
            self.request_icon(spell_name)

    def _icon_path(self, spell_id: str) -> Path:
        version = self._version or "unknown"
        return self._cache_root / version / f"{spell_id}.png"

    def _load_cached_manifest(self) -> None:
        try:
            payload: Any = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            version = str(payload.get("version", "") or "")
            mapping = payload.get("spell_ids", {})
            if version and isinstance(mapping, dict):
                self._version = version
                self._spell_ids = {
                    str(key): str(value)
                    for key, value in mapping.items()
                    if key and value
                }
        except (OSError, ValueError, TypeError):
            return

    def _save_manifest(self) -> None:
        try:
            self._manifest_path.write_text(
                json.dumps(
                    {
                        "version": self._version,
                        "spell_ids": self._spell_ids,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
