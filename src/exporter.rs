//! Turns rolling segments into a saved highlight.
//!
//! One FFmpeg process per clip (the Python version used ~25: an ffprobe per
//! segment, concat, mux, copy and thumbnail). Video is stream-copied, audio is
//! mixed from the WAV buffers, and the thumbnail comes from the same run as a
//! second output. The clip is written as `.part` and renamed when complete.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::audio::Audio;
use crate::config::{Paths, SharedSettings, MIN_FREE_DISK_MIB, SEGMENT_SECONDS};
use crate::ffmpeg::{last_line, Ffmpeg};
use crate::library::write_meta;
use crate::models::{round3, EventTracker, HighlightRequest, StoredEvent};
use crate::recorder::{Recorder, TimedSegment};
use crate::scoring::suggest_trim;
use crate::util::{disk_free_bytes, local_time, now, underscore_alnum};

pub struct Exporter {
    pub cfg: SharedSettings,
    pub paths: Paths,
    pub ff: Arc<Ffmpeg>,
    pub recorder: Arc<Recorder>,
    pub audio: Arc<Audio>,
    pub tracker: Arc<EventTracker>,
}

fn fallback_duration(label: &str, buffer: u32) -> f64 {
    let seconds = match label {
        "SINGLE KILL" => 12,
        "DOUBLE KILL" => 16,
        "TRIPLE KILL" => 20,
        "QUADRA KILL" => 26,
        "PENTAKILL" => 32,
        "ELDER DRAGON" => 16,
        "MANUAL CLIP" => buffer,
        l if l.contains("DRAGON") || l.contains("BARON") => 14,
        _ => buffer,
    };
    f64::from(seconds.min(buffer))
}

fn request_event_type(r: &HighlightRequest) -> String {
    if r.event_kind == "manual" || !r.automatic {
        return "MANUAL_TRIGGER".into();
    }
    let label = r.clean_label();
    if r.event_kind == "victory" {
        return "VICTORY".into();
    }
    if r.event_kind == "objective" {
        return "OBJECTIVE_STEAL".into();
    }
    if label.contains("BARON") {
        return if label.contains("STEAL") { "BARON_STEAL" } else { "BARON" }.into();
    }
    if label.contains("DRAGON") {
        return if label.contains("STEAL") { "DRAGON_STEAL" } else { "DRAGON" }.into();
    }
    if ["DOUBLE", "TRIPLE", "QUADRA", "PENTA"].iter().any(|t| label.contains(t)) {
        return "MULTIKILL".into();
    }
    if label.contains("KILL") {
        return "CHAMPION_KILL".into();
    }
    r.event_kind.to_uppercase()
}

fn concat_escape(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/").replace('\'', "'\\''")
}

fn unique_path(path: PathBuf) -> PathBuf {
    if !path.exists() {
        return path;
    }
    let stem = path.file_stem().unwrap().to_string_lossy().into_owned();
    (2..)
        .map(|n| path.with_file_name(format!("{stem}_{n}.mp4")))
        .find(|p| !p.exists())
        .unwrap()
}

