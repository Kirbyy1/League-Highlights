from __future__ import annotations

import base64
import ctypes
import logging
import os
from ctypes import wintypes
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class WebhookStoreError(RuntimeError):
    pass


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob_from_bytes(value: bytes) -> tuple[_DATA_BLOB, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(value)
    blob = _DATA_BLOB(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def _crypt32_functions():
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _protect_windows(value: bytes) -> bytes:
    input_blob, _input_buffer = _blob_from_bytes(value)
    output_blob = _DATA_BLOB()
    crypt32, kernel32 = _crypt32_functions()
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "League Highlights Discord webhook",
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    ):
        raise WebhookStoreError(
            f"Windows could not protect the Discord connection ({ctypes.get_last_error()})."
        )
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _unprotect_windows(value: bytes) -> bytes:
    input_blob, _input_buffer = _blob_from_bytes(value)
    output_blob = _DATA_BLOB()
    crypt32, kernel32 = _crypt32_functions()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    ):
        raise WebhookStoreError(
            f"Windows could not read the saved Discord connection ({ctypes.get_last_error()})."
        )
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


class DiscordWebhookStore:
    """Store a webhook token encrypted for the current Windows user.

    The non-Windows fallback exists only so the project's unit tests can run in
    development environments. League Highlights itself supports Windows only.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @property
    def configured(self) -> bool:
        return self.path.exists() and self.path.stat().st_size > 0

    def save(self, webhook_url: str) -> None:
        value = str(webhook_url).strip()
        if not value:
            raise ValueError("Enter a Discord webhook URL.")
        encoded = value.encode("utf-8")
        protected = _protect_windows(encoded) if os.name == "nt" else base64.b64encode(encoded)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_bytes(protected)
        os.replace(temporary, self.path)

    def load(self) -> str | None:
        if not self.configured:
            return None
        try:
            protected = self.path.read_bytes()
            decoded = _unprotect_windows(protected) if os.name == "nt" else base64.b64decode(protected)
            value = decoded.decode("utf-8").strip()
            return value or None
        except Exception as exc:
            LOGGER.warning("Could not read the saved Discord connection: %s", exc)
            return None

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
