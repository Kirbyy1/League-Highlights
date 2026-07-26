from __future__ import annotations

import base64
import errno
import json
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


LCU_FAILURE_DIAGNOSTICS_BUILD = "V13-LCU-ROOT-CAUSE-PROBES"


_WINDOWS_CONNECTION_REFUSED = {10061}
_WINDOWS_CONNECTION_RESET = {10053, 10054}
_WINDOWS_TIMEOUT = {10060}
_POSIX_CONNECTION_REFUSED = {errno.ECONNREFUSED}
_POSIX_CONNECTION_RESET = {errno.ECONNABORTED, errno.ECONNRESET}
_POSIX_TIMEOUT = {errno.ETIMEDOUT}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _exception_node(exc: BaseException) -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": type(exc).__name__,
        "module": type(exc).__module__,
        "message": str(exc),
    }
    errno_value = _int_or_none(getattr(exc, "errno", None))
    winerror_value = _int_or_none(getattr(exc, "winerror", None))
    status = _int_or_none(getattr(exc, "code", None))
    if errno_value is not None:
        node["errno"] = errno_value
    if winerror_value is not None:
        node["winerror"] = winerror_value
    if status is not None:
        node["http_status"] = status
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", None)
        if reason is not None and not isinstance(reason, BaseException):
            node["reason"] = str(reason)
    return node


def exception_chain(exc: BaseException | None, max_depth: int = 8) -> list[dict[str, Any]]:
    """Return the wrapper/cause chain without losing the low-level socket error."""
    if exc is None:
        return []

    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and len(result) < max(1, int(max_depth)):
        object_id = id(current)
        if object_id in seen:
            break
        seen.add(object_id)
        result.append(_exception_node(current))

        reason = getattr(current, "reason", None)
        if isinstance(reason, BaseException) and id(reason) not in seen:
            current = reason
            continue
        cause = getattr(current, "__cause__", None)
        if isinstance(cause, BaseException) and id(cause) not in seen:
            current = cause
            continue
        context = getattr(current, "__context__", None)
        if isinstance(context, BaseException) and id(context) not in seen:
            current = context
            continue
        current = None
    return result


def classify_exception(exc: BaseException | None) -> str:
    """Classify a nested urllib/socket exception as precisely as Python allows."""
    if exc is None:
        return "none"

    chain_objects: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and len(chain_objects) < 8:
        if id(current) in seen:
            break
        seen.add(id(current))
        chain_objects.append(current)
        reason = getattr(current, "reason", None)
        if isinstance(reason, BaseException) and id(reason) not in seen:
            current = reason
        elif isinstance(getattr(current, "__cause__", None), BaseException):
            current = current.__cause__
        elif isinstance(getattr(current, "__context__", None), BaseException):
            current = current.__context__
        else:
            current = None

    status_values = {
        _int_or_none(getattr(item, "code", None)) for item in chain_objects
    }
    status_values.discard(None)
    if status_values:
        return f"http_{min(status_values)}"

    for item in chain_objects:
        if isinstance(item, ssl.SSLError):
            return "ssl_error"
        if isinstance(item, socket.gaierror):
            return "name_resolution_error"

    error_numbers: set[int] = set()
    for item in chain_objects:
        for attribute in ("errno", "winerror"):
            value = _int_or_none(getattr(item, attribute, None))
            if value is not None:
                error_numbers.add(value)

    if error_numbers & (_WINDOWS_CONNECTION_REFUSED | _POSIX_CONNECTION_REFUSED):
        return "connection_refused"
    if error_numbers & (_WINDOWS_CONNECTION_RESET | _POSIX_CONNECTION_RESET):
        return "connection_reset"
    if error_numbers & (_WINDOWS_TIMEOUT | _POSIX_TIMEOUT):
        return "socket_timeout"

    for item in chain_objects:
        if isinstance(item, (socket.timeout, TimeoutError)):
            return "socket_timeout"
        if isinstance(item, ConnectionRefusedError):
            return "connection_refused"
        if isinstance(item, ConnectionResetError):
            return "connection_reset"
        if isinstance(item, BrokenPipeError):
            return "broken_pipe"

    if any(isinstance(item, URLError) for item in chain_objects):
        return "url_error"
    if any(isinstance(item, OSError) for item in chain_objects):
        return "os_error"
    if any(isinstance(item, ConnectionError) for item in chain_objects):
        return "connection_error"
    return "unknown"


@dataclass(frozen=True, slots=True)
class RequestActivity:
    request_id: int
    endpoint: str
    category: str
    active_total: int
    active_history: int
    peak_total: int
    peak_history: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "active_requests": self.active_total,
            "active_history_requests": self.active_history,
            "peak_active_requests": self.peak_total,
            "peak_active_history_requests": self.peak_history,
        }