impl Exporter {
    pub fn export(&self, request: &HighlightRequest) -> Result<PathBuf, String> {
        let s = self.cfg.read().unwrap().clone();
        let clip_dir = s.clip_dir();
        fs::create_dir_all(&clip_dir).map_err(|e| format!("Could not create {}: {e}", clip_dir.display()))?;
        if disk_free_bytes(&clip_dir).is_some_and(|free| free < MIN_FREE_DISK_MIB * 1024 * 1024) {
            return Err(format!(
                "Not enough free disk space to save a highlight. Free at least {MIN_FREE_DISK_MIB} MiB and try again."
            ));
        }
        if !self.recorder.running() {
            return Err("Recording is not active.".into());
        }

        let label = request.clean_label();
        // Long manual clips (shortcut pressed twice) keep their exact window.
        let long = request.event_kind == "long";
        let precise = request
            .desired_window()
            .filter(|_| long || (s.discord_auto_trim_events && request.automatic));

        // Objectives arrive immediately: wait until the post-roll exists.
        let needed_end = precise.map_or_else(|| request.triggered_at_wall.unwrap_or_else(now), |w| w.1);
        let delay = needed_end - now();
        if delay > 0.0 {
            log::info!("Waiting {delay:.1}s for post-event footage");
            std::thread::sleep(Duration::from_secs_f64((delay + 0.15).min(f64::from(s.buffer_seconds))));
        }
        let segment_wait = Duration::from_secs(u64::from(SEGMENT_SECONDS) + 3);
        if !self.recorder.wait_for_footage(needed_end, segment_wait) {
            log::warn!("Latest footage did not reach the requested end; using what is available");
        }

        let _hold = self.recorder.hold();
        let timeline = self.recorder.timeline();
        if timeline.is_empty() {
            return Err(format!(
                "The recording buffer is still warming up. Try {} again in a few seconds.",
                s.hotkey_display
            ));
        }
        let (mut selected, trim_start, trim_duration, requested) = match precise {
            Some((start, end)) => select_precise(&timeline, start, end)?,
            None => {
                let requested = fallback_duration(&label, s.buffer_seconds);
                select_tail(&timeline, requested)
            }
        };
        if trim_duration < 3.0 {
            return Err(format!(
                "The recording buffer is still warming up. Try {} again in a few seconds.",
                s.hotkey_display
            ));
        }
        // The event window that was asked for, on the wall clock.
        let window_start = selected[0].start_wall + trim_start;
        let window_end = window_start + trim_duration;
        let trigger_wall = request.triggered_at_wall.or(request.event_ended_at).or(request.event_started_at);
        let manual = request.event_kind == "manual" || !request.automatic;
        // Everything about the clip that depends on which segments are kept.
        let describe = |selected: &[TimedSegment]| {
            let start = selected[0].start_wall;
            let end = selected[selected.len() - 1].end_wall;
            let duration = selected.iter().map(|seg| seg.end_wall - seg.start_wall).sum::<f64>();
            let trigger_relative = (trigger_wall.unwrap_or(end) - start).clamp(0.0, duration);
            let mut events = self.tracker.events_for_clip(start, end, &request.match_id);
            let expected = request_event_type(request);
            if !events
                .iter()
                .any(|e| e.event_type == expected && (e.relative_time - trigger_relative).abs() <= 1.5)
            {
                events.push(StoredEvent {
                    relative_time: trigger_relative,
                    event_type: expected,
                    game_time: request.event_game_time,
                });
                events.sort_by(|a, b| a.relative_time.total_cmp(&b.relative_time));
            }
            let suggestion = suggest_trim(duration, &events, Some(trigger_relative), manual);
            (start, end, duration, trigger_relative, events, suggestion)
        };
        let (mut selected_start, mut selected_end, mut duration, mut trigger_relative, mut events, mut suggestion) =
            describe(&selected);

        // Trim to the action before saving, by keeping only the 2-second
        // segments the Smart Trim range touches. The clip is written once;
        // trimming it afterwards cost two more FFmpeg runs and a second copy
        // of the file.
        let mut auto_trimmed = false;
        if s.auto_trim && request.automatic && !matches!(request.event_kind.as_str(), "death" | "long") {
            if let Some((from, to)) = auto_trim_window(duration, suggestion.start, suggestion.end) {
                let kept = keep_window(&selected, selected_start + from, selected_start + to);
                if !kept.is_empty() && kept.len() < selected.len() {
                    log::info!("Trimmed to the action: {from:.1}s to {to:.1}s of {duration:.1}s");
                    selected = kept;
                    (selected_start, selected_end, duration, trigger_relative, events, suggestion) = describe(&selected);
                    auto_trimmed = true;
                }
            }
        }
        let window_start = window_start.max(selected_start);
        let window_end = window_end.min(selected_end);

        let created = now();
        let stamp = local_time(created);
        let mut file_label = underscore_alnum(&label.to_uppercase());
        if file_label.is_empty() {
            file_label = "HIGHLIGHT".into();
        }
        let champion = underscore_alnum(&request.champion_name);
        let prefix = if champion.is_empty() { file_label } else { format!("{champion}_{file_label}") };
        let output = unique_path(clip_dir.join(format!("{prefix}_{}.mp4", stamp.file_stamp())));
        let partial = output.with_extension("mp4.part");
        let thumb = output.with_extension("jpg");

        let work = self.paths.root.join("export").join(format!("{}", (created * 1000.0) as u64));
        fs::create_dir_all(&work).map_err(|e| e.to_string())?;
        // Preview frame: the play itself, not the start of the clip. It is the
        // first frame of the 2-second segment that starts nearest to the play,
        // so exactly one frame is decoded. (Decoding the clip up to the play
        // cost 10 s of CPU per H.264 clip and 30 s per AV1 clip, mid-game.)
        let moment = trigger_wall.map_or(selected_start + duration / 2.0, |w| w);
        let thumb_from = thumb_source(&selected, moment);
        let result = self.mux(&s, &work, &selected, selected_start, selected_end, &partial, &thumb, &thumb_from);
        let _ = fs::remove_dir_all(&work);
        let audio = match result {
            Ok(audio) => audio,
            Err(e) => {
                let _ = fs::remove_file(&partial);
                return Err(e);
            }
        };
        let size = fs::metadata(&partial).map_or(0, |m| m.len());
        if size < 4096 {
            let _ = fs::remove_file(&partial);
            return Err("The saved highlight file is incomplete.".into());
        }
        fs::rename(&partial, &output).map_err(|e| e.to_string())?;

        let mut meta = Map::new();
        let mut put = |k: &str, v: Value| {
            meta.insert(k.to_string(), v);
        };
        put("label", json!(label));
        put("created_at", json!(stamp.iso()));
        put("duration_seconds", json!(round3(duration)));
        put("match_id", json!(request.match_id));
        put("player_name", json!(request.player_name));
        put("champion_name", json!(request.champion_name));
        put("game_mode", json!(request.game_mode));
        put("match_started_at", json!(request.match_started_at));
        put("event_kind", json!(request.event_kind));
        put("event_game_time", json!(request.event_game_time));
        put("automatic", json!(request.automatic));
        put("trigger_relative_seconds", json!(round3(trigger_relative)));
        put("events", Value::Array(events.iter().map(StoredEvent::to_json).collect()));
        put("suggested_trim_start", json!(round3(suggestion.start)));
        put("suggested_trim_end", json!(round3(suggestion.end)));
        put("smart_trim_reason", json!(suggestion.reason));
        put("video_buffer_start_wall", json!(selected_start));
        put("video_buffer_end_wall", json!(selected_end));
        put("highlight_score", json!(request.highlight_score));
        put("score_reasons", json!(request.score_reasons));
        put("victim_names", json!(request.victim_names));
        put("victim_champions", json!(request.victim_champions));
        put("assister_names", json!(request.assister_names));
        put("event_started_at", json!(request.event_started_at));
        put("event_ended_at", json!(request.event_ended_at));
        put("clip_window_start_wall", json!(window_start));
        put("clip_window_end_wall", json!(window_end));
        put("is_match_reel", json!(false));
        put("rating", json!(""));
        // Deaths are for learning, not for the review queue.
        put("reviewed", json!(request.event_kind == "death"));
        put("requested_duration_seconds", json!(requested));
        put("selected_buffer_seconds", json!(round3(selected_end - selected_start)));
        put("trim_start_seconds", json!(round3(trim_start)));
        put("precise_event_window", json!(precise.is_some()));
        put("audio_included", json!(audio.0 || audio.1));
        put("system_audio_included", json!(audio.0));
        put("microphone_included", json!(audio.1));
        let mut sources = Vec::new();
        if audio.0 {
            sources.push("system");
        }
        if audio.1 {
            sources.push("microphone");
        }
        put("audio_sources", json!(sources));
        put("system_audio_device", json!(self.audio.device_name(crate::audio::Kind::System)));
        put("microphone_device", json!(self.audio.device_name(crate::audio::Kind::Microphone)));
        put("system_audio_volume", json!(s.system_audio_volume));
        put("microphone_volume", json!(s.microphone_volume));
        put("size_bytes", json!(size));
        put("discord_ready", json!(size < 10_000_000));
        put("quality_mode", json!("original_recording"));
        put("thumb_v", json!(THUMB_VERSION));
        put("favorite", json!(false));
        put("auto_trimmed", json!(auto_trimmed));
        write_meta(&output.with_extension("json"), &meta).map_err(|e| e.to_string())?;
        log::info!("Saved clip to {} ({size} bytes, {duration:.1}s)", output.display());
        Ok(output)
    }

