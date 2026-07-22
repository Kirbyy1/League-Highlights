from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


@dataclass(slots=True)
class AppConfig:
    buffer_seconds: int = 45
    segment_seconds: int = 2
    fps: int = 60
    width: int = 1920
    height: int = 1080
    quality: int = 22
    audio_bitrate_kbps: int = 160

    # Audio capture and mix controls. System audio contains everything playing
    # through the selected Windows output device, including Discord voice when
    # Discord uses that same output. Microphone audio is captured separately and
    # mixed only when a clip is exported.
    system_audio_enabled: bool = True
    system_audio_device: str = ""
    system_audio_volume: int = 100
    microphone_enabled: bool = False
    microphone_device: str = ""
    microphone_volume: int = 100

    auto_start: bool = True
    launch_with_windows: bool = False
    start_minimized: bool = False
    close_to_tray: bool = True
    draw_mouse: bool = False

    # Discord export is opt-in and runs only after the user presses Export.
    # The original full-quality highlight is never replaced. The older profile
    # fields remain solely for backwards-compatible settings files.
    discord_mode: bool = False
    discord_target_mb: float = 9.7
    discord_target_mib: float = 9.3
    discord_auto_trim_events: bool = True
    discord_width: int = 1280
    discord_height: int = 720
    discord_fps: int = 30
    discord_audio_bitrate_kbps: int = 56

    # Automatic highlight categories. Each category can be changed from Settings.
    auto_clip_single_kill: bool = True
    auto_clip_double_kill: bool = True
    auto_clip_triple_kill: bool = True
    auto_clip_quadra_kill: bool = True
    auto_clip_pentakill: bool = True
    auto_clip_dragon: bool = True
    auto_clip_baron: bool = True

    # Smart highlight scoring. Selected events become candidates, and the score
    # decides whether routine moments should be kept.
    smart_highlights_enabled: bool = True
    smart_sensitivity: str = "balanced"  # strict, balanced, save_more


    # Windows virtual-key code and optional modifier names used by the global
    # GetAsyncKeyState poller. F8 is the safe default.
    hotkey_vk: int = 0x77
    hotkey_modifiers: list[str] = field(default_factory=list)
    hotkey_display: str = "F8"

    clip_dir: Path = Path.home() / "Videos" / "League Highlights"
    temp_dir: Path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LeagueHighlights" / "buffer"
    log_dir: Path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LeagueHighlights" / "logs"
    settings_file: Path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LeagueHighlights" / "settings.json"
    discord_webhook_file: Path = (
        Path(os.environ.get("LOCALAPPDATA", Path.home()))
        / "LeagueHighlights"
        / "discord_webhook.dat"
    )
    ffmpeg_dir: Path = _bundle_root() / "tools" / "ffmpeg" / "bin"

    @classmethod
    def create_default(cls) -> "AppConfig":
        config = cls()
        config._load_user_settings()
        config.ensure_directories()
        return config

    def ensure_directories(self) -> None:
        self.clip_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)

    def _load_user_settings(self) -> None:
        if not self.settings_file.exists():
            return
        try:
            data = json.loads(self.settings_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        scalar_keys = (
            "buffer_seconds",
            "segment_seconds",
            "fps",
            "width",
            "height",
            "quality",
            "audio_bitrate_kbps",
            "system_audio_enabled",
            "system_audio_device",
            "system_audio_volume",
            "microphone_enabled",
            "microphone_device",
            "microphone_volume",
            "auto_start",
            "launch_with_windows",
            "start_minimized",
            "close_to_tray",
            "draw_mouse",
            "discord_mode",
            "discord_target_mb",
            "discord_target_mib",
            "discord_auto_trim_events",
            "discord_width",
            "discord_height",
            "discord_fps",
            "discord_audio_bitrate_kbps",
            "auto_clip_single_kill",
            "auto_clip_double_kill",
            "auto_clip_triple_kill",
            "auto_clip_quadra_kill",
            "auto_clip_pentakill",
            "auto_clip_dragon",
            "auto_clip_baron",
            "smart_highlights_enabled",
            "smart_sensitivity",
            "hotkey_vk",
            "hotkey_display",
        )
        for key in scalar_keys:
            if key in data:
                setattr(self, key, data[key])

        modifiers = data.get("hotkey_modifiers")
        if isinstance(modifiers, list):
            allowed = {"ctrl", "alt", "shift", "win"}
            self.hotkey_modifiers = [str(item) for item in modifiers if str(item) in allowed]

        if data.get("clip_dir"):
            self.clip_dir = Path(data["clip_dir"])

        # Guard against malformed or old settings files.
        if self.width not in {1280, 1600, 1920}:
            self.width = 1920
        if self.height not in {720, 900, 1080}:
            self.height = 1080
        if (self.width, self.height) not in {(1280, 720), (1600, 900), (1920, 1080)}:
            self.width, self.height = 1920, 1080
        if self.fps not in {30, 60}:
            self.fps = 60
        # v23 uses two-second rolling segments for tighter clip boundaries.
        # Override older settings files that persisted the previous five-second
        # value so existing installations receive the improvement automatically.
        self.segment_seconds = 2
        if not isinstance(self.quality, int) or not 16 <= self.quality <= 35:
            self.quality = 22
        if self.audio_bitrate_kbps not in {96, 128, 160, 192}:
            self.audio_bitrate_kbps = 160
        if not isinstance(self.launch_with_windows, bool):
            self.launch_with_windows = False
        if not isinstance(self.start_minimized, bool):
            self.start_minimized = False
        if not isinstance(self.close_to_tray, bool):
            self.close_to_tray = True
        if not isinstance(self.system_audio_enabled, bool):
            self.system_audio_enabled = True
        if not isinstance(self.microphone_enabled, bool):
            self.microphone_enabled = False
        if not isinstance(self.system_audio_device, str):
            self.system_audio_device = ""
        if not isinstance(self.microphone_device, str):
            self.microphone_device = ""
        if not isinstance(self.system_audio_volume, int) or not 0 <= self.system_audio_volume <= 200:
            self.system_audio_volume = 100
        if not isinstance(self.microphone_volume, int) or not 0 <= self.microphone_volume <= 200:
            self.microphone_volume = 100
        # The obsolete automatic Discord mode stays disabled. v38 uses an explicit
        # Smart Trim export action and always keeps the original recording.
        self.discord_mode = False
        if not isinstance(self.discord_auto_trim_events, bool):
            self.discord_auto_trim_events = True
        try:
            self.discord_target_mb = float(self.discord_target_mb)
        except (TypeError, ValueError):
            self.discord_target_mb = 9.7
        if self.discord_target_mb not in {9.2, 9.5, 9.7}:
            self.discord_target_mb = 9.7
        try:
            self.discord_target_mib = float(self.discord_target_mib)
        except (TypeError, ValueError):
            self.discord_target_mib = 9.3
        if not 1.0 <= self.discord_target_mib <= 100.0:
            self.discord_target_mib = 9.3
        if (self.discord_width, self.discord_height) != (1280, 720):
            self.discord_width, self.discord_height = 1280, 720
        if self.discord_fps != 30:
            self.discord_fps = 30
        if self.discord_audio_bitrate_kbps != 56:
            self.discord_audio_bitrate_kbps = 56
        self.discord_mode = False
        if not isinstance(self.smart_highlights_enabled, bool):
            self.smart_highlights_enabled = True
        if self.smart_sensitivity not in {"strict", "balanced", "save_more"}:
            self.smart_sensitivity = "balanced"
        if not isinstance(self.hotkey_vk, int) or self.hotkey_vk <= 0:
            self.hotkey_vk = 0x77
            self.hotkey_modifiers = []
            self.hotkey_display = "F8"
        if not isinstance(self.hotkey_display, str) or not self.hotkey_display.strip():
            self.hotkey_display = "F8"

    @property
    def discord_target_bytes(self) -> int:
        return int(float(self.discord_target_mib) * 1024 * 1024)

    def save_user_settings(self) -> None:
        data = asdict(self)
        for key, value in list(data.items()):
            if isinstance(value, Path):
                data[key] = str(value)
        self.settings_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
