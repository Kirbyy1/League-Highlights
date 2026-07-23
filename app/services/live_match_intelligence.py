from __future__ import annotations

import json
import logging
import re
import ssl
import threading
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


_DATA_DRAGON_VERSIONS = "https://ddragon.leagueoflegends.com/api/versions.json"
_DATA_DRAGON_CDN = "https://ddragon.leagueoflegends.com/cdn"


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or ""))
    return cleaned[:180] or "unknown"


class ChampionCatalog:
    """Thread-safe Data Dragon champion name/id catalog with disk caching."""

    CACHE_SECONDS = 7 * 24 * 60 * 60

    def __init__(self, cache_path: Path) -> None:
        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._loaded = False
        self._by_name: dict[str, int] = {}
        self._by_id: dict[int, str] = {}
        self._version = ""

    def champion_id(self, champion_name: str) -> int | None:
        self._ensure()
        return self._by_name.get(normalize_name(champion_name))

    def champion_name(self, champion_id: int) -> str:
        self._ensure()
        return self._by_id.get(int(champion_id), f"Champion {champion_id}")

    def _ensure(self) -> None:
        with self._lock:
            if self._loaded:
                return
            if self._load_disk():
                self._loaded = True
                return
            try:
                self._download()
            except Exception:
                logging.debug("Could not refresh Data Dragon champion catalog", exc_info=True)
            self._loaded = True

    def _load_disk(self) -> bool:
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            created_at = float(payload.get("created_at", 0) or 0)
            by_name = payload.get("by_name", {})
            by_id = payload.get("by_id", {})
            if not isinstance(by_name, dict) or not isinstance(by_id, dict):
                return False
            self._by_name = {str(k): int(v) for k, v in by_name.items()}
            self._by_id = {int(k): str(v) for k, v in by_id.items()}
            self._version = str(payload.get("version", "") or "")
            return bool(self._by_name) and (time.time() - created_at) < self.CACHE_SECONDS
        except Exception:
            return False

    def _download(self) -> None:
        versions = self._public_json(_DATA_DRAGON_VERSIONS)
        if not isinstance(versions, list) or not versions:
            raise RuntimeError("Data Dragon versions unavailable")
        version = str(versions[0])
        manifest = self._public_json(
            f"{_DATA_DRAGON_CDN}/{version}/data/en_US/champion.json"
        )
        data = manifest.get("data", {}) if isinstance(manifest, dict) else {}
        by_name: dict[str, int] = {}
        by_id: dict[int, str] = {}

        for entry in data.values() if isinstance(data, dict) else []:
            if not isinstance(entry, dict):
                continue
            try:
                champion_id = int(entry.get("key", 0) or 0)
            except (TypeError, ValueError):
                continue
            canonical = str(entry.get("id", "") or "")
            display = str(entry.get("name", "") or canonical)
            if not champion_id:
                continue
            by_id[champion_id] = display
            for candidate in {canonical, display}:
                key = normalize_name(candidate)
                if key:
                    by_name[key] = champion_id

        if not by_name:
            raise RuntimeError("Data Dragon champion catalog was empty")

        self._by_name = by_name
        self._by_id = by_id
        self._version = version
        try:
            self.cache_path.write_text(
                json.dumps(
                    {
                        "created_at": time.time(),
                        "version": version,
                        "by_name": by_name,
                        "by_id": {str(k): v for k, v in by_id.items()},
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    @staticmethod
    def _public_json(url: str) -> Any:
        context = ssl.create_default_context()
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "LeagueHighlights/ChampionCatalog",
            },
        )
        with urlopen(request, timeout=8.0, context=context) as response:
            return json.loads(response.read().decode("utf-8"))


class PlayerProfileDiskCache:
    """Small persistent profile cache so repeated players render immediately."""

    def __init__(self, directory: Path, ttl_seconds: int = 5 * 60) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = int(ttl_seconds)
        self._lock = threading.RLock()

    def load(self, puuid: str, champion: str) -> dict[str, Any] | None:
        path = self._path(puuid, champion)
        with self._lock:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                created_at = float(payload.get("created_at", 0) or 0)
                profile = payload.get("profile")
                if (
                    isinstance(profile, dict)
                    and time.time() - created_at <= self.ttl_seconds
                ):
                    return dict(profile)
            except Exception:
                return None
        return None

    def save(self, puuid: str, champion: str, profile: dict[str, Any]) -> None:
        path = self._path(puuid, champion)
        with self._lock:
            try:
                path.write_text(
                    json.dumps(
                        {"created_at": time.time(), "profile": profile},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass

    def _path(self, puuid: str, champion: str) -> Path:
        return self.directory / (
            safe_filename(puuid) + "__" + safe_filename(normalize_name(champion)) + ".json"
        )


class LocalBaselineStore:
    """Learns role-specific benchmarks from profiles processed by this app."""

    METRICS = (
        "avg_kda",
        "avg_cs_min",
        "avg_kp",
        "avg_team_damage_share",
        "avg_vision_min",
        "avg_deaths",
    )
    MAX_VALUES = 500

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = self._load()

    def percentiles(
        self,
        role: str,
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        role = str(role or "").upper()
        with self._lock:
            bucket = self._data.get("roles", {}).get(role, {})
            result: dict[str, Any] = {}
            for metric in self.METRICS:
                values = [
                    float(value)
                    for value in bucket.get(metric, [])
                    if isinstance(value, (int, float))
                ]
                if len(values) < 30:
                    continue
                value = float(analysis.get(metric, 0) or 0)
                if metric == "avg_deaths":
                    # Lower deaths are better, so invert the percentile.
                    below = sum(1 for item in values if item >= value)
                else:
                    below = sum(1 for item in values if item <= value)
                result[metric] = round((below / len(values)) * 100.0, 1)
                result[f"{metric}_sample"] = len(values)
            return result

    def record(
        self,
        unique_key: str,
        role: str,
        analysis: dict[str, Any],
    ) -> None:
        role = str(role or "").upper()
        if not role or int(analysis.get("sample_games", 0) or 0) < 5:
            return

        with self._lock:
            seen = self._data.setdefault("seen", [])
            if unique_key in seen:
                return
            seen.append(unique_key)
            if len(seen) > 5000:
                del seen[: len(seen) - 5000]

            roles = self._data.setdefault("roles", {})
            bucket = roles.setdefault(role, {})
            for metric in self.METRICS:
                value = analysis.get(metric)
                if not isinstance(value, (int, float)):
                    continue
                values = bucket.setdefault(metric, [])
                values.append(float(value))
                if len(values) > self.MAX_VALUES:
                    del values[: len(values) - self.MAX_VALUES]
            self._save()

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {"roles": {}, "seen": []}
        except Exception:
            return {"roles": {}, "seen": []}

    def _save(self) -> None:
        try:
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        except OSError:
            pass


class EncounterStore:
    """Remembers live teammates/enemies whenever League Highlights is running."""

    MAX_ENTRIES_PER_PLAYER = 30
    MAX_RECORDED_GAMES = 500

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = self._load()

    def lookup(self, player_key: str) -> dict[str, Any]:
        with self._lock:
            record = self._data.get("players", {}).get(player_key, {})
            return dict(record) if isinstance(record, dict) else {}

    def record_game(
        self,
        signature: str,
        players: list[dict[str, Any]],
    ) -> bool:
        now = time.time()
        with self._lock:
            recorded = self._data.setdefault("recorded_games", {})
            previous = float(recorded.get(signature, 0) or 0)
            if previous and now - previous < 6 * 60 * 60:
                return False

            recorded[signature] = now
            if len(recorded) > self.MAX_RECORDED_GAMES:
                oldest = sorted(recorded.items(), key=lambda item: item[1])
                for key, _ in oldest[: len(recorded) - self.MAX_RECORDED_GAMES]:
                    recorded.pop(key, None)

            all_players = self._data.setdefault("players", {})
            for item in players:
                key = str(item.get("key", "") or "")
                if not key:
                    continue
                relation = str(item.get("relation", "") or "")
                record = all_players.setdefault(
                    key,
                    {
                        "riot_id": str(item.get("riot_id", "") or ""),
                        "ally_count": 0,
                        "enemy_count": 0,
                        "last_seen": 0,
                        "entries": [],
                    },
                )
                if relation == "ally":
                    record["ally_count"] = int(record.get("ally_count", 0) or 0) + 1
                elif relation == "enemy":
                    record["enemy_count"] = int(record.get("enemy_count", 0) or 0) + 1
                record["riot_id"] = str(item.get("riot_id", "") or record.get("riot_id", ""))
                record["last_seen"] = now
                entries = record.setdefault("entries", [])
                entries.append(
                    {
                        "timestamp": float(item.get("timestamp", 0) or now),
                        "relation": relation,
                        "champion": str(item.get("champion", "") or ""),
                        "my_champion": str(item.get("my_champion", "") or ""),
                        "won": item.get("won") if isinstance(item.get("won"), bool) else None,
                        "result": str(item.get("result", "") or ""),
                        "my_kda": str(item.get("my_kda", "") or ""),
                        "their_kda": str(item.get("their_kda", "") or ""),
                        "queue_id": int(item.get("queue_id", 0) or 0),
                        "match_id": str(item.get("match_id", "") or ""),
                        "game_signature": str(
                            item.get("game_signature", "") or signature
                        ),
                    }
                )
                if len(entries) > self.MAX_ENTRIES_PER_PLAYER:
                    del entries[: len(entries) - self.MAX_ENTRIES_PER_PLAYER]

            self._save()
            return True

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        return {"players": {}, "recorded_games": {}}

    def _save(self) -> None:
        try:
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        except OSError:
            pass

class RankHistoryStore:
    """Stores official current Solo/Duo ranks so they survive season resets."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = self._load()

    @staticmethod
    def current_season_key() -> str:
        # Riot's public API does not expose a stable public season identifier
        # with League-v4 entries. The calendar year is used as the persistent
        # local snapshot bucket and is labelled as a tracked fallback.
        return str(time.gmtime().tm_year)

    def previous(self, puuid: str) -> dict[str, Any] | None:
        current_key = self.current_season_key()
        with self._lock:
            player = self._data.get("players", {}).get(str(puuid), {})
            seasons = player.get("seasons", {}) if isinstance(player, dict) else {}
            candidates: list[tuple[int, dict[str, Any]]] = []
            for key, payload in seasons.items() if isinstance(seasons, dict) else []:
                try:
                    year = int(key)
                except (TypeError, ValueError):
                    continue
                if str(key) == current_key or not isinstance(payload, dict):
                    continue
                candidates.append((year, payload))
            if not candidates:
                return None
            _, payload = max(candidates, key=lambda item: item[0])
            result = dict(payload)
            result["source"] = "local_snapshot"
            return result

    def record_current(self, puuid: str, ranked: dict[str, Any]) -> None:
        tier = str(ranked.get("tier", "UNRANKED") or "UNRANKED").upper()
        division = str(ranked.get("division", "") or "").upper()
        season_key = self.current_season_key()
        snapshot = {
            "season_key": season_key,
            "rank": str(ranked.get("rank", "Unranked") or "Unranked"),
            "tier": tier,
            "division": division,
            "lp": int(ranked.get("lp", 0) or 0),
            "wins": int(ranked.get("wins", 0) or 0),
            "losses": int(ranked.get("losses", 0) or 0),
            "updated_at": time.time(),
        }
        with self._lock:
            players = self._data.setdefault("players", {})
            player = players.setdefault(str(puuid), {"seasons": {}})
            seasons = player.setdefault("seasons", {})
            seasons[season_key] = snapshot
            self._save()

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        return {"players": {}}

    def _save(self) -> None:
        try:
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        except OSError:
            pass

