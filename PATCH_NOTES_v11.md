# v11 — Game-Based Highlight Library

This version corrects the match organization model. It does not generate one combined video.

## New behavior

- The top-level Highlights page shows games that contain saved highlights.
- Clicking a game opens the individual clips from that game.
- Clips are ordered by their in-game recording window.
- Each game card shows champion, player, mode/result when available, clip count, total duration, total file size, and a representative thumbnail.
- Match result and map metadata are written to every clip when GameEnd is detected.
- Older clips without a match ID are kept in an Older ungrouped clips group.
- Existing MATCH_HIGHLIGHTS reel files from v10 are hidden from the UI and no new reels are created.
- Smart scoring, player/victim names, ratings, Discord compression, automatic events, and the custom hotkey remain available.

## Updating

Copy the patch `app` folder over the existing project's `app` folder and replace files. The old `app/services/match_compiler.py` file can remain because v11 no longer imports or runs it. It may also be deleted manually.
