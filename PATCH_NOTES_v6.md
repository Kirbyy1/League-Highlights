# v6 — Automatic League events

## Added
- Local Riot Live Client Data API monitor (`127.0.0.1:2999`).
- Automatic clips for:
  - Single kill
  - Double kill
  - Triple kill
  - Quadra kill
  - Pentakill
  - Dragon secured by your team
  - Baron secured by your team
- Individual Settings toggles for every category.
- Team-aware objective filtering, so enemy Dragon/Baron kills are ignored.
- Dragon/Baron steal labels when Riot reports the objective as stolen.
- Kill-chain grouping: a triple kill creates one Triple Kill clip, not three separate clips.
- Live-data connection status in Settings.
- Automatic event labels stored in clip metadata and filenames.

## Timing
- Champion kills settle for about 10.5 seconds before saving so the app can determine whether the play became a double/triple/quadra/pentakill.
- Objectives trigger immediately and the exporter closes at the next 5-second segment boundary.

## Notes
- The local endpoint is only available while an actual League match is active.
- Starting the app mid-game baselines existing events, so it will not clip old kills/objectives.