class ActiveRequestTracker:
    """Thread-safe active request counters for proving or rejecting congestion."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._next_id = 0
        self._active_total = 0
        self._active_history = 0
        self._peak_total = 0
        self._peak_history = 0

    @staticmethod
    def category(endpoint: str) -> str:
        return "history" if "match-history" in str(endpoint or "").casefold() else "other"

    def start(self, endpoint: str) -> RequestActivity:
        category = self.category(endpoint)
        with self._lock:
            self._next_id += 1
            self._active_total += 1
            if category == "history":
                self._active_history += 1
            self._peak_total = max(self._peak_total, self._active_total)
            self._peak_history = max(self._peak_history, self._active_history)
            return RequestActivity(
                request_id=self._next_id,
                endpoint=str(endpoint),
                category=category,
                active_total=self._active_total,
                active_history=self._active_history,
                peak_total=self._peak_total,
                peak_history=self._peak_history,
            )

    def finish(self, activity: RequestActivity) -> dict[str, int]:
        with self._lock:
            self._active_total = max(0, self._active_total - 1)
            if activity.category == "history":
                self._active_history = max(0, self._active_history - 1)
            return self.snapshot()

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "active_requests": self._active_total,
                "active_history_requests": self._active_history,
                "peak_active_requests": self._peak_total,
                "peak_active_history_requests": self._peak_history,
            }


def tcp_port_probe(
    port: int,
    *,
    host: str = "127.0.0.1",
    timeout: float = 0.25,
    create_connection: Callable[..., Any] = socket.create_connection,
) -> dict[str, Any]:
    started = time.perf_counter()
    connection = None
    try:
        connection = create_connection((host, int(port)), timeout=float(timeout))
        return {
            "success": True,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
        }
    except BaseException as exc:
        return {
            "success": False,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "failure_class": classify_exception(exc),
            "exception_chain": exception_chain(exc),
        }
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def tls_handshake_probe(
    port: int,
    *,
    host: str = "127.0.0.1",
    timeout: float = 0.45,
    context: ssl.SSLContext | None = None,
    create_connection: Callable[..., Any] = socket.create_connection,
) -> dict[str, Any]:
    """Test TCP + TLS separately from the LCU HTTP endpoint work."""
    started = time.perf_counter()
    raw_socket = None
    tls_socket = None
    try:
        raw_socket = create_connection((host, int(port)), timeout=float(timeout))
        if context is None:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        tls_socket = context.wrap_socket(
            raw_socket,
            server_hostname=host,
            do_handshake_on_connect=False,
        )
        raw_socket = None  # ownership moved to the TLS socket
        tls_socket.settimeout(float(timeout))
        tls_socket.do_handshake()
        return {
            "success": True,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "tls_version": str(tls_socket.version() or ""),
        }
    except BaseException as exc:
        return {
            "success": False,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "failure_class": classify_exception(exc),
            "exception_chain": exception_chain(exc),
        }
    finally:
        for connection in (tls_socket, raw_socket):
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def gameflow_health_probe(
    *,
    port: int,
    password: str,
    protocol: str = "https",
    timeout: float = 0.65,
    context: ssl.SSLContext | None = None,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    """Probe a lightweight endpoint without calling the patched LCU method."""
    started = time.perf_counter()
    token = base64.b64encode(f"riot:{password}".encode("utf-8")).decode("ascii")
    request = Request(
        f"{protocol}://127.0.0.1:{int(port)}/lol-gameflow/v1/gameflow-phase",
        headers={
            "Accept": "application/json",
            "Authorization": f"Basic {token}",
            "User-Agent": "LeagueHighlights/LCUDiagnosticsV13",
            "Connection": "close",
        },
    )
    if context is None:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        with opener(request, timeout=float(timeout), context=context) as response:
            raw = response.read().decode("utf-8")
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = raw
            return {
                "success": True,
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
                "http_status": _int_or_none(getattr(response, "status", None)),
                "phase": str(payload or "") if not isinstance(payload, dict) else "",
            }
    except BaseException as exc:
        return {
            "success": False,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "failure_class": classify_exception(exc),
            "exception_chain": exception_chain(exc),
        }


def diagnose_lcu_failure(
    *,
    port: int,
    password: str,
    protocol: str,
    context: ssl.SSLContext | None,
    original_error: BaseException,
    active_state: dict[str, int] | None = None,
    tcp_probe_fn: Callable[..., dict[str, Any]] = tcp_port_probe,
    tls_probe_fn: Callable[..., dict[str, Any]] = tls_handshake_probe,
    health_probe_fn: Callable[..., dict[str, Any]] = gameflow_health_probe,
) -> dict[str, Any]:
    """Run transport and HTTP probes and state the strongest supported verdict."""
    tcp_result = tcp_probe_fn(port)
    tls_result: dict[str, Any]
    health_result: dict[str, Any]
    if tcp_result.get("success"):
        tls_result = tls_probe_fn(port, context=context)
    else:
        tls_result = {
            "success": False,
            "skipped": True,
            "reason": "TCP port was not reachable",
        }

    if tls_result.get("success"):
        health_result = health_probe_fn(
            port=port,
            password=password,
            protocol=protocol,
            context=context,
        )
    else:
        health_result = {
            "success": False,
            "skipped": True,
            "reason": "TLS handshake was not available",
        }

    if not tcp_result.get("success"):
        verdict = "lcu_port_unreachable"
    elif not tls_result.get("success"):
        verdict = "lcu_tls_handshake_failed_or_stalled"
    elif health_result.get("success"):
        verdict = "history_endpoint_specific_or_request_queue"
    else:
        health_failure = str(health_result.get("failure_class", ""))
        if health_failure.startswith("http_"):
            verdict = "lcu_http_alive_but_health_endpoint_failed"
        else:
            verdict = "lcu_http_service_stalled_or_busy"

    return {
        "diagnostics_build": LCU_FAILURE_DIAGNOSTICS_BUILD,
        "original_failure_class": classify_exception(original_error),
        "original_exception_chain": exception_chain(original_error),
        "active_state": dict(active_state or {}),
        "probe_sequence": ["tcp_connect", "tls_handshake", "gameflow_http"],
        "tcp_probe": tcp_result,
        "tls_probe": tls_result,
        "gameflow_health_probe": health_result,
        "verdict": verdict,
        "verdict_note": (
            "The probe distinguishes TCP, TLS and client-wide HTTP failure. "
            "A successful health probe does not prove the history endpoint itself is healthy; "
            "it narrows the failure to history processing or request queueing."
        ),
    }
