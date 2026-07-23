from __future__ import annotations

from dataclasses import dataclass
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class RiotApiValidationResult:
    valid: bool
    state: str
    message: str
    status_code: int | None = None
    definitive: bool = True


def validate_riot_api_key(
    api_key: str,
    platform: str,
    *,
    timeout: float = 8.0,
) -> RiotApiValidationResult:
    key = str(api_key or "").strip()
    platform_name = str(platform or "euw1").strip().lower()

    if not key:
        return RiotApiValidationResult(False, "missing", "No Riot API key was provided.")
    if not key.startswith("RGAPI-"):
        return RiotApiValidationResult(
            False,
            "invalid",
            "This is not a Riot development API key. Keys must begin with RGAPI-.",
        )

    url = f"https://{platform_name}.api.riotgames.com/lol/status/v4/platform-data"
    request = Request(
        url,
        headers={
            "X-Riot-Token": key,
            "Accept": "application/json",
            "User-Agent": "LeagueHighlights/1.0",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            response.read(256)
            if 200 <= status < 300:
                return RiotApiValidationResult(
                    True,
                    "valid",
                    "API key verified successfully with Riot Games.",
                    status,
                )
    except HTTPError as exc:
        status = int(exc.code)
        if status == 401:
            return RiotApiValidationResult(
                False,
                "invalid",
                "Riot Games rejected this API key. Check that it was copied completely.",
                status,
            )
        if status == 403:
            return RiotApiValidationResult(
                False,
                "expired",
                "This Riot development API key has expired or no longer has access. Generate a new key and save it again.",
                status,
            )
        if status == 429:
            return RiotApiValidationResult(
                True,
                "valid",
                "The key is valid, but its Riot API rate limit is currently exhausted.",
                status,
            )
        if 500 <= status < 600:
            return RiotApiValidationResult(
                False,
                "unavailable",
                "Riot Games is temporarily unavailable, so the key could not be verified. Try again shortly.",
                status,
                definitive=False,
            )
        return RiotApiValidationResult(
            False,
            "invalid",
            f"Riot Games rejected the key with HTTP {status}.",
            status,
        )
    except (URLError, socket.timeout, TimeoutError, OSError):
        return RiotApiValidationResult(
            False,
            "unavailable",
            "The key could not be checked because Riot Games could not be reached. Your existing saved key was not changed.",
            definitive=False,
        )

    return RiotApiValidationResult(
        False,
        "unavailable",
        "Riot Games returned an unexpected response while checking the key.",
        definitive=False,
    )