    /// Returns (system audio included, microphone included).
    #[allow(clippy::too_many_arguments)]
    fn mux(
        &self,
        s: &crate::config::Settings,
        work: &Path,
        selected: &[TimedSegment],
        start: f64,
        end: f64,
        partial: &Path,
        thumb: &Path,
        thumb_from: &Path,
    ) -> Result<(bool, bool), String> {
        let list = work.join("segments.txt");
        let text: String = selected
            .iter()
            .map(|seg| format!("file '{}'\n", concat_escape(&seg.path)))
            .collect();
        fs::write(&list, text).map_err(|e| e.to_string())?;

        let audio = self.audio.export(start, end, work);
        let mut inputs: Vec<(PathBuf, f64, String)> = Vec::new();
        if let Some(p) = &audio.system {
            inputs.push((p.clone(), f64::from(s.system_audio_volume) / 100.0, String::new()));
        }
        if let Some(p) = &audio.microphone {
            inputs.push((p.clone(), f64::from(s.microphone_volume) / 100.0, mic_filter(s, start, end)));
        }
        let flags = (audio.system.is_some(), audio.microphone.is_some());

        match self.ff.run(&mux_args(s, &list, &inputs, partial, thumb, thumb_from), Duration::from_secs(120)) {
            Ok(()) => Ok(flags),
            Err(error) if !inputs.is_empty() => {
                log::warn!("Could not attach audio ({}); saving a video-only clip", last_line(&error));
                self.ff
                    .run(&mux_args(s, &list, &[], partial, thumb, thumb_from), Duration::from_secs(90))
                    .map(|_| (false, false))
            }
            Err(error) => Err(format!("Could not save the clip: {}", last_line(&error))),
        }
    }
}

