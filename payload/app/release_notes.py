from __future__ import annotations

from copy import deepcopy


_RELEASE_NOTES: dict[str, tuple[dict[str, object], ...]] = {
    "1.0.0": (
        {
            "eyebrow": "AUTOMATIC UPDATES",
            "title": "Updates that stay out of your way",
            "description": (
                "League Highlights now checks GitHub Releases in the background, verifies the "
                "download, and stages it outside the running application."
            ),
            "bullets": (
                "The recorder is never replaced while it is running.",
                "Updates install only after the application fully exits.",
                "A separate updater can roll back the installation if replacement fails.",
            ),
        },
        {
            "eyebrow": "CLEANER INTERFACE",
            "title": "Sharper PyCharm-style geometry",
            "description": (
                "Cards, inputs, menus, and buttons now use tighter corner radii for a more "
                "intentional desktop-tool appearance."
            ),
            "bullets": (
                "Flatter cards and controls with less visual softness.",
                "The hamburger menu no longer uses Qt's menu-button overlay.",
                "Existing recording, player, trimmer, and library behavior is unchanged.",
            ),
        },
        {
            "eyebrow": "WHAT'S NEW",
            "title": "A proper release highlights carousel",
            "description": (
                "After an update, a focused release dialog presents the important changes "
                "without permanently taking space from the library."
            ),
            "bullets": (
                "The application behind the dialog is softly blurred.",
                "Multiple feature pages can be browsed with arrows and page indicators.",
                "The dialog is shown once per installed version and can be reopened from the menu.",
            ),
        },
    ),
}


def notes_for_version(version: str) -> list[dict[str, object]]:
    """Return an isolated copy so UI code can safely normalize the values."""

    notes = _RELEASE_NOTES.get(str(version), ())
    return deepcopy(list(notes))
