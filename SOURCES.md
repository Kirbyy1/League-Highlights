# Technical sources used for the MVP design

- FFmpeg Windows capture device documentation (`gdigrab`, including `title=` and `hwnd=` inputs): https://ffmpeg.org/ffmpeg-devices.html
- FFmpeg Desktop Duplication (`ddagrab`) documentation/wiki: https://trac.ffmpeg.org/wiki/Capture/Desktop
- FFmpeg official download page, which links Windows builds from gyan.dev and BtbN: https://ffmpeg.org/download.html
- PyAudioWPatch WASAPI loopback package: https://pypi.org/project/PyAudioWPatch/
- PyAudioWPatch loopback recording example: https://github.com/s0d3s/PyAudioWPatch/blob/master/examples/pawp_record_wasapi_loopback.py
- PySide6 (official Qt for Python bindings): https://pypi.org/project/PySide6/

- Riot Games Live Client Data API documentation (`activeplayer`, `playerlist`, `eventdata`, `gamestats`): https://developer.riotgames.com/docs/lol#game-client-api_live-client-data-api
- Riot Live Client event schema (ChampionKill, Multikill, DragonKill, BaronKill, Ace, GameEnd): https://static.developer.riotgames.com/docs/lol/liveclientdata_events.json
