from __future__ import annotations

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import quote


LOGGER = logging.getLogger(__name__)
STREAMER_NAME_PATCH_BUILD = "V17-OPTIONAL-STREAMER-NAME-RESOLUTION"
_SETTING_KEY = "resolve_streamer_mode_names"
_PLACEHOLDER_PATTERN = re.compile(
    r"^(?:unknown(?:\s+player)?|anonymous|hidden|player|summoner|ally|enemy)(?:\s*#?\s*\d+)?$",
    re.IGNORECASE,
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_-]{12,}$")


def _read_settings(config: Any) -> dict[str, Any]:
    settings_path = getattr(config, "settings_file", None)
    if not settings_path:
        return {}
    path = Path(settings_path)
    try:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return dict(payload) if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        LOGGER.debug("Could not read streamer-name setting", exc_info=True)
        return {}


def streamer_name_resolution_enabled(config: Any) -> bool:
    return bool(_read_settings(config).get(_SETTING_KEY, False))


def _write_setting(config: Any, enabled: bool) -> None:
    settings_path = getattr(config, "settings_file", None)
    if not settings_path:
        return
    path = Path(settings_path)
    data = _read_settings(config)
    data[_SETTING_KEY] = bool(enabled)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        LOGGER.exception("Could not save streamer-name setting")


def _display_name(player: dict[str, Any]) -> str:
    riot_id = str(player.get("riot_id", "") or "").strip()
    game_name = str(player.get("game_name", "") or "").strip()
    tag_line = str(player.get("tag_line", "") or "").strip()
    return riot_id or (f"{game_name}#{tag_line}" if game_name and tag_line else game_name)


def _is_hidden_display_name(scout: Any, player: dict[str, Any]) -> bool:
    display = _display_name(player)
    checker = getattr(scout, "_is_real_display_name", None)
    if callable(checker):
        try:
            return not bool(checker(display))
        except Exception:
            pass
    normalized = display.strip()
    if not normalized:
        return True
    base_name = normalized.rsplit("#", 1)[0].strip()
    return bool(
        _PLACEHOLDER_PATTERN.fullmatch(normalized)
        or _PLACEHOLDER_PATTERN.fullmatch(base_name)
    )


def _identifier_candidates(player: dict[str, Any]) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for field in ("lcu_player_id", "puuid", "summoner_id", "account_id", "player_key"):
        value = str(player.get(field, "") or "").strip()
        folded = value.casefold()
        if not value or folded in seen:
            continue
        if field == "player_key" and not (_IDENTIFIER_PATTERN.fullmatch(value) or value.isdigit()):
            continue
        seen.add(folded)
        candidates.append((field, value))
    return candidates


def _identity_from_payload(payload: Any) -> dict[str, str] | None:
    if not isinstance(payload, dict) or not payload:
        return None

    riot_id = str(payload.get("riotId", "") or "").strip()
    game_name = str(
        payload.get("gameName", "")
        or payload.get("riotIdGameName", "")
        or ""
    ).strip()
    tag_line = str(
        payload.get("tagLine", "")
        or payload.get("riotIdTagLine", "")
        or ""
    ).strip()

    if riot_id and "#" in riot_id:
        parsed_name, parsed_tag = riot_id.rsplit("#", 1)
        game_name = game_name or parsed_name.strip()
        tag_line = tag_line or parsed_tag.strip()

    if not game_name:
        display = str(
            payload.get("displayName", "")
            or payload.get("summonerName", "")
            or payload.get("name", "")
            or ""
        ).strip()
        if "#" in display:
            game_name, display_tag = display.rsplit("#", 1)
            game_name = game_name.strip()
            tag_line = tag_line or display_tag.strip()
        else:
            game_name = display

    candidate_display = riot_id or (
        f"{game_name}#{tag_line}" if game_name and tag_line else game_name
    )
    if (
        not candidate_display
        or _PLACEHOLDER_PATTERN.fullmatch(candidate_display)
        or _PLACEHOLDER_PATTERN.fullmatch(game_name)
    ):
        return None

    return {
        "riot_id": riot_id or (
            f"{game_name}#{tag_line}" if game_name and tag_line else game_name
        ),
        "game_name": game_name,
        "tag_line": tag_line,
    }


