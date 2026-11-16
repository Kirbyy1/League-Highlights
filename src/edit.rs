//! Clip editor export: trim range + optional background music and slow
//! motion, written as a new clip next to the original. Without slow motion the
//! video is stream-copied (no quality loss) and only the audio is re-encoded;
//! slow motion re-encodes the video with the hardware encoder.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::{json, Value};

use crate::ffmpeg::{last_line, Ffmpeg};
use crate::library::{read_meta, thumbnail, write_meta};
use crate::models::{round3, StoredEvent};
use crate::scoring::suggest_trim;
use crate::util::{local_time, now};

pub struct EditOptions {
    pub start: f64,
    pub end: f64,
    pub music: Option<PathBuf>,
    /// Where in the song the clip starts (seconds).
    pub music_offset: f64,
    /// 0.0 - 2.0
    pub music_volume: f64,
    /// 0.0 - 2.0 (game audio under the music)
    pub game_volume: f64,
    pub fade: bool,
    /// Playback speed: 1.0 normal, 0.5 or 0.25 slow motion.
    pub speed: f64,
    /// Video encoder for slow motion (from Ffmpeg::choose_encoder).
    pub encoder: Option<&'static str>,
}

/// Speeds the editor offers.
pub fn clamp_speed(speed: f64) -> f64 {
    if speed <= 0.3 {
        0.25
    } else if speed < 0.9 {
        0.5
    } else {
        1.0
    }
}

/// atempo only goes down to 0.5, so 0.25 is two steps.
fn atempo(speed: f64) -> String {
    if speed <= 0.3 { ",atempo=0.5,atempo=0.5".into() } else if speed < 0.9 { format!(",atempo={speed}") } else { String::new() }
}

/// FFmpeg arguments for the edit. Separate from the run so it can be tested.
pub fn build_args(source: &Path, output: &Path, o: &EditOptions, clip_has_audio: bool) -> Vec<String> {
    let input_duration = (o.end - o.start).max(0.5);
    let speed = clamp_speed(o.speed);
    // The result is longer when slowed down.
    let duration = input_duration / speed;
    let mut args: Vec<String> = vec![
        "-hide_banner".into(), "-loglevel".into(), "error".into(), "-y".into(),
        "-ss".into(), format!("{:.3}", o.start), "-t".into(), format!("{input_duration:.3}"),
        "-i".into(), source.to_string_lossy().into_owned(),
    ];
    if let Some(music) = &o.music {
        // Loop short songs so the music never stops before the clip does.
        args.extend(["-stream_loop".into(), "-1".into(), "-ss".into(), format!("{:.3}", o.music_offset.max(0.0)), "-i".into(), music.to_string_lossy().into_owned()]);
    }
    args.extend(["-map".into(), "0:v:0".into()]);
    if speed < 1.0 {
        args.extend(["-vf".into(), format!("setpts=PTS/{speed}")]);
        args.extend(crate::share::encoder_args(o.encoder.unwrap_or("libx264"), 14000));
    } else {
        args.extend(["-c:v".into(), "copy".into()]);
    }

    let stereo = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo";
    let game = format!("[0:a]{stereo}{},volume={:.3}", atempo(speed), o.game_volume.clamp(0.0, 2.0));
    let mut music = format!("[1:a]{stereo},volume={:.3}", o.music_volume.clamp(0.0, 2.0));
    if o.fade {
        let fade_in = 1.0_f64.min(duration / 4.0);
        let fade_out = 1.5_f64.min(duration / 3.0);
        music.push_str(&format!(",afade=t=in:st=0:d={fade_in:.2},afade=t=out:st={:.3}:d={fade_out:.2}", duration - fade_out));
    }
    let filter = match (o.music.is_some(), clip_has_audio) {
        (true, true) => Some(format!(
            "{game}[g];{music}[m];[g][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95[a]"
        )),
        (true, false) => Some(format!("{music},atrim=0:{duration:.3}[a]")),
        (false, true) => Some(format!("{game}[a]")),
        (false, false) => None,
    };
    match filter {
        Some(f) => args.extend([
            "-filter_complex".into(), f, "-map".into(), "[a]".into(),
            "-c:a".into(), "aac".into(), "-b:a".into(), "192k".into(), "-ac".into(), "2".into(),
        ]),
        None => args.push("-an".into()),
    }
    args.extend(
        // No -avoid_negative_ts: the copied video keeps its keyframe lead-in
        // as hidden pre-roll (MP4 edit list), so playback starts exactly at
        // the chosen range and stays in sync with the newly mixed audio.
        ["-t", &format!("{duration:.3}"), "-movflags", "+faststart", "-f", "mp4"]
            .iter()
            .map(|s| s.to_string()),
    );
    args.push(output.to_string_lossy().into_owned());
    args
}

