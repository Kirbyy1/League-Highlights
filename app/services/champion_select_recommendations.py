from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.services.live_match_intelligence import normalize_name


_ROLE_NAMES = {
    "TOP": "Top",
    "JUNGLE": "Jungle",
    "MIDDLE": "Mid",
    "BOTTOM": "AD Carry",
    "UTILITY": "Support",
}

_POSITION_TO_ROLE = {
    "top": "TOP",
    "jungle": "JUNGLE",
    "middle": "MIDDLE",
    "mid": "MIDDLE",
    "bottom": "BOTTOM",
    "bot": "BOTTOM",
    "utility": "UTILITY",
    "support": "UTILITY",
}


@dataclass(frozen=True, slots=True)
class ChampionOption:
    name: str
    roles: tuple[str, ...]
    damage: str
    provides: frozenset[str]
    counters: frozenset[str]


def _option(
    name: str,
    roles: tuple[str, ...],
    damage: str,
    provides: tuple[str, ...],
    counters: tuple[str, ...],
) -> ChampionOption:
    return ChampionOption(name, roles, damage, frozenset(provides), frozenset(counters))


_OPTIONS: tuple[ChampionOption, ...] = (
    _option("Malphite", ("TOP",), "magic", ("frontline", "engage", "armor"), ("physical", "mobile", "immobile_carry")),
    _option("Ornn", ("TOP",), "mixed", ("frontline", "engage", "scaling"), ("frontline", "short_range")),
    _option(
        "Poppy",
        ("TOP", "JUNGLE", "UTILITY"),
        "physical",
        ("frontline", "peel", "anti_dash"),
        ("dash", "dive", "mobile", "engage"),
    ),
    _option("Shen", ("TOP",), "mixed", ("frontline", "peel", "global"), ("dive", "assassin")),
    _option("Gnar", ("TOP",), "physical", ("range", "engage", "kiting"), ("tank", "bruiser", "short_range")),
    _option("Renekton", ("TOP",), "physical", ("lane_pressure", "point_lockdown"), ("assassin", "melee_carry")),
    _option("Jax", ("TOP", "JUNGLE"), "mixed", ("duel", "scaling"), ("melee_carry", "physical", "split_push")),
    _option("Fiora", ("TOP",), "physical", ("duel", "true_damage", "split_push"), ("tank", "frontline", "split_push")),
    _option("Camille", ("TOP",), "physical", ("pick", "dive", "split_push"), ("immobile_carry", "tank")),
    _option("Gwen", ("TOP", "JUNGLE"), "magic", ("tank_buster", "scaling"), ("tank", "frontline", "short_range")),
    _option("Mordekaiser", ("TOP",), "magic", ("isolation", "frontline"), ("frontline", "melee_carry")),
    _option("Kennen", ("TOP",), "magic", ("engage", "teamfight"), ("short_range", "immobile_carry")),
    _option("Gragas", ("TOP", "JUNGLE", "MIDDLE"), "magic", ("disengage", "engage"), ("dive", "engage", "short_range")),
    _option("Quinn", ("TOP",), "physical", ("range", "lane_pressure"), ("immobile", "juggernaut")),
    _option("Teemo", ("TOP",), "magic", ("blind", "lane_pressure"), ("physical", "melee_carry", "juggernaut")),
    _option(
        "Rammus",
        ("JUNGLE",),
        "magic",
        ("frontline", "armor", "point_lockdown"),
        ("physical", "melee_carry", "mobile"),
    ),
    _option("Sejuani", ("JUNGLE",), "magic", ("frontline", "engage", "lockdown"), ("dive", "short_range")),
    _option(
        "Maokai",
        ("JUNGLE", "UTILITY", "TOP"),
        "magic",
        ("frontline", "engage", "peel"),
        ("dive", "engage", "short_range"),
    ),
    _option("Amumu", ("JUNGLE", "UTILITY"), "magic", ("engage", "teamfight"), ("short_range", "immobile_carry")),
    _option("Vi", ("JUNGLE",), "physical", ("pick", "point_lockdown", "dive"), ("mobile", "immobile_carry", "carry")),
    _option("Jarvan IV", ("JUNGLE",), "physical", ("engage", "early_pressure"), ("immobile_carry", "poke")),
    _option("Nocturne", ("JUNGLE",), "physical", ("pick", "dive"), ("immobile_carry", "poke")),
    _option("Lillia", ("JUNGLE", "TOP"), "magic", ("kiting", "teamfight"), ("tank", "frontline", "short_range")),
    _option("Fiddlesticks", ("JUNGLE",), "magic", ("engage", "teamfight"), ("short_range", "immobile_carry")),
    _option("Ivern", ("JUNGLE",), "magic", ("shield", "peel"), ("dive", "assassin")),
    _option("Kindred", ("JUNGLE",), "physical", ("range", "anti_burst", "scaling"), ("dive", "frontline")),
    _option("Graves", ("JUNGLE",), "physical", ("skirmish", "armor"), ("physical", "short_range")),
    _option("Trundle", ("JUNGLE", "TOP"), "physical", ("tank_buster", "duel"), ("tank", "frontline")),
    _option("Zac", ("JUNGLE",), "magic", ("engage", "frontline"), ("immobile_carry", "poke")),
    _option("Nunu & Willump", ("JUNGLE",), "magic", ("objective_control", "frontline"), ("scaling", "slow_setup")),
    _option("Lissandra", ("MIDDLE",), "magic", ("lockdown", "anti_burst", "engage"), ("assassin", "dive", "mobile")),
    _option("Vex", ("MIDDLE",), "magic", ("anti_dash", "burst"), ("dash", "mobile", "assassin")),
    _option("Galio", ("MIDDLE",), "magic", ("anti_magic", "peel", "engage"), ("magic", "assassin", "dive")),
    _option("Malzahar", ("MIDDLE",), "magic", ("point_lockdown", "waveclear"), ("assassin", "mobile", "melee_carry")),
    _option("Taliyah", ("MIDDLE", "JUNGLE"), "magic", ("anti_dash", "zone"), ("dash", "engage", "short_range")),
    _option("Annie", ("MIDDLE",), "magic", ("point_lockdown", "burst"), ("mobile", "assassin", "immobile_carry")),
    _option("Orianna", ("MIDDLE",), "magic", ("teamfight", "shield"), ("frontline", "dive")),
    _option("Syndra", ("MIDDLE",), "magic", ("burst", "pick"), ("immobile", "short_range")),
    _option("Viktor", ("MIDDLE",), "magic", ("zone", "scaling"), ("frontline", "short_range")),
    _option("Cassiopeia", ("MIDDLE",), "magic", ("anti_dash", "sustain_damage"), ("dash", "tank", "short_range")),
    _option("Ahri", ("MIDDLE",), "magic", ("pick", "mobility"), ("immobile", "poke")),
    _option("Swain", ("MIDDLE", "UTILITY"), "magic", ("sustain", "teamfight"), ("short_range", "engage")),
    _option("Veigar", ("MIDDLE",), "magic", ("zone", "scaling", "burst"), ("dash", "short_range")),
    _option("Anivia", ("MIDDLE",), "magic", ("zone", "waveclear"), ("engage", "short_range")),
    _option(
        "Ashe",
        ("BOTTOM", "UTILITY"),
        "physical",
        ("engage", "utility", "range"),
        ("mobile", "short_range", "immobile_carry"),
    ),
    _option(
        "Varus",
        ("BOTTOM", "MIDDLE"),
        "mixed",
        ("poke", "pick", "tank_buster"),
        ("frontline", "short_range", "sustain"),
    ),
    _option("Xayah", ("BOTTOM",), "physical", ("self_peel", "scaling"), ("dive", "engage", "assassin")),
    _option("Kai'Sa", ("BOTTOM",), "mixed", ("dive", "scaling"), ("immobile_carry", "poke")),
    _option("Jhin", ("BOTTOM",), "physical", ("pick", "range"), ("immobile", "short_range")),
    _option("Caitlyn", ("BOTTOM",), "physical", ("range", "lane_pressure"), ("short_range", "immobile")),
    _option("Ezreal", ("BOTTOM",), "physical", ("poke", "safety"), ("engage", "short_range")),
    _option("Sivir", ("BOTTOM",), "physical", ("waveclear", "spell_shield"), ("poke", "pick", "engage")),
    _option("Vayne", ("BOTTOM", "TOP"), "physical", ("tank_buster", "duel", "scaling"), ("tank", "frontline")),
    _option("Kog'Maw", ("BOTTOM",), "mixed", ("tank_buster", "scaling"), ("tank", "frontline")),
    _option("Tristana", ("BOTTOM", "MIDDLE"), "physical", ("self_peel", "scaling"), ("short_range", "immobile")),
    _option("Nilah", ("BOTTOM",), "physical", ("anti_auto", "teamfight"), ("physical", "short_range")),
    _option("Samira", ("BOTTOM",), "physical", ("reset", "dive"), ("poke", "immobile_carry")),
    _option("Miss Fortune", ("BOTTOM",), "physical", ("teamfight", "lane_pressure"), ("short_range", "immobile")),
    _option(
        "Seraphine",
        ("BOTTOM", "UTILITY", "MIDDLE"),
        "magic",
        ("teamfight", "shield", "utility"),
        ("engage", "short_range"),
    ),
    _option("Janna", ("UTILITY",), "magic", ("disengage", "peel", "shield"), ("dive", "engage", "assassin")),
    _option("Milio", ("UTILITY",), "magic", ("peel", "shield", "range"), ("poke", "dive")),
    _option("Lulu", ("UTILITY",), "magic", ("peel", "shield", "anti_burst"), ("assassin", "dive", "melee_carry")),
    _option("Braum", ("UTILITY",), "magic", ("peel", "frontline"), ("engage", "projectile", "dive")),
    _option("Taric", ("UTILITY",), "magic", ("anti_burst", "peel", "frontline"), ("dive", "engage", "melee_carry")),
    _option("Renata Glasc", ("UTILITY",), "magic", ("anti_burst", "disengage"), ("dive", "melee_carry", "engage")),
    _option("Morgana", ("UTILITY", "MIDDLE"), "magic", ("spell_shield", "pick"), ("pick", "lockdown", "magic")),
    _option("Nautilus", ("UTILITY",), "magic", ("engage", "point_lockdown"), ("immobile_carry", "poke", "enchanter")),
    _option("Leona", ("UTILITY",), "magic", ("engage", "lockdown", "frontline"), ("immobile_carry", "enchanter")),
    _option("Rell", ("UTILITY",), "magic", ("engage", "frontline"), ("shield", "immobile_carry")),
    _option("Blitzcrank", ("UTILITY",), "magic", ("pick", "lane_pressure"), ("immobile", "enchanter", "poke")),
    _option("Thresh", ("UTILITY",), "physical", ("pick", "peel"), ("immobile_carry", "dive")),
    _option("Soraka", ("UTILITY",), "magic", ("heal", "sustain"), ("poke", "short_range")),
    _option("Nami", ("UTILITY",), "magic", ("engage", "sustain"), ("short_range", "immobile")),
    _option("Zyra", ("UTILITY",), "magic", ("zone", "lane_pressure"), ("engage", "short_range")),
    _option("Brand", ("UTILITY", "MIDDLE"), "magic", ("tank_buster", "poke"), ("tank", "frontline", "short_range")),
    _option("Senna", ("UTILITY", "BOTTOM"), "physical", ("range", "scaling"), ("short_range", "immobile")),
    _option("Alistar", ("UTILITY",), "magic", ("disengage", "engage", "frontline"), ("dive", "assassin", "engage")),
    _option("Tahm Kench", ("UTILITY", "TOP"), "magic", ("peel", "frontline"), ("pick", "dive", "assassin")),
)