/// The contiguous run of segments that overlaps [from, to] on the wall clock.
fn keep_window(selected: &[TimedSegment], from: f64, to: f64) -> Vec<TimedSegment> {
    selected
        .iter()
        .filter(|seg| seg.end_wall > from + 0.01 && seg.start_wall < to - 0.01)
        .cloned()
        .collect()
}

/// The segment whose first frame is nearest to wall-clock time `at`. Every
/// segment starts on a keyframe, so its first frame decodes on its own.
fn thumb_source(selected: &[TimedSegment], at: f64) -> PathBuf {
    selected
        .iter()
        .min_by(|a, b| (a.start_wall - at).abs().total_cmp(&(b.start_wall - at).abs()))
        .unwrap_or(&selected[0])
        .path
        .clone()
}

/// The Smart Trim range to apply automatically, if it is worth it: it must
/// cut at least 4 s and leave at least 8 s.
pub fn auto_trim_window(duration: f64, start: f64, end: f64) -> Option<(f64, f64)> {
    let start = start.max(0.0);
    let end = end.min(duration);
    (end - start >= 8.0 && duration - (end - start) >= 4.0).then_some((start, end))
}

/// Thumbnails taken at the play; older clips are redone in the background.
pub const THUMB_VERSION: u64 = 2;

/// Half a second before the play, kept inside the clip.
pub fn thumb_time(moment: f64, duration: f64) -> f64 {
    if duration <= 0.0 {
        return 0.0;
    }
    (moment - 0.5).clamp(0.0, (duration - 0.5).max(0.0))
}

/// Extra mic processing: rumble cut and noise gate, and push to talk.
fn mic_filter(s: &crate::config::Settings, start: f64, end: f64) -> String {
    let mut parts = Vec::new();
    if s.mic_noise_gate {
        // Below about -36 dB the mic is closed; speech opens it in 5 ms and
        // it closes 300 ms after you stop, so words are never clipped.
        parts.push("highpass=f=80,agate=threshold=0.016:ratio=8:range=0.03:attack=5:release=300".to_string());
    }
    if s.mic_push_to_talk && crate::ptt::active() {
        parts.push(crate::ptt::mute_filter(&crate::ptt::intervals(start, end)));
    }
    parts.join(",")
}

