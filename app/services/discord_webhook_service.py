from __future__ import annotations

import http.client
import json
import logging
import mimetypes
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

LOGGER = logging.getLogger(__name__)


class DiscordWebhookError(RuntimeError):
    pass


class DiscordWebhookCancelled(DiscordWebhookError):
    pass


@dataclass(slots=True, frozen=True)
class DiscordWebhookInfo:
    name: str
    channel_id: str


@dataclass(slots=True, frozen=True)
class DiscordWebhookUploadResult:
    message_id: str
    channel_id: str


class DiscordWebhookService:
    """Validate and upload one exported MP4 through a Discord incoming webhook."""

    USER_AGENT = "LeagueHighlights/1.0"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[Path, tuple[threading.Event, http.client.HTTPSConnection | None]] = {}

    @staticmethod
    def _validated_url(webhook_url: str, *, wait: bool = False) -> tuple[str, int, str]:
        value = str(webhook_url).strip()
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        allowed = (
            host == "discord.com"
            or host.endswith(".discord.com")
            or host == "discordapp.com"
            or host.endswith(".discordapp.com")
        )
        if parsed.scheme != "https" or not allowed:
            raise DiscordWebhookError("Enter a valid Discord webhook URL.")
        if "/api/webhooks/" not in parsed.path or len(parsed.path.rstrip("/").split("/")) < 5:
            raise DiscordWebhookError("Enter a complete Discord webhook URL, including its token.")
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if wait:
            query["wait"] = "true"
        path = urlunparse(("", "", parsed.path, parsed.params, urlencode(query), parsed.fragment))
        return host, parsed.port or 443, path

    def test_connection(self, webhook_url: str) -> DiscordWebhookInfo:
        host, port, path = self._validated_url(webhook_url)
        connection = http.client.HTTPSConnection(host, port, timeout=15)
        try:
            connection.request("GET", path, headers={"User-Agent": self.USER_AGENT})
            response = connection.getresponse()
            body = response.read().decode("utf-8", errors="replace")
            if response.status == 404:
                raise DiscordWebhookError("Discord could not find this webhook. It may have been deleted.")
            if response.status in {401, 403}:
                raise DiscordWebhookError("Discord rejected this webhook URL.")
            if response.status != 200:
                raise DiscordWebhookError(self._response_error(response.status, body))
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                raise DiscordWebhookError("Discord returned an unreadable response.") from exc
            return DiscordWebhookInfo(
                str(payload.get("name") or "Discord webhook"),
                str(payload.get("channel_id") or ""),
            )
        except OSError as exc:
            raise DiscordWebhookError(f"Could not connect to Discord: {exc}") from exc
        finally:
            connection.close()

    def is_uploading(self, file_path: Path) -> bool:
        key = Path(file_path).resolve()
        with self._lock:
            return key in self._active

    def cancel(self, file_path: Path) -> bool:
        key = Path(file_path).resolve()
        with self._lock:
            active = self._active.get(key)
            if active is None:
                return False
            cancel_event, connection = active
            cancel_event.set()
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        return True

    def cancel_all(self) -> None:
        with self._lock:
            paths = list(self._active)
        for path in paths:
            self.cancel(path)

    def upload(
        self,
        webhook_url: str,
        file_path: Path,
        *,
        content: str = "",
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> DiscordWebhookUploadResult:
        file_path = Path(file_path)
        if not file_path.exists() or file_path.stat().st_size <= 0:
            raise DiscordWebhookError("The exported video could not be found.")
        key = file_path.resolve()
        cancel_event = threading.Event()
        with self._lock:
            if key in self._active:
                raise DiscordWebhookError("This video is already being sent to Discord.")
            self._active[key] = (cancel_event, None)

        try:
            for attempt in range(2):
                try:
                    return self._upload_once(
                        webhook_url,
                        file_path,
                        content=content,
                        cancel_event=cancel_event,
                        progress_callback=progress_callback,
                    )
                except _DiscordRateLimited as limited:
                    if attempt > 0:
                        raise DiscordWebhookError("Discord is rate limiting uploads. Try again shortly.")
                    delay = max(0.1, min(30.0, limited.retry_after))
                    if progress_callback:
                        progress_callback(0, f"Discord asked us to wait {delay:.1f}s…")
                    deadline = time.monotonic() + delay
                    while time.monotonic() < deadline:
                        if cancel_event.wait(timeout=min(0.2, deadline - time.monotonic())):
                            raise DiscordWebhookCancelled("Discord upload cancelled.")
            raise DiscordWebhookError("Discord upload failed.")
        finally:
            with self._lock:
                self._active.pop(key, None)

    def _upload_once(
        self,
        webhook_url: str,
        file_path: Path,
        *,
        content: str,
        cancel_event: threading.Event,
        progress_callback: Callable[[int, str], None] | None,
    ) -> DiscordWebhookUploadResult:
        host, port, path = self._validated_url(webhook_url, wait=True)
        boundary = f"----LeagueHighlights{secrets.token_hex(12)}"
        safe_filename = file_path.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
        mime = mimetypes.guess_type(safe_filename)[0] or "video/mp4"
        payload = json.dumps(
            {
                "content": str(content)[:2000],
                "username": "League Highlights",
                "allowed_mentions": {"parse": []},
                "attachments": [{"id": 0, "filename": safe_filename}],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        preamble = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="payload_json"\r\n'
            "Content-Type: application/json\r\n\r\n"
        ).encode("ascii") + payload + (
            f"\r\n--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files[0]"; filename="{safe_filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
        epilogue = f"\r\n--{boundary}--\r\n".encode("ascii")
        file_size = file_path.stat().st_size
        content_length = len(preamble) + file_size + len(epilogue)
        connection = http.client.HTTPSConnection(host, port, timeout=45)
        key = file_path.resolve()
        with self._lock:
            if key in self._active:
                self._active[key] = (cancel_event, connection)

        try:
            connection.putrequest("POST", path)
            connection.putheader("User-Agent", self.USER_AGENT)
            connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            connection.putheader("Content-Length", str(content_length))
            connection.endheaders()
            if cancel_event.is_set():
                raise DiscordWebhookCancelled("Discord upload cancelled.")
            connection.send(preamble)
            sent = 0
            with file_path.open("rb") as handle:
                while True:
                    if cancel_event.is_set():
                        raise DiscordWebhookCancelled("Discord upload cancelled.")
                    chunk = handle.read(256 * 1024)
                    if not chunk:
                        break
                    connection.send(chunk)
                    sent += len(chunk)
                    if progress_callback:
                        percent = min(98, max(1, int(sent / max(1, file_size) * 100)))
                        progress_callback(percent, "Sending video to Discord…")
            connection.send(epilogue)
            response = connection.getresponse()
            body = response.read().decode("utf-8", errors="replace")
            if response.status == 429:
                retry_after = self._retry_after(response, body)
                raise _DiscordRateLimited(retry_after)
            if response.status == 404:
                raise DiscordWebhookError("The saved Discord webhook no longer exists.")
            if response.status in {401, 403}:
                raise DiscordWebhookError("Discord rejected the saved webhook.")
            if not 200 <= response.status < 300:
                raise DiscordWebhookError(self._response_error(response.status, body))
            try:
                result = json.loads(body) if body else {}
            except json.JSONDecodeError:
                result = {}
            if progress_callback:
                progress_callback(100, "Sent to Discord")
            return DiscordWebhookUploadResult(
                str(result.get("id") or ""),
                str(result.get("channel_id") or ""),
            )
        except DiscordWebhookCancelled:
            raise
        except OSError as exc:
            if cancel_event.is_set():
                raise DiscordWebhookCancelled("Discord upload cancelled.") from exc
            raise DiscordWebhookError(f"The Discord upload was interrupted: {exc}") from exc
        finally:
            connection.close()

    @staticmethod
    def _retry_after(response: http.client.HTTPResponse, body: str) -> float:
        header = response.getheader("Retry-After")
        if header:
            try:
                return float(header)
            except ValueError:
                pass
        try:
            payload = json.loads(body)
            return float(payload.get("retry_after", 1.0))
        except (json.JSONDecodeError, TypeError, ValueError):
            return 1.0

    @staticmethod
    def _response_error(status: int, body: str) -> str:
        try:
            payload = json.loads(body)
            message = str(payload.get("message") or "").strip()
        except json.JSONDecodeError:
            message = body.strip()
        if message:
            return f"Discord returned {status}: {message}"
        return f"Discord returned HTTP {status}."


class _DiscordRateLimited(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__(retry_after)
        self.retry_after = float(retry_after)