_OPTIONS_BY_NAME = {normalize_name(option.name): option for option in _OPTIONS}

_TRAITS: dict[str, frozenset[str]] = {
    "aatrox": frozenset(("bruiser", "sustain", "dive", "physical", "short_range")),
    "ahri": frozenset(("mobile", "dash", "pick", "magic")),
    "akali": frozenset(("assassin", "mobile", "dash", "dive", "magic")),
    "alistar": frozenset(("engage", "frontline", "disengage", "magic")),
    "aphelios": frozenset(("immobile_carry", "carry", "physical", "scaling")),
    "ashe": frozenset(("engage", "utility", "physical", "immobile_carry")),
    "azir": frozenset(("scaling", "zone", "magic", "range")),
    "blitzcrank": frozenset(("pick", "engage", "frontline")),
    "brand": frozenset(("poke", "tank", "magic", "short_range")),
    "caitlyn": frozenset(("range", "poke", "physical", "immobile_carry")),
    "camille": frozenset(("dive", "mobile", "dash", "physical", "split_push")),
    "cassiopeia": frozenset(("sustain_damage", "zone", "magic")),
    "darius": frozenset(("juggernaut", "physical", "short_range", "sustain")),
    "diana": frozenset(("assassin", "dive", "engage", "magic", "dash")),
    "draven": frozenset(("immobile_carry", "lane_pressure", "physical")),
    "ekko": frozenset(("assassin", "mobile", "dash", "magic")),
    "evelynn": frozenset(("assassin", "stealth", "magic", "dive")),
    "ezreal": frozenset(("poke", "mobile", "dash", "physical")),
    "fiora": frozenset(("duel", "split_push", "physical", "mobile")),
    "fizz": frozenset(("assassin", "mobile", "dash", "magic")),
    "galio": frozenset(("frontline", "engage", "magic")),
    "garen": frozenset(("juggernaut", "physical", "short_range", "sustain")),
    "gnar": frozenset(("range", "engage", "physical")),
    "gragas": frozenset(("engage", "disengage", "magic")),
    "graves": frozenset(("physical", "short_range", "skirmish")),
    "gwen": frozenset(("tank", "magic", "sustain_damage", "short_range")),
    "hecarim": frozenset(("dive", "engage", "mobile", "physical")),
    "irelia": frozenset(("dash", "mobile", "melee_carry", "physical", "dive")),
    "jarvaniv": frozenset(("engage", "dive", "physical")),
    "jax": frozenset(("melee_carry", "split_push", "physical", "scaling")),
    "jhin": frozenset(("immobile_carry", "pick", "physical")),
    "jinx": frozenset(("immobile_carry", "scaling", "physical", "carry")),
    "kaisa": frozenset(("dive", "mobile", "carry", "mixed")),
    "kalista": frozenset(("mobile", "physical", "lane_pressure")),
    "katarina": frozenset(("assassin", "mobile", "dash", "reset", "magic")),
    "kayle": frozenset(("scaling", "magic", "immobile")),
    "khazix": frozenset(("assassin", "stealth", "dive", "physical")),
    "kindred": frozenset(("range", "physical", "anti_burst")),
    "kogmaw": frozenset(("immobile_carry", "tank", "scaling", "mixed")),
    "leblanc": frozenset(("assassin", "mobile", "dash", "magic")),
    "leona": frozenset(("engage", "frontline", "lockdown")),
    "lillia": frozenset(("mobile", "magic", "tank")),
    "lucian": frozenset(("mobile", "dash", "physical", "lane_pressure")),
    "lulu": frozenset(("shield", "peel", "enchanter", "magic")),
    "lux": frozenset(("poke", "pick", "magic", "immobile")),
    "malphite": frozenset(("frontline", "engage", "armor", "magic")),
    "masteryi": frozenset(("melee_carry", "physical", "reset", "dive")),
    "milio": frozenset(("shield", "peel", "enchanter", "range")),
    "missfortune": frozenset(("teamfight", "physical", "immobile_carry")),
    "mordekaiser": frozenset(("juggernaut", "magic", "frontline", "short_range")),
    "nautilus": frozenset(("engage", "frontline", "lockdown")),
    "nilah": frozenset(("dive", "melee_carry", "physical", "short_range")),
    "nocturne": frozenset(("dive", "assassin", "physical", "engage")),
    "olaf": frozenset(("juggernaut", "physical", "dive", "sustain")),
    "orianna": frozenset(("teamfight", "zone", "magic")),
    "ornn": frozenset(("frontline", "tank", "engage", "magic")),
    "poppy": frozenset(("anti_dash", "frontline", "physical")),
    "pyke": frozenset(("assassin", "pick", "physical", "mobile")),
    "qiyana": frozenset(("assassin", "mobile", "dash", "physical")),
    "rakan": frozenset(("engage", "mobile", "dash", "magic")),
    "rammus": frozenset(("frontline", "armor", "physical", "engage")),
    "rell": frozenset(("frontline", "engage", "shield")),
    "renekton": frozenset(("bruiser", "physical", "dive", "short_range")),
    "rengar": frozenset(("assassin", "stealth", "dive", "physical")),
    "riven": frozenset(("dash", "mobile", "physical", "dive")),
    "samira": frozenset(("mobile", "dive", "reset", "physical", "short_range")),
    "senna": frozenset(("range", "physical", "scaling")),
    "seraphine": frozenset(("teamfight", "shield", "magic", "immobile")),
    "sett": frozenset(("juggernaut", "physical", "frontline", "short_range")),
    "shen": frozenset(("frontline", "peel", "physical")),
    "sion": frozenset(("frontline", "tank", "physical")),
    "sivir": frozenset(("waveclear", "physical", "spell_shield")),
    "soraka": frozenset(("heal", "sustain", "enchanter", "immobile")),
    "syndra": frozenset(("burst", "pick", "magic", "immobile")),
    "taliyah": frozenset(("anti_dash", "zone", "magic")),
    "talon": frozenset(("assassin", "mobile", "physical")),
    "taric": frozenset(("anti_burst", "frontline", "peel")),
    "thresh": frozenset(("pick", "peel", "physical")),
    "tristana": frozenset(("mobile", "physical", "scaling")),
    "trundle": frozenset(("tank_buster", "duel", "physical")),
    "tryndamere": frozenset(("melee_carry", "split_push", "physical")),
    "twitch": frozenset(("stealth", "immobile_carry", "physical", "scaling")),
    "varus": frozenset(("poke", "pick", "physical", "immobile_carry")),
    "vayne": frozenset(("tank", "mobile", "physical", "scaling")),
    "veigar": frozenset(("zone", "burst", "magic", "scaling")),
    "vex": frozenset(("anti_dash", "burst", "magic")),
    "vi": frozenset(("dive", "point_lockdown", "physical")),
    "viego": frozenset(("melee_carry", "reset", "physical", "dive")),
    "viktor": frozenset(("zone", "scaling", "magic")),
    "wukong": frozenset(("engage", "dive", "physical")),
    "xayah": frozenset(("self_peel", "physical", "scaling")),
    "xerath": frozenset(("poke", "magic", "immobile")),
    "yasuo": frozenset(("dash", "mobile", "melee_carry", "physical")),
    "yone": frozenset(("dash", "mobile", "melee_carry", "mixed", "dive")),
    "yuumi": frozenset(("heal", "shield", "enchanter")),
    "zac": frozenset(("engage", "dive", "frontline", "magic")),
    "zed": frozenset(("assassin", "mobile", "physical", "dive")),
    "zeri": frozenset(("mobile", "physical", "scaling", "carry")),
    "ziggs": frozenset(("poke", "magic", "immobile")),
    "zoe": frozenset(("poke", "pick", "magic", "immobile")),
}