fn mux_args(
    s: &crate::config::Settings,
    list: &Path,
    audio: &[(PathBuf, f64, String)],
    partial: &Path,
    thumb: &Path,
    thumb_from: &Path,
) -> Vec<String> {
    let mut a: Vec<String> = ["-hide_banner", "-loglevel", "error", "-y", "-fflags", "+genpts", "-f", "concat", "-safe", "0", "-i"]
        .iter()
        .map(|x| x.to_string())
        .collect();
    a.push(list.to_string_lossy().into_owned());
    for (path, _, _) in audio {
        a.push("-i".into());
        a.push(path.to_string_lossy().into_owned());
    }
    // Last input: the single segment the preview frame comes from. One
    // decoder thread: only its first frame is ever decoded.
    let thumb_input = audio.len() + 1;
    a.extend(["-threads".to_string(), "1".to_string(), "-i".to_string(), thumb_from.to_string_lossy().into_owned()]);
    a.extend(["-map", "0:v:0", "-c:v", "copy"].iter().map(|x| x.to_string()));
    // Normalise each source to 48 kHz stereo; mix only at export so the
    // video stream is never re-encoded by audio settings.
    let common = "aresample=48000:async=1000:first_pts=0,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo";
    match audio.len() {
        0 => a.push("-an".into()),
        1 => {
            let gain = audio[0].1;
            let extra = if audio[0].2.is_empty() { String::new() } else { format!("{},", audio[0].2) };
            let mut filter = format!("{common},{extra}volume={gain:.3}");
            if gain > 1.0 {
                filter.push_str(",alimiter=limit=0.98");
            }
            a.extend(["-map".into(), "1:a:0".into(), "-af".into(), filter]);
        }
        _ => {
            let mut parts = Vec::new();
            let mut labels = String::new();
            for (index, (_, gain, extra)) in audio.iter().enumerate() {
                let extra = if extra.is_empty() { String::new() } else { format!("{extra},") };
                parts.push(format!("[{}:a]{common},{extra}volume={gain:.3}[a{}]", index + 1, index + 1));
                labels.push_str(&format!("[a{}]", index + 1));
            }
            parts.push(format!(
                "{labels}amix=inputs={}:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.94[aout]",
                audio.len()
            ));
            a.extend(["-filter_complex".into(), parts.join(";"), "-map".into(), "[aout]".into()]);
        }
    }
    if !audio.is_empty() {
        a.extend(
            ["-c:a", "aac", "-b:a", &format!("{}k", s.audio_bitrate_kbps), "-ar", "48000", "-ac", "2"]
                .iter()
                .map(|x| x.to_string()),
        );
    }
    a.extend(
        ["-avoid_negative_ts", "make_zero", "-movflags", "+faststart", "-f", "mp4"]
            .iter()
            .map(|x| x.to_string()),
    );
    a.push(partial.to_string_lossy().into_owned());
    // Second output: thumbnail from the same run.
    a.extend(
        [
            "-map".into(),
            format!("{thumb_input}:v:0"),
            "-frames:v".into(),
            "1".into(),
            "-vf".into(),
            "scale=480:-2".into(),
            "-q:v".into(),
            "3".into(),
            "-f".into(),
            "image2".into(),
            thumb.to_string_lossy().into_owned(),
        ],
    );
    a
}

fn select_precise(timeline: &[TimedSegment], desired_start: f64, desired_end: f64) -> Result<(Vec<TimedSegment>, f64, f64, f64), String> {
    let available_start = timeline[0].start_wall;
    let available_end = timeline[timeline.len() - 1].end_wall;
    let start = desired_start.max(available_start);
    let end = desired_end.min(available_end);
    if end <= start {
        return Err("The event is no longer inside the rolling buffer.".into());
    }
    if start > desired_start + 0.2 {
        log::warn!("The beginning of the event clip was limited by the current buffer");
    }
    if end < desired_end - 0.2 {
        log::warn!("The end of the event clip was limited by available footage");
    }
    let selected: Vec<TimedSegment> = timeline
        .iter()
        .filter(|seg| seg.end_wall > start && seg.start_wall < end)
        .cloned()
        .collect();
    if selected.is_empty() {
        return Err("Could not locate the event inside the rolling buffer.".into());
    }
    let trim_start = (start - selected[0].start_wall).max(0.0);
    Ok((selected, trim_start, end - start, desired_end - desired_start))
}

