# v10 — Smart Highlights and Match Reels

## Smart play detection

The app now uses Riot's local live match data to attach real context to each candidate moment:

- Active Riot ID, champion, team, game mode, and map
- Victim names and victim champions
- Assister names and solo-kill detection
- Exact Riot multikill events when available
- Active-player health, level, death state, and score snapshots
- Ace, Dragon, Baron, Elder, and objective-steal context

An explainable score decides whether a candidate is worth saving. The score rewards multikills, solo kills, low-health survival, level disadvantage, fast kill chains, aces, Elder/Baron, and steals. It penalizes dying immediately after an ordinary play and simple one-for-one kills.

Sensitivity presets:

- **Strict** — saves only standout plays
- **Balanced** — recommended default
- **Save more** — keeps more candidates

Triple kills, quadra kills, pentakills, objective steals, Elder kills, and manual clips are always retained.

## Ratings that affect future detection

Each clip has Good / Not good feedback buttons. Ratings are stored locally beside the clip metadata. After at least two ratings for a similar event type, the app gently adjusts the future score threshold for that kind of play. No clip or rating is uploaded anywhere.

## One video per match

At GameEnd, the app can build one chronological `MATCH_HIGHLIGHTS_...mp4` containing only the accepted moments from that match.

- Moments are ordered by when they occurred.
- Overlapping footage between neighboring clips is removed.
- Video and audio are normalized before joining.
- Manual clips can optionally be included.
- Individual clips can be kept or deleted after the reel succeeds.
- A reel is created only when a match has at least two unique saved moments.

The match reel is intended as the complete game recap. Individual Discord-ready clips remain the easiest files to send one at a time.

## Reliability improvements

- Clip requests are queued instead of being discarded while another clip is exporting.
- Match compilation waits behind queued clips.
- Temporary Live Client API interruptions must persist for about 20 seconds before the match is considered ended.
- Existing manual hotkey, Discord compression, recording resolution/FPS, and event toggles remain available.
