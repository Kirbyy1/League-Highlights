# v14 — Better Audio Controls

## Added

- Enable/disable system audio.
- Select the Windows output/loopback device.
- Adjust system-audio volume from 0% to 200%.
- Enable/disable microphone capture.
- Select the microphone input device.
- Adjust microphone volume from 0% to 200%.
- Select AAC audio quality: 96, 128, 160, or 192 kbps.
- Refresh audio devices without restarting the application.
- Clip cards now show `System audio`, `Microphone`, `System + microphone`, or `Video only`.
- Audio settings persist in the existing settings file.

## How it works

System and microphone audio are captured into separate rolling buffers. When a
highlight is saved, FFmpeg mixes only the enabled sources and applies the chosen
volume levels plus a limiter. The H.264 video is stream-copied, so these audio
controls do not reduce video quality.

## Discord voice

Discord voice is included in **System audio** when Discord plays through the
selected output device. This version does not yet isolate League and Discord as
separate per-application audio sources.

## Installation

Close League Highlights, copy the patch's `app` folder over the existing `app`
folder, choose **Replace files in the destination**, then restart the app.