fn select_tail(timeline: &[TimedSegment], requested: f64) -> (Vec<TimedSegment>, f64, f64, f64) {
    let count = ((requested / f64::from(SEGMENT_SECONDS)).ceil() as usize).max(1);
    let selected: Vec<TimedSegment> = timeline[timeline.len().saturating_sub(count)..].to_vec();
    let total = selected[selected.len() - 1].end_wall - selected[0].start_wall;
    let trim_duration = requested.min(total);
    ((selected), (total - trim_duration).max(0.0), trim_duration, requested)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn timeline() -> Vec<TimedSegment> {
        (0..20)
            .map(|i| TimedSegment {
                path: PathBuf::from(format!("s{i}.mkv")),
                start_wall: 100.0 + f64::from(i) * 2.0,
                end_wall: 102.0 + f64::from(i) * 2.0,
            })
            .collect()
    }

    #[test]
    fn precise_selection_covers_window() {
        let (selected, trim_start, trim, _) = select_precise(&timeline(), 111.0, 125.0).unwrap();
        assert_eq!(selected.first().unwrap().start_wall, 110.0);
        assert_eq!(selected.last().unwrap().end_wall, 126.0);
        assert_eq!(trim_start, 1.0);
        assert_eq!(trim, 14.0);
        assert!(select_precise(&timeline(), 10.0, 20.0).is_err());
    }

    #[test]
    fn tail_selection_takes_latest_segments() {
        let (selected, _, trim, _) = select_tail(&timeline(), 45.0);
        assert_eq!(selected.len(), 20);
        assert_eq!(trim, 40.0);
        let (selected, _, _, _) = select_tail(&timeline(), 12.0);
        assert_eq!(selected.len(), 6);
    }

    #[test]
    fn trim_keeps_whole_segments_and_preview_reads_one() {
        let seg = |i: usize| TimedSegment {
            path: PathBuf::from(format!("segment_{i:06}.mkv")),
            start_wall: 100.0 + 2.0 * i as f64,
            end_wall: 102.0 + 2.0 * i as f64,
        };
        let selected: Vec<TimedSegment> = (0..10).map(seg).collect(); // 100..120
        // Keep 105..113: segments 2 (104-106) to 6 (112-114).
        let kept = keep_window(&selected, 105.0, 113.0);
        assert_eq!(kept.len(), 5);
        assert_eq!((kept[0].start_wall, kept[4].end_wall), (104.0, 114.0));
        // A range that ends exactly on a boundary doesn't pull in the next one.
        assert_eq!(keep_window(&selected, 104.0, 106.0).len(), 1);
        // A play at 108.7 is nearest to the start of segment 4 (108); at
        // 109.3 the start of segment 5 (110) is nearer.
        assert_eq!(thumb_source(&selected, 108.7), PathBuf::from("segment_000004.mkv"));
        assert_eq!(thumb_source(&selected, 109.3), PathBuf::from("segment_000005.mkv"));
        // Before the start or past the end: the first or last segment.
        assert_eq!(thumb_source(&selected, 0.0), PathBuf::from("segment_000000.mkv"));
        assert_eq!(thumb_source(&selected, 500.0), PathBuf::from("segment_000009.mkv"));

        let settings = crate::config::Settings::default();
        let args = mux_args(&settings, Path::new("list.txt"), &[], Path::new("out.mp4"), Path::new("out.jpg"), Path::new("seg.mkv")).join(" ");
        assert!(args.contains("-i list.txt -threads 1 -i seg.mkv -map 0:v:0 -c:v copy"), "{args}");
        assert!(args.contains("-map 1:v:0 -frames:v 1"), "{args}");
    }

    #[test]
    fn automatic_trim_only_when_it_helps() {
        assert_eq!(auto_trim_window(38.0, 12.0, 30.0), Some((12.0, 30.0)));
        assert_eq!(auto_trim_window(16.0, 2.0, 15.0), None, "cuts only 3 s");
        assert_eq!(auto_trim_window(20.0, 5.0, 11.0), None, "would leave under 8 s");
        assert_eq!(auto_trim_window(30.0, -1.0, 40.0), None);
    }

    #[test]
    fn thumbnail_sits_just_before_the_play() {
        assert_eq!(thumb_time(12.0, 30.0), 11.5);
        assert_eq!(thumb_time(0.2, 30.0), 0.0);
        assert_eq!(thumb_time(40.0, 30.0), 29.5);
        assert_eq!(thumb_time(5.0, 0.0), 0.0);
    }

    #[test]
    fn concat_paths_are_escaped() {
        assert_eq!(concat_escape(Path::new("C:\\a\\it's.mkv")), "C:/a/it'\\''s.mkv");
    }
}