_DIRECT_COUNTERS: dict[str, tuple[str, ...]] = {
    "akali": ("Galio", "Lissandra", "Vex"),
    "aatrox": ("Poppy", "Fiora", "Malphite"),
    "ahri": ("Lissandra", "Vex", "Malzahar"),
    "caitlyn": ("Sivir", "Varus", "Nautilus"),
    "camille": ("Poppy", "Jax", "Renekton"),
    "darius": ("Quinn", "Vayne", "Teemo"),
    "draven": ("Nautilus", "Leona", "Ashe", "Varus"),
    "evelynn": ("Galio", "Lissandra", "Morgana"),
    "fiora": ("Malphite", "Poppy", "Teemo"),
    "garen": ("Vayne", "Teemo", "Gnar"),
    "irelia": ("Poppy", "Jax", "Taliyah"),
    "jarvaniv": ("Poppy", "Janna", "Xayah"),
    "jinx": ("Nocturne", "Vi", "Nautilus", "Ashe"),
    "katarina": ("Lissandra", "Galio", "Vex"),
    "khazix": ("Poppy", "Janna", "Lulu"),
    "leblanc": ("Lissandra", "Galio", "Vex"),
    "lux": ("Nautilus", "Blitzcrank", "Sivir"),
    "masteryi": ("Rammus", "Poppy", "Janna", "Lulu"),
    "milio": ("Blitzcrank", "Nautilus", "Leona"),
    "mordekaiser": ("Olaf", "Vayne", "Fiora"),
    "nautilus": ("Janna", "Morgana", "Alistar"),
    "nilah": ("Janna", "Poppy", "Xayah"),
    "nocturne": ("Janna", "Lulu", "Morgana"),
    "ornn": ("Fiora", "Gwen", "Camille", "Trundle"),
    "rakan": ("Poppy", "Janna", "Morgana"),
    "rammus": ("Lillia", "Trundle", "Gwen"),
    "rengar": ("Poppy", "Janna", "Lulu"),
    "riven": ("Poppy", "Renekton", "Malphite"),
    "samira": ("Janna", "Poppy", "Alistar", "Ashe"),
    "sett": ("Vayne", "Gnar", "Mordekaiser"),
    "sion": ("Fiora", "Gwen", "Trundle"),
    "soraka": ("Blitzcrank", "Nautilus", "Varus"),
    "tryndamere": ("Malphite", "Jax", "Poppy"),
    "vayne": ("Malphite", "Vi", "Nautilus", "Ashe"),
    "vi": ("Janna", "Poppy", "Morgana"),
    "yasuo": ("Malphite", "Poppy", "Vex", "Annie"),
    "yone": ("Poppy", "Lissandra", "Vex", "Malphite"),
    "yuumi": ("Leona", "Nautilus", "Blitzcrank"),
    "zac": ("Poppy", "Janna", "Trundle"),
    "zed": ("Lissandra", "Malzahar", "Galio"),
    "zeri": ("Vi", "Nautilus", "Ashe"),
}

