from __future__ import annotations

import ctypes
import logging
import threading
import time
from collections.abc import Callable, Iterable

LOGGER = logging.getLogger(__name__)

MODIFIER_VKS: dict[str, tuple[int, ...]] = {
    "ctrl": (0x11,),
    "alt": (0x12,),
    "shift": (0x10,),
    "win": (0x5B, 0x5C),
}


class GlobalHotkey:
    """Detect a configurable shortcut globally with low-cost Windows polling."""

    # Forty checks per second still gives a maximum detection delay of only
    # 25 ms, while doing substantially less work than the former 100 Hz loop.
    POLL_SECONDS = 0.025
    DISABLED_POLL_SECONDS = 0.25

    def __init__(
        self,
        callback: Callable[[], None],
        error_callback: Callable[[str], None],
        virtual_key: int,
        modifiers: Iterable[str] = (),
        display_name: str = "F8",
    ) -> None:
        self.callback = callback
        self.error_callback = error_callback
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_trigger = 0.0
        self._settings_lock = threading.Lock()
        self._virtual_key = int(virtual_key)
        self._modifiers = tuple(
            self._normalise_modifiers(modifiers)
        )
        self._display_name = display_name or "F8"
        self._generation = 0
        self._enabled = True
        self.mode = "stopped"

    @staticmethod
    def _normalise_modifiers(
        modifiers: Iterable[str],
    ) -> list[str]:
        order = ("ctrl", "alt", "shift", "win")
        selected = {
            str(item).lower()
            for item in modifiers
        }
        return [
            name
            for name in order
            if name in selected
        ]

    @property
    def display_name(self) -> str:
        with self._settings_lock:
            return self._display_name

    def update(
        self,
        virtual_key: int,
        modifiers: Iterable[str],
        display_name: str,
    ) -> None:
        if virtual_key <= 0:
            raise ValueError(
                "The hotkey must have a valid Windows "
                "virtual-key code."
            )

        with self._settings_lock:
            self._virtual_key = int(virtual_key)
            self._modifiers = tuple(
                self._normalise_modifiers(modifiers)
            )
            self._display_name = (
                display_name.strip()
                or f"VK {virtual_key}"
            )
            self._generation += 1

        LOGGER.info(
            "Global clip hotkey changed to %s",
            self._display_name,
        )

    def set_enabled(self, enabled: bool) -> None:
        with self._settings_lock:
            self._enabled = bool(enabled)
            self._generation += 1

    def start(self) -> None:
        if (
            self._thread
            and self._thread.is_alive()
        ):
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="GlobalClipHotkey",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if (
            self._thread
            and self._thread.is_alive()
        ):
            self._thread.join(timeout=2.0)
        self._thread = None
        self.mode = "stopped"

    def _snapshot(
        self,
    ) -> tuple[
        int,
        tuple[str, ...],
        str,
        int,
        bool,
    ]:
        with self._settings_lock:
            return (
                self._virtual_key,
                self._modifiers,
                self._display_name,
                self._generation,
                self._enabled,
            )

    def _poll_loop(self) -> None:
        try:
            user32 = ctypes.WinDLL(
                "user32",
                use_last_error=True,
            )
            user32.GetAsyncKeyState.argtypes = [
                ctypes.c_int
            ]
            user32.GetAsyncKeyState.restype = (
                ctypes.c_short
            )
        except Exception as exc:
            message = (
                "Could not initialize the global "
                f"shortcut detector: {exc}"
            )
            LOGGER.exception(message)
            self.error_callback(message)
            return

        self.mode = "GetAsyncKeyState polling"
        (
            virtual_key,
            modifiers,
            display_name,
            generation,
            enabled,
        ) = self._snapshot()

        LOGGER.info(
            "Global clip hotkey active: %s with "
            "GetAsyncKeyState polling "
            "(administrator=%s, interval=%sms)",
            display_name,
            self._is_admin(),
            int(self.POLL_SECONDS * 1000),
        )

        was_down = False

        while not self._stop_event.is_set():
            try:
                (
                    current_vk,
                    current_modifiers,
                    current_display,
                    current_generation,
                    current_enabled,
                ) = self._snapshot()

                if current_generation != generation:
                    virtual_key = current_vk
                    modifiers = current_modifiers
                    display_name = current_display
                    generation = current_generation
                    enabled = current_enabled
                    was_down = False

                if not enabled:
                    was_down = False
                    self._stop_event.wait(
                        self.DISABLED_POLL_SECONDS
                    )
                    continue

                main_down = bool(
                    user32.GetAsyncKeyState(
                        virtual_key
                    )
                    & 0x8000
                )
                modifiers_down = all(
                    any(
                        user32.GetAsyncKeyState(vk)
                        & 0x8000
                        for vk in MODIFIER_VKS[name]
                    )
                    for name in modifiers
                )
                shortcut_down = (
                    main_down
                    and modifiers_down
                )

                if shortcut_down and not was_down:
                    LOGGER.info(
                        "Global clip hotkey detected: %s",
                        display_name,
                    )
                    self._trigger()

                was_down = shortcut_down
            except Exception as exc:
                message = (
                    "Global shortcut detector stopped "
                    f"unexpectedly: {exc}"
                )
                LOGGER.exception(message)
                self.error_callback(message)
                return

            self._stop_event.wait(
                self.POLL_SECONDS
            )

    def _trigger(self) -> None:
        now = time.monotonic()
        if now - self._last_trigger < 0.6:
            return
        self._last_trigger = now

        try:
            self.callback()
        except Exception:
            LOGGER.exception(
                "Global hotkey callback failed"
            )

    @staticmethod
    def _is_admin() -> bool:
        try:
            return bool(
                ctypes.windll.shell32.IsUserAnAdmin()
            )
        except Exception:
            return False