def _resolver_endpoints(field: str, identifier: str) -> list[str]:
    encoded = quote(identifier, safe="")
    endpoints: list[str] = []
    if field in {"lcu_player_id", "puuid", "player_key"}:
        endpoints.append(f"/lol-summoner/v2/summoners/puuid/{encoded}")
    if field in {"summoner_id", "account_id"} or identifier.isdigit():
        endpoints.append(f"/lol-summoner/v1/summoners/{encoded}")
    # Some client revisions accept the same local UUID through the older route.
    fallback = f"/lol-summoner/v1/summoners/{encoded}"
    if fallback not in endpoints:
        endpoints.append(fallback)
    return endpoints


def _resolve_one(scout: Any, player: dict[str, Any]) -> dict[str, str] | None:
    for field, identifier in _identifier_candidates(player):
        for endpoint in _resolver_endpoints(field, identifier):
            try:
                payload = scout._lcu.get_json_optional(endpoint, None)
            except Exception:
                LOGGER.debug("Streamer-name LCU lookup failed for %s", endpoint, exc_info=True)
                continue
            identity = _identity_from_payload(payload)
            if identity is not None:
                return identity
    return None


def _remember_original_identity(player: dict[str, Any]) -> None:
    if "_streamer_original_identity" in player:
        return
    player["_streamer_original_identity"] = {
        "riot_id": str(player.get("riot_id", "") or ""),
        "game_name": str(player.get("game_name", "") or ""),
        "tag_line": str(player.get("tag_line", "") or ""),
    }


def _restore_original_identity(player: dict[str, Any]) -> None:
    original = player.get("_streamer_original_identity")
    if isinstance(original, dict):
        player["riot_id"] = str(original.get("riot_id", "") or "")
        player["game_name"] = str(original.get("game_name", "") or "")
        player["tag_line"] = str(original.get("tag_line", "") or "")
    player.pop("streamer_mode_name_resolved", None)


def _apply_identity(player: dict[str, Any], identity: dict[str, str]) -> None:
    _remember_original_identity(player)
    player["riot_id"] = str(identity.get("riot_id", "") or "")
    player["game_name"] = str(identity.get("game_name", "") or "")
    player["tag_line"] = str(identity.get("tag_line", "") or "")
    player["streamer_mode_name_resolved"] = True


def _rebuild_teams(roster: dict[str, Any]) -> None:
    players = [player for player in list(roster.get("players", ()) or ()) if isinstance(player, dict)]
    active_team = str(roster.get("active_team", "") or "").upper()
    if active_team:
        roster["allies"] = [player for player in players if str(player.get("team", "") or "").upper() == active_team]
        roster["enemies"] = [player for player in players if str(player.get("team", "") or "").upper() != active_team]
    else:
        roster["allies"] = [player for player in players if str(player.get("team", "") or "").upper() == "ORDER"]
        roster["enemies"] = [player for player in players if str(player.get("team", "") or "").upper() == "CHAOS"]


def _roster_identity(roster: dict[str, Any]) -> str:
    game_id = str(roster.get("game_id", "") or "")
    players = list(roster.get("players", ()) or ())
    parts: list[str] = []
    for index, player in enumerate(players):
        if not isinstance(player, dict):
            continue
        stable = ""
        candidates = _identifier_candidates(player)
        if candidates:
            stable = candidates[0][1]
        if not stable:
            stable = f"{player.get('team', '')}:{player.get('champion', '')}:{index}"
        parts.append(stable.casefold())
    return game_id + "|" + "|".join(sorted(parts))