_COUNTER_REASON = {
    "anti_dash": "punishes dashes",
    "anti_burst": "denies burst windows",
    "anti_magic": "absorbs magic pressure",
    "assassin": "answers assassin pressure",
    "armor": "handles physical-heavy comps",
    "carry": "pressures enemy carries",
    "dash": "punishes dash-heavy picks",
    "disengage": "breaks enemy engage",
    "dive": "protects against dives",
    "duel": "answers side-lane pressure",
    "enchanter": "pressures enchanters",
    "engage": "starts fights on carries",
    "frontline": "adds a durable front line",
    "immobile": "punishes immobile targets",
    "immobile_carry": "reaches immobile carries",
    "kiting": "kites short-range threats",
    "lockdown": "locks down mobile threats",
    "magic": "answers magic damage",
    "melee_carry": "controls melee carries",
    "mobile": "controls mobile threats",
    "peel": "protects carries from dives",
    "physical": "answers physical damage",
    "pick": "catches immobile targets",
    "point_lockdown": "guarantees lockdown",
    "poke": "punishes poke setups",
    "range": "outranges short-range picks",
    "shield": "pressures shield-heavy teams",
    "short_range": "punishes short-range comps",
    "spell_shield": "blocks key pick tools",
    "tank": "answers tanks",
    "tank_buster": "cuts through tanks",
    "true_damage": "threatens tanks",
    "waveclear": "stabilizes poke lanes",
    "zone": "controls engage paths",
}


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _role(value: Any) -> str:
    return _POSITION_TO_ROLE.get(str(value or "").strip().casefold(), "")


