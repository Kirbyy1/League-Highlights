from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.services.live_match_diagnostics import (
    console_debug_enabled,
    set_console_debug_override,
)


LOGGER = logging.getLogger(__name__)
LOCAL_ONLY_UI_BUILD = "V8-CONSOLE-DEBUG-SETTING"
_EXTERNAL_PHRASES = (
    "riot api",
    "api key",
    "rgapi-",
    "developer.riotgames.com",
)


_CONSOLE_DEBUG_SETTING_KEY = "console_debug_enabled"


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
        LOGGER.debug("Could not read user settings", exc_info=True)
        return {}


def _console_debug_setting(config: Any) -> bool:
    return bool(_read_settings(config).get(_CONSOLE_DEBUG_SETTING_KEY, False))


def _write_console_debug_setting(config: Any, enabled: bool) -> None:
    settings_path = getattr(config, "settings_file", None)
    if not settings_path:
        return
    path = Path(settings_path)
    data = _read_settings(config)
    data.pop("riot_api_key", None)
    data[_CONSOLE_DEBUG_SETTING_KEY] = bool(enabled)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        LOGGER.exception("Could not save Console Debug setting")


def _storage_settings_layout(window: Any) -> Any | None:
    pages = getattr(window, "settings_pages", None)
    if pages is None:
        return None

    storage_index = -1
    buttons = list(getattr(window, "settings_tab_buttons", ()) or ())
    for index, button in enumerate(buttons):
        try:
            label = str(button.text() or "").strip().casefold()
        except Exception:
            continue
        if "storage" in label:
            storage_index = index
            break

    if storage_index < 0:
        # Current layout: Recording, Audio, Smart highlights, Storage & app.
        storage_index = 3 if pages.count() > 3 else pages.count() - 1
    if storage_index < 0 or storage_index >= pages.count():
        return None

    page = pages.widget(storage_index)
    if page is None:
        return None
    content = page.widget() if hasattr(page, "widget") else page
    return content.layout() if content is not None else None


def _install_console_debug_setting(window: Any, config: Any) -> None:
    if getattr(window, "_console_debug_setting_installed", False):
        return

    layout = _storage_settings_layout(window)
    if layout is None:
        LOGGER.warning("Storage & app page was not found; Console Debug toggle was not added")
        return

    try:
        from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QVBoxLayout

        section = QFrame()
        section.setObjectName("SettingsSection")
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(20, 18, 20, 18)
        section_layout.setSpacing(11)

        title = QLabel("Diagnostics")
        title.setObjectName("SettingsTitle")
        description = QLabel(
            "Enable a dedicated Live Match trace for troubleshooting loading speed, "
            "empty ranked data, LCU response formats, and per-player analysis."
        )
        description.setObjectName("CardMuted")
        description.setWordWrap(True)

        checkbox = QCheckBox("Enable Console Debug")
        checkbox.setToolTip(
            "Writes detailed Live Match diagnostics only while enabled. "
            "Authentication credentials and tokens are redacted."
        )

        log_path = Path(getattr(config, "log_dir", Path.cwd())) / "live_match_debug.txt"
        detail = QLabel(
            "When enabled, Live Match writes request timings, endpoint responses, "
            "parsed rank/history data, player-stage timings, and errors to:\n"
            f"{log_path}"
        )
        detail.setObjectName("CardMuted")
        detail.setWordWrap(True)

        warning = QLabel(
            "Disabled by default. Turn it off after testing because detailed response "
            "data can make the log file grow during repeated matches."
        )
        warning.setObjectName("CardMuted")
        warning.setWordWrap(True)

        section_layout.addWidget(title)
        section_layout.addWidget(description)
        section_layout.addWidget(checkbox)
        section_layout.addWidget(detail)
        section_layout.addWidget(warning)

        # Storage pages end with a stretch; keep Diagnostics above it.
        insert_index = max(0, layout.count() - 1)
        layout.insertWidget(insert_index, section)

        effective_enabled = bool(_console_debug_setting(config) or console_debug_enabled(config))
        checkbox.setChecked(effective_enabled)
        set_console_debug_override(effective_enabled)

        def on_toggled(enabled: bool) -> None:
            enabled = bool(enabled)
            _write_console_debug_setting(config, enabled)
            set_console_debug_override(enabled)
            try:
                window._show_toast(
                    "CONSOLE DEBUG ENABLED" if enabled else "CONSOLE DEBUG DISABLED",
                    (
                        "Live Match diagnostics will be written to live_match_debug.txt."
                        if enabled
                        else "Live Match diagnostic logging has stopped."
                    ),
                )
            except Exception:
                LOGGER.info("Console Debug changed: %s", enabled)

        checkbox.toggled.connect(on_toggled)
        window.console_debug_checkbox = checkbox
        window._console_debug_setting_installed = True
    except Exception:
        LOGGER.exception("Could not add the Console Debug setting")


def _strip_saved_external_key(config: Any) -> None:
    try:
        config.riot_api_key = ""
    except Exception:
        pass

    settings_path = getattr(config, "settings_file", None)
    if not settings_path:
        return
    path = Path(settings_path)
    try:
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        changed = False
        if "riot_api_key" in data:
            data.pop("riot_api_key", None)
            changed = True
        if changed:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except (OSError, ValueError, TypeError):
        LOGGER.debug("Could not remove obsolete external API setting", exc_info=True)


def prepare_local_only_config(config: Any) -> None:
    """Clear obsolete external credentials without touching local LCU settings."""

    _strip_saved_external_key(config)


