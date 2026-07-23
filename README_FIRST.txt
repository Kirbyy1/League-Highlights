LEAGUE HIGHLIGHTS — LIVE MATCH V6 CUMULATIVE SOURCE UPDATE

This is the real cumulative source package.

It contains:
- PUUID League-v4 rank lookup fix
- vertical 5-versus-5 player-card interface
- background loading instead of Riot requests on the UI thread
- 20 recent ranked games
- main-role, secondary-role, flex-role and likely off-role detection
- champion familiarity and recent champion win rate
- premade detection
- role-specific behavioural tags
- early jungle invade/fight analysis
- confidence tooltips
- team summaries
- all earlier Live Match changes made while you were on your phone

EASIEST INSTALL

1. Fully stop League Highlights in PyCharm.
2. Extract this ZIP to any normal folder, such as Downloads.
3. Open PowerShell in the extracted folder.
4. Run:

   powershell -ExecutionPolicy Bypass -File ".\INSTALL_LIVE_MATCH_UPDATE.ps1"

The installer automatically targets:
C:\Users\alekkum\PycharmProjects\LeagueHighlights

It backs up the current files, replaces them, clears __pycache__, and verifies the update.

5. Start main.py again.