def _champion_name(champion_id: int, lookup: Callable[[int], str]) -> str:
    if champion_id <= 0:
        return ""
    try:
        name = str(lookup(champion_id) or "").strip()
    except Exception:
        name = ""
    return name if name and not name.startswith("Champion ") else f"Champion {champion_id}"


def _team(
    entries: Any,
    lookup: Callable[[int], str],
    *,
    local_cell_id: int,
) -> list[dict[str, Any]]:
    players: list[dict[str, Any]] = []
    if not isinstance(entries, list):
        return players
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            continue
        cell_id = _safe_int(raw.get("cellId"))
        champion_id = _safe_int(raw.get("championId"))
        intent_id = _safe_int(raw.get("championPickIntent"))
        display_id = champion_id or intent_id
        role = _role(
            raw.get("assignedPosition")
            or raw.get("position")
            or raw.get("selectedPosition")
        )
        name = _champion_name(display_id, lookup)
        players.append(
            {
                "cell_id": cell_id,
                "slot": index + 1,
                "champion_id": display_id,
                "champion": name,
                "locked": champion_id > 0,
                "role": role,
                "role_name": _ROLE_NAMES.get(role, "Unknown"),
                "is_local": cell_id == local_cell_id,
            }
        )
    return players


def _bans(session: dict[str, Any], lookup: Callable[[int], str]) -> list[str]:
    raw_bans = session.get("bans", {})
    if not isinstance(raw_bans, dict):
        return []
    seen: set[str] = set()
    names: list[str] = []
    for bucket in ("myTeamBans", "theirTeamBans"):
        values = raw_bans.get(bucket, [])
        if not isinstance(values, list):
            continue
        for value in values:
            champion_id = _safe_int(value)
            if champion_id <= 0:
                continue
            name = _champion_name(champion_id, lookup)
            key = normalize_name(name)
            if key and key not in seen:
                seen.add(key)
                names.append(name)
    return names