def _remove_external_api_copy(root: Any) -> None:
    try:
        from PySide6.QtWidgets import QAbstractButton, QLabel
    except Exception:
        return

    widgets = []
    try:
        widgets.extend(root.findChildren(QLabel))
        widgets.extend(root.findChildren(QAbstractButton))
    except Exception:
        return

    for widget in widgets:
        try:
            text = str(widget.text() or "")
        except Exception:
            continue
        lowered = text.casefold()
        if not any(phrase in lowered for phrase in _EXTERNAL_PHRASES):
            continue

        # Keep the local-client explanation while removing the obsolete external
        # rate-limit sentence from the Recording settings page.
        if "does not consume your riot api rate limit" in lowered:
            widget.setText(
                text.replace(
                    "Queue detection uses the local League Client API and does not consume "
                    "your Riot API rate limit.",
                    "Queue detection uses only the local League Client.",
                )
            )
            continue
        widget.hide()


def install_local_only_api_removal() -> None:
    """Remove external Riot API settings, validation, banners and notifications."""

    try:
        from app.config import AppConfig
    except Exception:
        AppConfig = None

    if AppConfig is not None and not getattr(
        AppConfig, "_local_only_save_patch_installed", False
    ):
        original_save_settings = AppConfig.save_user_settings

        def local_only_save_settings(self: Any) -> None:
            # AppConfig is a slotted dataclass and does not own the patch-only
            # Console Debug field. Preserve it across ordinary settings saves.
            console_debug = _console_debug_setting(self)
            try:
                self.riot_api_key = ""
            except Exception:
                pass
            original_save_settings(self)
            _strip_saved_external_key(self)
            _write_console_debug_setting(self, console_debug)

        AppConfig.save_user_settings = local_only_save_settings
        AppConfig._local_only_save_patch_installed = True

    try:
        from app.ui.main_window import MainWindow
    except Exception:
        MainWindow = None

    if MainWindow is not None and not getattr(
        MainWindow, "_local_only_toast_patch_installed", False
    ):
        original_show_toast = MainWindow._show_toast

        def local_only_show_toast(
            self: Any,
            title: str,
            message: str,
            *args: Any,
            **kwargs: Any,
        ) -> None:
            combined = f"{title} {message}".casefold()
            if any(phrase in combined for phrase in _EXTERNAL_PHRASES):
                return
            original_show_toast(self, title, message, *args, **kwargs)

        MainWindow._show_toast = local_only_show_toast
        MainWindow._local_only_toast_patch_installed = True

    try:
        from app.ui.enhanced_main_window import EnhancedMainWindow
    except Exception:
        EnhancedMainWindow = None

    if EnhancedMainWindow is not None and not getattr(
        EnhancedMainWindow, "_local_only_api_patch_installed", False
    ):
        # Do not add the old API settings tab at all.
        EnhancedMainWindow._add_riot_api_settings = lambda self: None
        EnhancedMainWindow._validate_saved_riot_api_key = lambda self: None
        EnhancedMainWindow._start_riot_api_validation = lambda self, *args, **kwargs: None
        EnhancedMainWindow._on_riot_api_validation_finished = lambda self, *args, **kwargs: None
        EnhancedMainWindow._refresh_riot_api_status = lambda self: None
        EnhancedMainWindow._open_riot_api_settings = lambda self: None
        EnhancedMainWindow._save_riot_api_settings = lambda self: None

        original_init = EnhancedMainWindow.__init__

        def local_only_window_init(self: Any, config: Any, *args: Any, **kwargs: Any) -> None:
            prepare_local_only_config(config)
            original_init(self, config, *args, **kwargs)

            timer = getattr(self, "riot_api_recheck_timer", None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass

            # Compatibility with windows created by revisions that already added
            # the tab before this patch was installed.
            for name in (
                "riot_api_key_input",
                "riot_api_save_button",
                "riot_api_status",
                "api_banner",
            ):
                widget = getattr(self, name, None)
                if widget is not None:
                    try:
                        widget.hide()
                    except Exception:
                        pass
            _remove_external_api_copy(self)

            # Add the actual visible Settings > Storage & app > Diagnostics toggle.
            _install_console_debug_setting(self, config)

        EnhancedMainWindow.__init__ = local_only_window_init
        EnhancedMainWindow._local_only_api_patch_installed = True

    try:
        from app.ui.live_match_page import LiveMatchPage
    except Exception:
        LiveMatchPage = None

    if LiveMatchPage is not None and not getattr(
        LiveMatchPage, "_local_only_api_patch_installed", False
    ):
        original_page_init = LiveMatchPage.__init__
        original_set_status = LiveMatchPage.set_status

        def local_only_page_init(self: Any, *args: Any, **kwargs: Any) -> None:
            original_page_init(self, *args, **kwargs)
            banner = getattr(self, "api_banner", None)
            if banner is not None:
                banner.hide()
            _remove_external_api_copy(self)

        def local_only_sync_banner(self: Any) -> None:
            banner = getattr(self, "api_banner", None)
            if banner is not None:
                banner.hide()

        def local_only_set_status(self: Any, state: str, message: str) -> None:
            # Older asynchronous validation can finish after the update is copied.
            # Ignore those obsolete states so they cannot reopen a popup/banner.
            if str(state) in {"key_missing", "key_invalid", "rate_limited"} and (
                "api" in str(message).casefold() or "riot" in str(message).casefold()
            ):
                return
            original_set_status(self, state, message)
            banner = getattr(self, "api_banner", None)
            if banner is not None:
                banner.hide()

        LiveMatchPage.__init__ = local_only_page_init
        LiveMatchPage._sync_api_banner = local_only_sync_banner
        LiveMatchPage.set_status = local_only_set_status
        LiveMatchPage._local_only_api_patch_installed = True

    LOGGER.info("Local-only UI patch %s enabled", LOCAL_ONLY_UI_BUILD)
