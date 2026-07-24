from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path


BUILD = "V1-CIRCULAR-RANKED-WINRATE"


def locate_project_root() -> Path:
    candidates = [
        Path.cwd(),
        Path(__file__).resolve().parent,
        Path(__file__).resolve().parent.parent,
    ]

    for candidate in candidates:
        if (candidate / "app" / "ui" / "live_match_page.py").is_file():
            return candidate

    raise FileNotFoundError(
        "Could not find app/ui/live_match_page.py.\n\n"
        "Copy this update folder's CONTENTS into the LeagueHighlights project "
        "folder, then run INSTALL_WINRATE_RING.bat again."
    )


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text

    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected one matching code block, found {count}.\n"
            "Your live_match_page.py may be a different version."
        )

    return text.replace(old, new, 1)


def install() -> None:
    root = locate_project_root()
    target = root / "app" / "ui" / "live_match_page.py"
    source_widget = Path(__file__).resolve().parent / "app" / "ui" / "win_rate_ring.py"
    target_widget = root / "app" / "ui" / "win_rate_ring.py"

    if not source_widget.is_file():
        raise FileNotFoundError(f"Missing update file: {source_widget}")

    original = target.read_text(encoding="utf-8")

    if BUILD in original and target_widget.is_file():
        print("Circular win-rate update is already installed.")
        return

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = target.with_name(f"live_match_page.py.backup-{timestamp}")
    shutil.copy2(target, backup)
    if source_widget.resolve() != target_widget.resolve():
        shutil.copy2(source_widget, target_widget)

    updated = original

    updated = replace_once(
        updated,
        "from app.services.live_match_scout import LiveMatchScout\n",
        "from app.services.live_match_scout import LiveMatchScout\n"
        "from app.ui.win_rate_ring import CircularWinRate\n",
        "Add CircularWinRate import",
    )

    updated = replace_once(
        updated,
        'LIVE_MATCH_UI_BUILD = "V29-RELIABLE-PROGRESSIVE-SMART-WINDOWS"\n',
        'LIVE_MATCH_UI_BUILD = "V29-RELIABLE-PROGRESSIVE-SMART-WINDOWS"\n'
        f'WIN_RATE_RING_BUILD = "{BUILD}"\n',
        "Add update build marker",
    )

    updated = replace_once(
        updated,
        """        rank_row.addLayout(rank_texts, 1)
        root.addLayout(rank_row)
""",
        """        rank_row.addLayout(rank_texts, 1)

        self.win_rate_ring = CircularWinRate()
        rank_row.addWidget(
            self.win_rate_ring,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        root.addLayout(rank_row)
""",
        "Add win-rate ring to player card",
    )

    updated = replace_once(
        updated,
        """        self.level_chip.setText("Lv —")
        self._set_tags([])
""",
        """        self.level_chip.setText("Lv —")
        self.win_rate_ring.clear_win_rate("Ranked win rate unavailable")
        self._set_tags([])
""",
        "Reset win-rate ring without player data",
    )

    updated = replace_once(
        updated,
        """        ranked_games = int(stats.get("ranked_games", stats.get("games", 0)) or 0)
        ranked_wr = stats.get("ranked_win_rate", stats.get("win_rate"))
        if rank_state == "ready" and ranked_games and ranked_wr is not None:
""",
        """        ranked_games = int(stats.get("ranked_games", stats.get("games", 0)) or 0)
        ranked_wr = stats.get("ranked_win_rate", stats.get("win_rate"))

        if rank_state == "ready" and ranked_wr is not None:
            self.win_rate_ring.set_win_rate(float(ranked_wr), ranked_games)
        elif rank_state == "unranked":
            self.win_rate_ring.set_win_rate(0.0, 0)
            self.win_rate_ring.setToolTip("No ranked Solo/Duo games")
        elif rank_state == "unavailable":
            self.win_rate_ring.clear_win_rate("Ranked win rate unavailable")
        else:
            self.win_rate_ring.clear_win_rate("Ranked win rate is loading")

        if rank_state == "ready" and ranked_games and ranked_wr is not None:
""",
        "Connect ranked win rate to ring",
    )

    target.write_text(updated, encoding="utf-8")

    print()
    print("Circular ranked win-rate ring installed successfully.")
    print(f"Project: {root}")
    print(f"Backup:  {backup}")
    print()
    print("Start the app with: python main.py")


def main() -> int:
    try:
        install()
        return 0
    except Exception as exc:
        print()
        print("INSTALL FAILED")
        print(str(exc))
        print()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