def _traits(champion: str) -> frozenset[str]:
    return _TRAITS.get(normalize_name(champion), frozenset())


def _composition(players: list[dict[str, Any]]) -> dict[str, Any]:
    picked = [p for p in players if p.get("champion")]
    traits = set().union(*(_traits(str(p.get("champion", ""))) for p in picked)) if picked else set()
    physical = sum(1 for p in picked if "physical" in _traits(str(p.get("champion", ""))))
    magic = sum(1 for p in picked if "magic" in _traits(str(p.get("champion", ""))))
    frontline = sum(1 for p in picked if _traits(str(p.get("champion", ""))) & {"frontline", "tank"})
    engage = sum(1 for p in picked if "engage" in _traits(str(p.get("champion", ""))))
    return {
        "picked": picked,
        "traits": traits,
        "physical": physical,
        "magic": magic,
        "frontline": frontline,
        "engage": engage,
    }


def _candidate_roles(local_role: str) -> tuple[str, ...]:
    if local_role:
        return (local_role,)
    return ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")


def _reason_text(token: str, enemy_names: list[str]) -> str:
    base = _COUNTER_REASON.get(token, token.replace("_", " "))
    if enemy_names:
        return f"{base} vs {', '.join(enemy_names[:2])}"
    return base


def build_champion_select_advice(
    session: dict[str, Any],
    champion_name_lookup: Callable[[int], str],
) -> dict[str, Any]:
    if not isinstance(session, dict) or not session:
        return {}

    local_cell_id = _safe_int(session.get("localPlayerCellId"))
    allies = _team(session.get("myTeam", []), champion_name_lookup, local_cell_id=local_cell_id)
    enemies = _team(session.get("theirTeam", []), champion_name_lookup, local_cell_id=-1)
    bans = _bans(session, champion_name_lookup)
    banned_keys = {normalize_name(name) for name in bans}
    picked_keys = {
        normalize_name(str(player.get("champion", "")))
        for player in [*allies, *enemies]
        if player.get("locked") and player.get("champion")
    }

    local_player = next((player for player in allies if player.get("is_local")), {})
    local_role = str(local_player.get("role", "") or "")
    allowed_roles = _candidate_roles(local_role)
    enemy_champions = [str(player.get("champion", "")) for player in enemies if player.get("champion")]
    enemy_traits = {name: _traits(name) for name in enemy_champions}
    enemy_comp = _composition(enemies)
    ally_comp = _composition(allies)
    direct_counter_targets: dict[str, list[tuple[str, int]]] = {}
    for enemy_name in enemy_champions:
        for order, counter in enumerate(_DIRECT_COUNTERS.get(normalize_name(enemy_name), ())):
            direct_counter_targets.setdefault(normalize_name(counter), []).append(
                (enemy_name, max(18, 42 - order * 6))
            )

    scored: list[dict[str, Any]] = []
    for option in _OPTIONS:
        if not any(role in allowed_roles for role in option.roles):
            continue
        option_key = normalize_name(option.name)
        if option_key in banned_keys or option_key in picked_keys:
            continue

        score = 48
        if local_role and option.roles and option.roles[0] == local_role:
            score += 6
        reasons: list[str] = []
        matched_enemies: list[str] = []

        for enemy_name, traits in enemy_traits.items():
            matched = option.counters & traits
            if not matched:
                continue
            score += min(30, 10 * len(matched))
            matched_enemies.append(enemy_name)
            for token in sorted(matched):
                reason = _reason_text(token, [enemy_name])
                if reason not in reasons:
                    reasons.append(reason)

        direct_matches = direct_counter_targets.get(option_key, [])
        if direct_matches:
            direct_targets = [enemy for enemy, _bonus in direct_matches]
            score += max(bonus for _enemy, bonus in direct_matches)
            matched_enemies.extend(direct_targets)
            reasons.insert(0, f"known answer to {', '.join(direct_targets[:2])}")

        if enemy_comp["physical"] >= 3 and {"armor", "frontline"} & option.provides:
            score += 14
            reasons.append("good into physical-heavy enemies")
        if enemy_comp["magic"] >= 3 and {"anti_magic", "frontline"} & option.provides:
            score += 12
            reasons.append("good into magic-heavy enemies")
        if enemy_comp["frontline"] >= 2 and {"tank_buster", "true_damage", "sustain_damage"} & option.provides:
            score += 16
            reasons.append("helps cut through front line")
        if enemy_comp["engage"] >= 2 and {"disengage", "peel", "anti_burst"} & option.provides:
            score += 14
            reasons.append("stabilizes against hard engage")

        if ally_comp["frontline"] == 0 and "frontline" in option.provides:
            score += 12
            reasons.append("adds missing front line")
        if ally_comp["engage"] == 0 and "engage" in option.provides:
            score += 8
            reasons.append("adds reliable engage")
        if ally_comp["magic"] <= 1 and option.damage == "magic":
            score += 8
            reasons.append("balances team damage")
        if ally_comp["physical"] <= 1 and option.damage == "physical":
            score += 6
            reasons.append("adds physical damage")

        if not reasons:
            score += 4
            reasons.append("solid blind option for the shown role")

        seen_reasons: set[str] = set()
        deduped_reasons: list[str] = []
        for reason in reasons:
            if reason not in seen_reasons:
                seen_reasons.add(reason)
                deduped_reasons.append(reason)

        scored.append(
            {
                "champion": option.name,
                "roles": [role for role in option.roles if role in allowed_roles] or list(option.roles),
                "role_names": [_ROLE_NAMES.get(role, role.title()) for role in option.roles if role in allowed_roles],
                "score": min(100, int(score)),
                "_sort_score": int(score),
                "reasons": deduped_reasons[:3],
                "matched_enemies": sorted(set(matched_enemies))[:3],
                "damage": option.damage,
            }
        )

    scored.sort(
        key=lambda item: (
            -int(item.get("_sort_score", 0)),
            str(item.get("champion", "")),
        )
    )
    for item in scored:
        item.pop("_sort_score", None)
    visible_recommendations = scored[:6]
    local_role_name = _ROLE_NAMES.get(local_role, "Any role")
    enemy_names = [name for name in enemy_champions if not name.startswith("Champion ")]
    summary = (
        f"{local_role_name}: best into {', '.join(enemy_names[:3])}"
        if enemy_names
        else f"{local_role_name}: waiting for enemy picks"
    )

    return {
        "phase": "ChampSelect",
        "local_cell_id": local_cell_id,
        "local_role": local_role,
        "local_role_name": local_role_name,
        "allies": allies,
        "enemies": enemies,
        "bans": bans,
        "recommendations": visible_recommendations,
        "summary": summary,
    }