/// Render the edit. Returns the new clip's file name.
pub fn render(ff: &Ffmpeg, source: &Path, o: &EditOptions) -> Result<String, String> {
    let mut meta = read_meta(&source.with_extension("json"));
    let clip_duration = meta.get("duration_seconds").and_then(Value::as_f64).unwrap_or(f64::MAX);
    let start = o.start.max(0.0);
    let end = o.end.min(clip_duration);
    if end - start < 1.0 {
        return Err("Select at least one second to export.".into());
    }
    let speed = clamp_speed(o.speed);
    let encoder = if speed < 1.0 { Some(ff.choose_encoder()?) } else { None };
    let o = EditOptions { start, end, music: o.music.clone(), speed, encoder, ..*o };
    let stem = source.file_stem().unwrap_or_default().to_string_lossy().into_owned();
    let mut output = source.with_file_name(format!("{stem}_edit.mp4"));
    let mut n = 2;
    while output.exists() {
        output = source.with_file_name(format!("{stem}_edit_{n}.mp4"));
        n += 1;
    }
    let partial = output.with_extension("mp4.part");
    let has_audio = ff.has_audio(source);
    ff.run(&build_args(source, &partial, &o, has_audio), Duration::from_secs(240))
        .map_err(|e| {
            let _ = fs::remove_file(&partial);
            format!("Export failed: {}", last_line(&e))
        })?;
    if fs::metadata(&partial).map_or(0, |m| m.len()) < 4096 {
        let _ = fs::remove_file(&partial);
        return Err("Export produced an empty file.".into());
    }
    fs::rename(&partial, &output).map_err(|e| e.to_string())?;
    let duration = ff.probe_duration(&output).unwrap_or((end - start) / speed);

    // Metadata: same match and labels, events shifted into the new timeline.
    let events: Vec<StoredEvent> = meta
        .get("events")
        .and_then(Value::as_array)
        .map(|list| list.iter().filter_map(StoredEvent::from_json).collect())
        .unwrap_or_default();
    let shifted: Vec<StoredEvent> = events
        .into_iter()
        .filter(|e| e.relative_time >= start && e.relative_time <= end)
        .map(|e| StoredEvent { relative_time: (e.relative_time - start) / speed, ..e })
        .collect();
    let trigger = meta
        .get("trigger_relative_seconds")
        .and_then(Value::as_f64)
        .map_or(duration, |t| (t - start) / speed)
        .clamp(0.0, duration);
    let manual = meta.get("event_kind").and_then(Value::as_str).unwrap_or("manual") == "manual";
    let suggestion = suggest_trim(duration, &shifted, Some(trigger), manual);
    let size = fs::metadata(&output).map_or(0, |m| m.len());
    let music_name = o.music.as_ref().and_then(|m| m.file_stem()).map(|s| s.to_string_lossy().into_owned());
    if let Some(ws) = meta.get("clip_window_start_wall").and_then(Value::as_f64) {
        meta.insert("clip_window_start_wall".into(), json!(ws + start));
        meta.insert("clip_window_end_wall".into(), json!(ws + end));
    }
    for (key, value) in [
        ("created_at", json!(local_time(now()).iso())),
        ("duration_seconds", json!(round3(duration))),
        ("edited_from", json!(source.file_name().unwrap().to_string_lossy())),
        ("edited", json!(true)),
        ("music_track", json!(music_name)),
        ("slow_motion", json!(if speed < 1.0 { Some(speed) } else { None })),
        ("events", Value::Array(shifted.iter().map(StoredEvent::to_json).collect())),
        ("trigger_relative_seconds", json!(round3(trigger))),
        ("suggested_trim_start", json!(round3(suggestion.start))),
        ("suggested_trim_end", json!(round3(suggestion.end))),
        ("size_bytes", json!(size)),
        ("discord_ready", json!(size < 10_000_000)),
        ("audio_included", json!(has_audio || o.music.is_some())),
        ("reviewed", json!(true)),
        ("rating", json!("")),
    ] {
        meta.insert(key.into(), value);
    }
    for key in ["file", "has_thumb", "mtime"] {
        meta.remove(key);
    }
    write_meta(&output.with_extension("json"), &meta).map_err(|e| e.to_string())?;
    thumbnail(ff, &output, &output.with_extension("jpg"), (duration * 0.4).max(0.1));
    log::info!("Edited clip {} ({size} bytes{})", output.display(), music_name.map(|m| format!(", music: {m}")).unwrap_or_default());
    Ok(output.file_name().unwrap().to_string_lossy().into_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn opts(music: bool) -> EditOptions {
        EditOptions {
            start: 2.0,
            end: 12.0,
            music: music.then(|| PathBuf::from("song.mp3")),
            music_offset: 30.0,
            music_volume: 0.8,
            game_volume: 0.5,
            fade: true,
            speed: 1.0,
            encoder: None,
        }
    }

    #[test]
    fn mixes_music_under_game_audio() {
        let args = build_args(Path::new("in.mp4"), Path::new("out.mp4"), &opts(true), true).join(" ");
        assert!(args.contains("-stream_loop -1 -ss 30.000 -i song.mp3"));
        assert!(args.contains("[g][m]amix=inputs=2:duration=first"));
        assert!(args.contains("afade=t=out:st=8.500:d=1.50"));
        assert!(args.contains("-c:v copy"));
    }

    #[test]
    fn slow_motion_stretches_video_and_sound() {
        let mut o = opts(true);
        o.speed = 0.25;
        o.encoder = Some("h264_nvenc");
        let args = build_args(Path::new("in.mp4"), Path::new("out.mp4"), &o, true).join(" ");
        assert!(args.contains("-t 10.000 -i in.mp4"), "reads 10 s of the clip");
        assert!(args.contains("setpts=PTS/0.25") && args.contains("h264_nvenc") && !args.contains("-c:v copy"));
        assert!(args.contains("atempo=0.5,atempo=0.5"));
        assert!(args.contains("-t 40.000 -movflags"), "writes 40 s");
        assert_eq!((clamp_speed(0.5), clamp_speed(0.1), clamp_speed(2.0)), (0.5, 0.25, 1.0));
    }

    #[test]
    fn music_only_and_no_music_paths() {
        let silent = build_args(Path::new("in.mp4"), Path::new("out.mp4"), &opts(true), false).join(" ");
        assert!(silent.contains("atrim=0:10.000[a]") && !silent.contains("amix"));
        let plain = build_args(Path::new("in.mp4"), Path::new("out.mp4"), &opts(false), true).join(" ");
        assert!(plain.contains("[0:a]") && !plain.contains("song.mp3"));
        let none = build_args(Path::new("in.mp4"), Path::new("out.mp4"), &opts(false), false).join(" ");
        assert!(none.contains("-an"));
    }
}