def _resolve_roster_names(scout: Any, roster: dict[str, Any]) -> dict[str, Any]:
    players = [player for player in list(roster.get("players", ()) or ()) if isinstance(player, dict)]
    if not players:
        return roster

    match_identity = _roster_identity(roster)
    lock = getattr(scout, "_streamer_name_lock", None)
    if lock is None:
        lock = threading.RLock()
        scout._streamer_name_lock = lock

    with lock:
        previous_identity = str(getattr(scout, "_streamer_name_match_identity", "") or "")
        if match_identity and match_identity != previous_identity:
            scout._streamer_name_cache = {}
            scout._streamer_name_match_identity = match_identity

        enabled = bool(getattr(scout, "_streamer_name_resolution_enabled", False))
        if not enabled:
            for player in players:
                _restore_original_identity(player)
            roster["streamer_name_resolution_enabled"] = False
            roster["streamer_names_resolved"] = 0
            _rebuild_teams(roster)
            return roster

        unresolved: list[tuple[str, dict[str, Any]]] = []
        resolved_count = 0
        cache = getattr(scout, "_streamer_name_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            scout._streamer_name_cache = cache

        for player in players:
            if not _is_hidden_display_name(scout, player):
                if player.get("streamer_mode_name_resolved"):
                    resolved_count += 1
                continue
            candidates = _identifier_candidates(player)
            key = candidates[0][1].casefold() if candidates else ""
            if not key:
                continue
            cached_identity = cache.get(key)
            if isinstance(cached_identity, dict):
                _apply_identity(player, cached_identity)
                resolved_count += 1
            else:
                unresolved.append((key, player))

        if unresolved:
            # Keep this deliberately below the rank gate's concurrency. Name
            # resolution is optional and should not overload the local client.
            with ThreadPoolExecutor(
                max_workers=min(2, len(unresolved)),
                thread_name_prefix="StreamerNameResolver",
            ) as executor:
                futures = {
                    executor.submit(_resolve_one, scout, dict(player)): (key, player)
                    for key, player in unresolved
                }
                for future in as_completed(futures):
                    key, player = futures[future]
                    try:
                        identity = future.result()
                    except Exception:
                        LOGGER.debug("Streamer-name worker failed", exc_info=True)
                        identity = None
                    if identity is None:
                        continue
                    cache[key] = dict(identity)
                    _apply_identity(player, identity)
                    resolved_count += 1

        roster["streamer_name_resolution_enabled"] = True
        roster["streamer_names_resolved"] = resolved_count
        _rebuild_teams(roster)
        return roster


def _storage_settings_layout(window: Any) -> Any | None:
    pages = getattr(window, "settings_pages", None)
    if pages is None:
        return None
    buttons = list(getattr(window, "settings_tab_buttons", ()) or ())
    storage_index = -1
    for index, button in enumerate(buttons):
        try:
            if "storage" in str(button.text() or "").casefold():
                storage_index = index
                break
        except Exception:
            continue
    if storage_index < 0:
        storage_index = 3 if pages.count() > 3 else pages.count() - 1
    if storage_index < 0 or storage_index >= pages.count():
        return None
    page = pages.widget(storage_index)
    content = page.widget() if page is not None and hasattr(page, "widget") else page
    return content.layout() if content is not None else None


def _install_setting_ui(window: Any, config: Any) -> None:
    if getattr(window, "_streamer_name_setting_installed", False):
        return
    layout = _storage_settings_layout(window)
    if layout is None:
        LOGGER.warning("Storage & app page was not found; streamer-name option was not added")
        return

    try:
        from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QVBoxLayout

        section = QFrame()
        section.setObjectName("SettingsSection")
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(20, 18, 20, 18)
        section_layout.setSpacing(11)

        title = QLabel("Live Match privacy")
        title.setObjectName("SettingsTitle")
        description = QLabel(
            "Choose whether League Highlights should resolve Riot IDs that League hides "
            "when streamer mode is active."
        )
        description.setObjectName("CardMuted")
        description.setWordWrap(True)

        checkbox = QCheckBox("Resolve streamer-mode player names")
        checkbox.setToolTip(
            "Uses the local League Client and stable player identifiers to reveal hidden Riot IDs."
        )

        detail = QLabel(
            "Disabled by default. Rank, division, LP and match history continue to use stable "
            "local identifiers. Enabling this option additionally reveals the real Riot ID on "
            "Live Match cards when the local client allows it."
        )
        detail.setObjectName("CardMuted")
        detail.setWordWrap(True)

        warning = QLabel(
            "Privacy notice: resolved names can also appear in Console Debug and in a benchmark "
            "roster captured while this option is enabled. Turn it off to preserve League's "
            "streamer-mode names."
        )
        warning.setObjectName("CardMuted")
        warning.setWordWrap(True)

        section_layout.addWidget(title)
        section_layout.addWidget(description)
        section_layout.addWidget(checkbox)
        section_layout.addWidget(detail)
        section_layout.addWidget(warning)

        insert_index = max(0, layout.count() - 1)
        layout.insertWidget(insert_index, section)

        initial = streamer_name_resolution_enabled(config)
        checkbox.setChecked(initial)
        page = getattr(window, "live_match_page", None)
        scout = getattr(page, "scout", None)
        if scout is not None:
            scout._streamer_name_resolution_enabled = initial

        def on_toggled(enabled: bool) -> None:
            enabled = bool(enabled)
            _write_setting(config, enabled)
            current_page = getattr(window, "live_match_page", None)
            current_scout = getattr(current_page, "scout", None)
            if current_scout is not None:
                setter = getattr(current_scout, "set_streamer_name_resolution", None)
                if callable(setter):
                    setter(enabled, refresh=True)
                else:
                    current_scout._streamer_name_resolution_enabled = enabled
                    current_scout.refresh(force=True)
            try:
                window._show_toast(
                    "STREAMER NAMES ENABLED" if enabled else "STREAMER NAMES DISABLED",
                    (
                        "Hidden Riot IDs will be resolved when the local client provides them."
                        if enabled
                        else "Live Match will preserve League's hidden streamer-mode names."
                    ),
                )
            except Exception:
                LOGGER.info("Streamer-name resolution changed: %s", enabled)

        checkbox.toggled.connect(on_toggled)
        window.streamer_name_resolution_checkbox = checkbox
        window._streamer_name_setting_installed = True
    except Exception:
        LOGGER.exception("Could not add streamer-name setting")


def install_streamer_mode_name_resolution() -> None:
    """Add optional local Riot-ID resolution for League streamer mode."""

    try:
        from app.config import AppConfig
    except Exception:
        AppConfig = None

    if AppConfig is not None and not getattr(
        AppConfig, "_streamer_name_save_patch_installed", False
    ):
        original_save_settings = AppConfig.save_user_settings

        def preserve_streamer_setting(self: Any) -> None:
            enabled = streamer_name_resolution_enabled(self)
            original_save_settings(self)
            _write_setting(self, enabled)

        AppConfig.save_user_settings = preserve_streamer_setting
        AppConfig._streamer_name_save_patch_installed = True

    from app.services.live_match_scout import LiveMatchScout

    if not getattr(LiveMatchScout, "_streamer_name_resolution_patch_installed", False):
        original_init = LiveMatchScout.__init__
        original_discover = LiveMatchScout._discover_roster

        def streamer_scout_init(self: Any, *args: Any, **kwargs: Any) -> None:
            original_init(self, *args, **kwargs)
            self._streamer_name_resolution_enabled = streamer_name_resolution_enabled(self.config)
            self._streamer_name_cache: dict[str, dict[str, str]] = {}
            self._streamer_name_match_identity = ""
            self._streamer_name_lock = threading.RLock()

        def discover_with_optional_names(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
            roster = original_discover(self, *args, **kwargs)
            if not isinstance(roster, dict):
                return roster
            return _resolve_roster_names(self, roster)

        def set_streamer_name_resolution(
            self: Any,
            enabled: bool,
            *,
            refresh: bool = True,
        ) -> None:
            with self._streamer_name_lock:
                self._streamer_name_resolution_enabled = bool(enabled)
                if not enabled:
                    self._streamer_name_cache.clear()
            if refresh:
                self.refresh(force=True)

        LiveMatchScout.__init__ = streamer_scout_init
        LiveMatchScout._discover_roster = discover_with_optional_names
        LiveMatchScout.set_streamer_name_resolution = set_streamer_name_resolution
        LiveMatchScout._streamer_name_resolution_patch_installed = True

    try:
        from app.ui.enhanced_main_window import EnhancedMainWindow
    except Exception:
        EnhancedMainWindow = None

    if EnhancedMainWindow is not None and not getattr(
        EnhancedMainWindow, "_streamer_name_ui_patch_installed", False
    ):
        original_window_init = EnhancedMainWindow.__init__

        def streamer_window_init(self: Any, config: Any, *args: Any, **kwargs: Any) -> None:
            original_window_init(self, config, *args, **kwargs)
            _install_setting_ui(self, config)

        EnhancedMainWindow.__init__ = streamer_window_init
        EnhancedMainWindow._streamer_name_ui_patch_installed = True

    LOGGER.info("Streamer-mode name patch %s enabled", STREAMER_NAME_PATCH_BUILD)
