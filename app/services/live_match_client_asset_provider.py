
from __future__ import annotations

from pathlib import Path
from typing import Literal

from PySide6.QtCore import QObject, QStandardPaths, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


_COMMUNITY_DRAGON_BASE = "https://raw.communitydragon.org/latest"
_RANK_BASE = (
    f"{_COMMUNITY_DRAGON_BASE}/plugins/rcp-fe-lol-static-assets/"
    "global/default/images/ranked-emblem"
)
_ROLE_BASE = (
    f"{_COMMUNITY_DRAGON_BASE}/plugins/rcp-fe-lol-clash/global/default/"
    "assets/images/position-selector/positions"
)

_SUPPORTED_TIERS = {
    "IRON",
    "BRONZE",
    "SILVER",
    "GOLD",
    "PLATINUM",
    "EMERALD",
    "DIAMOND",
    "MASTER",
    "GRANDMASTER",
    "CHALLENGER",
    "UNRANKED",
}
_ROLE_FILE = {
    "TOP": "top",
    "JUNGLE": "jungle",
    "MIDDLE": "middle",
    "BOTTOM": "bottom",
    "UTILITY": "utility",
}


class LiveMatchClientAssetProvider(QObject):
    """Fetch Riot-client rank crests and position icons from CommunityDragon."""

    rank_icon_ready = Signal(str, object)
    role_icon_ready = Signal(str, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._network = QNetworkAccessManager(self)
        self._memory: dict[tuple[str, str], QPixmap] = {}
        self._inflight: set[tuple[str, str]] = set()

        cache_location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation
        )
        cache_root = (
            Path(cache_location)
            if cache_location
            else Path.home() / ".league_highlights" / "cache"
        )
        self._cache_root = cache_root / "communitydragon" / "live_match"
        self._cache_root.mkdir(parents=True, exist_ok=True)

    def request_rank(self, tier: str) -> None:
        normalized = str(tier or "UNRANKED").upper()
        if normalized not in _SUPPORTED_TIERS:
            normalized = "UNRANKED"

        if normalized == "UNRANKED":
            url = (
                f"{_COMMUNITY_DRAGON_BASE}/plugins/rcp-fe-lol-static-assets/"
                "global/default/images/unranked-emblem.png"
            )
        else:
            url = f"{_RANK_BASE}/emblem-{normalized.casefold()}.png"

        self._request("rank", normalized, url)

    def request_role(self, role: str) -> None:
        normalized = str(role or "").upper()
        filename = _ROLE_FILE.get(normalized)
        if not filename:
            return
        url = f"{_ROLE_BASE}/icon-position-{filename}.png"
        self._request("role", normalized, url)

    def _request(
        self,
        kind: Literal["rank", "role"],
        key: str,
        url: str,
    ) -> None:
        cache_key = (kind, key)
        memory = self._memory.get(cache_key)
        if memory is not None and not memory.isNull():
            self._emit(kind, key, memory)
            return

        disk_path = self._cache_root / kind / f"{key.casefold()}.png"
        if disk_path.exists():
            pixmap = QPixmap(str(disk_path))
            if not pixmap.isNull():
                self._memory[cache_key] = pixmap
                self._emit(kind, key, pixmap)
                return

        if cache_key in self._inflight:
            return

        self._inflight.add(cache_key)
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"Accept", b"image/png")
        request.setRawHeader(b"User-Agent", b"LeagueHighlights/LiveMatchV10")
        reply = self._network.get(request)
        reply.finished.connect(
            lambda current=reply, selected_kind=kind, selected_key=key, target=disk_path:
            self._handle_reply(current, selected_kind, selected_key, target)
        )

    def _handle_reply(
        self,
        reply: QNetworkReply,
        kind: Literal["rank", "role"],
        key: str,
        disk_path: Path,
    ) -> None:
        cache_key = (kind, key)
        self._inflight.discard(cache_key)
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return

            image_bytes = bytes(reply.readAll())
            pixmap = QPixmap()
            if not pixmap.loadFromData(image_bytes, "PNG"):
                return

            try:
                disk_path.parent.mkdir(parents=True, exist_ok=True)
                disk_path.write_bytes(image_bytes)
            except OSError:
                pass

            self._memory[cache_key] = pixmap
            self._emit(kind, key, pixmap)
        finally:
            reply.deleteLater()

    def _emit(
        self,
        kind: Literal["rank", "role"],
        key: str,
        pixmap: QPixmap,
    ) -> None:
        if kind == "rank":
            self.rank_icon_ready.emit(key, pixmap)
        else:
            self.role_icon_ready.emit(key, pixmap)
