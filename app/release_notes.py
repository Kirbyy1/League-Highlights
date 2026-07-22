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
    "1.0.4": (
        {
            "eyebrow": "INTERFACE REDESIGN",
            "title": "A cleaner desktop experience",
            "description": (
                "League Highlights now uses a more restrained interface inspired by JetBrains "
                "tools and Discord, with stronger hierarchy and less visual clutter."
            ),
            "bullets": (
                "Flatter surfaces and tighter corner radii throughout the application.",
                "Cleaner title-bar, navigation, buttons, inputs, menus, and settings sections.",
                "Improved spacing and typography make important information easier to scan.",
            ),
        },
        {
            "eyebrow": "HIGHLIGHTS LIBRARY",
            "title": "Find the match you want faster",
            "description": (
                "The Highlights page now includes better library controls and clearer match "
                "rows without changing how recordings or clips are stored."
            ),
            "bullets": (
                "Search recorded matches directly from the Highlights page.",
                "Filter the library by victories or defeats.",
                "See match count, game metadata, highlight count, and duration more clearly.",
            ),
        },
        {
            "eyebrow": "BETTER NAVIGATION",
            "title": "Settings and status at a glance",
            "description": (
                "Settings are easier to navigate, while a compact bottom status bar keeps "
                "important recorder information visible without taking over the interface."
            ),
            "bullets": (
                "Settings categories now use a dedicated vertical navigation layout.",
                "The bottom bar shows recorder state, League data, resolution, FPS, audio, and hotkey.",
                "Recording, clipping, exporting, updater, and playback behavior remain unchanged.",
            ),
        },
    ),
    "1.0.5": (
        {
            "eyebrow": "SMART HIGHLIGHTS V2",
            "title": "Better fights, fewer duplicate clips",
            "description": (
                "Automatic highlights now group related combat events into one complete play "
                "instead of saving several overlapping clips from the same fight."
            ),
            "bullets": (
                "Related kills, assists, and objective steals are grouped into one highlight.",
                "Adaptive timing adds more context to longer and more valuable fights.",
                "Overlapping automatic highlights are suppressed before saving.",
            ),
        },
        {
            "eyebrow": "PERFORMANCE",
            "title": "Lighter during long sessions",
            "description": (
                "Background work now scales down when League or the application is inactive, "
                "while recorded matches and previews are loaded only when needed."
            ),
            "bullets": (
                "League and metadata polling slow down while no match is active.",
                "Match cards load progressively instead of loading the full library at once.",
                "Unchanged clip metadata and filmstrip previews are cached.",
            ),
        },
        {
            "eyebrow": "RELIABILITY",
            "title": "Safer recording and exporting",
            "description": (
                "Recording, clip saving, temporary storage, and hardware encoding now include "
                "additional safeguards and recovery behavior."
            ),
            "bullets": (
                "Healthy hardware encoders are preferred with automatic fallback after failures.",
                "Saved highlights are validated before appearing in the library.",
                "Rolling segments, temporary files, and concurrent FFmpeg jobs are bounded.",
            ),
        },
    ),
}


def notes_for_version(version: str) -> list[dict[str, object]]:
    """Return an isolated copy so UI code can safely normalize the values."""

    notes = _RELEASE_NOTES.get(str(version), ())
    return deepcopy(list(notes))